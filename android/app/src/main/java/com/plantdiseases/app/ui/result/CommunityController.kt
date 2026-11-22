package com.plantdiseases.app.ui.result

import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.account.QuickSignIn
import com.plantdiseases.app.ui.community.CommunityJoin
import com.plantdiseases.app.ui.community.lockNearbyPreview
import com.plantdiseases.app.ui.community.forum.ComposePostActivity
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.SimilarScans
import com.plantdiseases.app.util.onConnectivityChanged
import kotlinx.coroutines.launch

/**
 * Owns every community-facing surface on the result screen: the similar-scans
 * carousel (country/global scope resolved by the server; scans from the user's
 * own area once they join the community) and the "Ask the community" button,
 * which posts the scan to the forum.
 */
class CommunityController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
    private val repository: ScanRepository,
    private val authRepository: AuthRepository,
    private val connectivity: ConnectivityObserver,
    private val communityJoin: CommunityJoin,
    private val quickSignIn: QuickSignIn,
) {

    /** The one writer of this button's enabled state: off while there is no server. */
    private fun applyAskEnabled() {
        binding.btnAskCommunity.isEnabled = connectivity.lastKnownOnline
    }

    private fun displayName(key: String, isRu: Boolean): String =
        DiseaseLabels.displayName(key, isRu) ?: key

    /** Fire-and-forget: record that the signed account opened someone else's
     *  shared scan. Guests return before the repository is called; the server
     *  deduplicates per actor and ignores owner self-views. */
    private fun recordView(scanId: String) {
        if (scanId.isBlank() || authRepository.state.value !is AuthState.Authenticated) return
        activity.lifecycleScope.launch { repository.recordCommunityView(scanId) }
    }

    /** Offers sign-in in place and deliberately drops the initiating mutation. */
    private fun requireAuthenticatedAction(): Boolean = quickSignIn.require()

    /** "Ask the community": posts the analysed photo and the AI verdict to the
     *  forum (guests sign in first). Hidden for an abstained scan. */
    fun setupAskCommunity(scan: ScanEntity) {
        val btn = binding.btnAskCommunity
        if (repository.isAbstainedScan(scan)) {
            btn.visibility = View.GONE
            return
        }
        // This control is not in the activity's enableWhileOnline set, so it
        // observes reachability itself.
        activity.onConnectivityChanged(connectivity.isOnline) { applyAskEnabled() }
        applyAskEnabled()
        btn.setText(R.string.result_ask_community)
        btn.setOnClickListener {
            if (!requireAuthenticatedAction()) return@setOnClickListener
            activity.startActivity(ComposePostActivity.intentForScan(activity, scan.id))
        }
    }

    fun bindSimilarScans(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val card = binding.cardSimilarScans
        val rv = binding.rvSimilarScans
        val empty = binding.tvSimilarEmpty
        // Default state: hidden until we know what to show. Members get the
        // nearby strip; everyone else a blurred preview under the join button.
        card.visibility = View.GONE
        binding.similarStrip.visibility = View.GONE
        empty.visibility = View.GONE
        if (repository.isAbstainedScan(scan)) return

        // Accepted scans use their probability-backed diagnostic class. An
        // abstained diagnostic argmax returns above and never scopes a feed.
        val diseaseKey = repository.verdictClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')
        val adapter = SimilarScansAdapter(
            displayName = { key -> displayName(key, isRu) },
            onAuthorClick = { item ->
                item.authorToken?.let { token ->
                    activity.startActivity(
                        com.plantdiseases.app.ui.community.CommunityProfileActivity
                            .intent(activity, token),
                    )
                }
            },
            onClick = { item ->
                recordView(item.scanId)
                activity.lifecycleScope.launch {
                    val mine = item.scanId in repository.localClientScanIds()
                    // localClientScanIds() suspends on Dispatchers.IO, so we
                    // always resume a main-loop turn later. lifecycleScope only
                    // cancels at DESTROYED, so a Home press mid-read would land
                    // here after onSaveInstanceState and blow up inside show().
                    // Guests get the same sheet with its like locked.
                    if (activity.supportFragmentManager.isStateSaved) return@launch
                    CommunityScanSheet.show(
                        activity.supportFragmentManager,
                        item,
                        displayName(item.disease, isRu),
                        mine = mine,
                    )
                }
            },
        )
        rv.adapter = adapter
        rv.layoutManager = LinearLayoutManager(activity, LinearLayoutManager.HORIZONTAL, false)

        // Public community reads remain available to guests and non-members
        // (country/global scope). Scans from the user's own area need location,
        // which comes with joining the community, so that is an explicit tap.
        val render = { fetchAndRender(adapter, diseaseKey, scan.speciesScientific, isRu) }
        val join = View.OnClickListener {
            communityJoin.ensureMember { joined -> if (joined) render() }
        }
        binding.btnSimilarJoin.setOnClickListener(join)
        binding.similarLockOverlay.setOnClickListener(join)
        render()
    }

    /**
     * Fetch + render the similar-scans carousel: other people's
     * scans of the SAME problem on the SAME species where known. There is no
     * disease switcher — the strip is "scans similar to this one". Similarity
     * is enforced by [SimilarScans.filterSimilar].
     */
    private fun fetchAndRender(
        adapter: SimilarScansAdapter,
        disease: String,
        speciesScientific: String?,
        isRu: Boolean,
    ) {
        val card = binding.cardSimilarScans
        val strip = binding.similarStrip
        val empty = binding.tvSimilarEmpty
        val member = CommunityJoin.isMember(activity)
        setPreviewLocked(!member)
        activity.lifecycleScope.launch {
            // Members send their coarse location for the nearby scope. Without
            // it the server widens to country/global scope, so the carousel
            // still fills.
            val region = if (member) {
                runCatching { RegionResolver.resolveRegion(activity) }.getOrNull()
            } else {
                null
            }
            repository.communityNearby(
                country = region?.country,
                region = region?.locality,
                lat = region?.lat,
                lon = region?.lon,
                disease = disease,
                // Narrow by detected scientific name so "Powdery mildew on
                // Monstera" doesn't render Ficus thumbnails. Server falls back
                // gracefully when the species bucket is empty.
                species = speciesScientific,
                limit = 12,
            ).onSuccess { response ->
                card.visibility = View.VISIBLE
                val label = if (isRu) {
                    response.countryNameRu ?: response.summary?.countryNameRu
                } else {
                    response.countryNameEn ?: response.summary?.countryNameEn
                } ?: activity.getString(R.string.community_scope_global)
                // Restrict to genuinely similar scans client-side too, so a
                // cached response from an older deployment can't leak unrelated
                // verdicts/species into a "similar to this" strip. Order stays
                // the server's newest-first.
                val items = SimilarScans.filterSimilar(response.items, disease, speciesScientific)

                if (items.isEmpty()) {
                    binding.tvSimilarSubtitle.text =
                        activity.getString(R.string.similar_scans_subtitle_region, label)
                    empty.text = activity.getString(R.string.similar_scans_empty)
                    empty.visibility = View.VISIBLE
                    strip.visibility = View.GONE
                } else {
                    // Count what the strip actually SHOWS, not the regional
                    // all-disease total.
                    binding.tvSimilarSubtitle.text = activity.resources.getQuantityString(
                        R.plurals.similar_scans_subtitle_count,
                        items.size,
                        items.size,
                        label,
                    )
                    strip.visibility = View.VISIBLE
                    empty.visibility = View.GONE
                    adapter.submit(items)
                }
            }.onFailure {
                // Stay hidden on error — never surface a broken card.
                card.visibility = View.GONE
            }
        }
    }

    private fun setPreviewLocked(locked: Boolean) = lockNearbyPreview(
        strip = binding.rvSimilarScans,
        overlay = binding.similarLockOverlay,
        joinButton = binding.btnSimilarJoin,
        locked = locked,
    )
}
