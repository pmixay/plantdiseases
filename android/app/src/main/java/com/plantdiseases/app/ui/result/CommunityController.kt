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
 * own area once they join the community), the second-opinion ask/wait/view
 * control, and the activity readout on the user's own shared scan.
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

    // Remembered so onResume() can re-probe the request/reply state after
    // the user comes back from MyScanRepliesActivity (e.g. having tapped
    // Stop) — that screen is a separate Activity now, not a bottom sheet
    // over this one, so there's no Fragment Result callback to catch.
    private var lastScan: ScanEntity? = null

    fun onResume() {
        val scan = lastScan ?: return
        refreshSecondOpinion(scan)
    }

    /** The one writer of this button's enabled state: off while there is no server. */
    private fun applySecondOpinionEnabled() {
        binding.btnSecondOpinion.isEnabled = connectivity.lastKnownOnline
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

    /**
     * Wire the ONE second-opinion control (the separate "view opinions"
     * button was folded into it — two buttons that both opened the replies
     * sheet were redundant). Three states, probed from the server on load:
     *
     * - **Ask**: no live request → "Get a second opinion" (community-gated).
     * - **Open request**: dynamic label with the reply count and the hours
     *   left until the 72 h auto-expiry, gently pulsing while it waits.
     *   Tapping opens [com.plantdiseases.app.ui.community.MyScanRepliesActivity],
     *   which carries the "Stop request" action — reachable at ANY point of
     *   the request's life, including after replies have arrived.
     * - **Replies, request closed** (expired/stopped): "View second
     *   opinions (N)".
     *
     * That screen is a separate Activity, not a bottom sheet over this one,
     * so there's no result callback on return — [onResume] re-probes instead.
     */
    fun setupSecondOpinion(scan: ScanEntity) {
        if (repository.isAbstainedScan(scan)) {
            binding.btnSecondOpinion.visibility = View.GONE
            lastScan = null
            return
        }
        lastScan = scan
        // This control is not in the activity's enableWhileOnline set, so it
        // observes reachability itself.
        activity.onConnectivityChanged(connectivity.isOnline) { applySecondOpinionEnabled() }
        showAsk(scan)
        refreshSecondOpinion(scan)
    }

    private fun refreshSecondOpinion(scan: ScanEntity) {
        activity.lifecycleScope.launch {
            repository.getCommunityOpinions(scan.clientScanId).onSuccess { resp ->
                val n = resp.opinions.size
                when {
                    resp.wantsOpinion ->
                        showOpenRequest(scan, n, resp.opinionRequestedAt)
                    n > 0 -> showViewReplies(scan, n)
                    else -> showAsk(scan)
                }
            }
        }
    }

    private fun openReplies(scan: ScanEntity) {
        activity.startActivity(
            com.plantdiseases.app.ui.community.MyScanRepliesActivity.intentFor(activity, scan.clientScanId),
        )
    }

    /** Hours until the 72 h expiry, floored at 1 so it never reads "0 h
     *  left" while still live; null when the timestamp is unknown. */
    private fun hoursLeft(requestedAtSec: Long?): Int? {
        if (requestedAtSec == null || requestedAtSec <= 0L) return null
        val expiresMs = (requestedAtSec + 72L * 3600L) * 1000L
        val left = expiresMs - System.currentTimeMillis()
        if (left <= 0L) return null
        return ((left + 3_599_999L) / 3_600_000L).toInt().coerceIn(1, 72)
    }

    /** Live request: dynamic status label + a gentle pulse so the control
     *  reads as "in progress", not a dead button. Tap opens the replies
     *  sheet (which carries the Stop action). */
    private fun showOpenRequest(
        scan: ScanEntity,
        replyCount: Int,
        requestedAtSec: Long?,
    ) {
        val btn = binding.btnSecondOpinion
        applySecondOpinionEnabled()
        val hours = hoursLeft(requestedAtSec)
        btn.text = when {
            replyCount > 0 && hours != null ->
                activity.getString(R.string.second_opinion_open_replies, replyCount, hours)
            replyCount > 0 ->
                activity.getString(R.string.result_second_opinion_view, replyCount)
            hours != null ->
                activity.getString(R.string.second_opinion_waiting_hours, hours)
            else -> activity.getString(R.string.result_second_opinion_waiting)
        }
        // Soft breathing pulse — a view animation, so it dies with the view.
        val pulse = android.view.animation.AlphaAnimation(1f, 0.55f).apply {
            duration = 900L
            repeatMode = android.view.animation.Animation.REVERSE
            repeatCount = android.view.animation.Animation.INFINITE
        }
        btn.startAnimation(pulse)
        btn.setOnClickListener { openReplies(scan) }
    }

    private fun showViewReplies(scan: ScanEntity, count: Int) {
        val btn = binding.btnSecondOpinion
        btn.clearAnimation()
        applySecondOpinionEnabled()
        btn.text = activity.getString(R.string.result_second_opinion_view, count)
        btn.setOnClickListener { openReplies(scan) }
    }

    /** Ask state: new questions go to the community forum with the analysed
     *  photo and the AI verdict attached. Requests opened before the forum
     *  existed keep their waiting / replies states above. */
    private fun showAsk(scan: ScanEntity) {
        val btn = binding.btnSecondOpinion
        btn.clearAnimation()
        applySecondOpinionEnabled()
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
                    val mine = repository.localClientScanIds()
                    if (item.scanId in mine) {
                        activity.startActivity(
                            com.plantdiseases.app.ui.community.MyScanRepliesActivity
                                .intentFor(activity, item.scanId),
                        )
                    } else {
                        // localClientScanIds() suspends on Dispatchers.IO, so we
                        // always resume a main-loop turn later. lifecycleScope
                        // only cancels at DESTROYED, so a Home press mid-read
                        // would land here after onSaveInstanceState and blow up
                        // inside show()'s commit(). Guests get the same sheet
                        // with its like and vote controls locked.
                        if (activity.supportFragmentManager.isStateSaved) return@launch
                        CommunityOpinionSheet.show(
                            activity.supportFragmentManager,
                            item,
                            displayName(item.disease, isRu),
                        )
                    }
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
