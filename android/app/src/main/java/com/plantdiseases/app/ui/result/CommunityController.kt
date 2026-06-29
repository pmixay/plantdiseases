package com.plantdiseases.app.ui.result

import android.graphics.Rect
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.SimilarScans
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Owns every community-facing surface on the result screen: the similar-scans
 * carousel (country/global scope resolved by the server), the second-opinion
 * ask/wait/view control, and the activity readout on the user's own shared
 * scan. Community upload happens here too: asking for a second opinion forces
 * the scan into the shared corpus.
 *
 * The notification permission launcher stays on the activity; this controller
 * drives it through [ensureNotificationPermission].
 */
class CommunityController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
    private val ensureNotificationPermission: () -> Unit,
) : KoinComponent {
    private val repository: ScanRepository by inject()
    private val communityUploader: CommunityUploader by inject()

    private fun displayName(key: String, isRu: Boolean): String =
        DiseaseLabels.displayName(key, isRu) ?: key

    /** Fire-and-forget: record that this install opened someone else's shared
     *  scan. The repository swallows failures and the server dedups per
     *  viewer + ignores owner self-views, so this is safe to call on any tap. */
    private fun recordView(scanId: String, viewerPseudoId: String) {
        if (scanId.isBlank() || viewerPseudoId.isBlank()) return
        activity.lifecycleScope.launch { repository.recordCommunityView(scanId, viewerPseudoId) }
    }

    /**
     * Community opinions others left on the user's OWN scan. Loaded lazily; the
     * button appears only if there is activity (opinions and/or views), and
     * jumps straight to the opinions sheet when arriving from a notification.
     */
    fun bindOwnScanOpinions(scan: ScanEntity, isRu: Boolean) {
        binding.btnViewOpinions.visibility = View.GONE
        val ownScanId = scan.clientScanId
        if (ownScanId.isBlank()) return
        activity.lifecycleScope.launch {
            repository.getCommunityOpinions(ownScanId).onSuccess { resp ->
                val n = resp.opinions.size
                val v = resp.views
                // Opening the scan's result counts as viewing its activity:
                // advance the viewed baseline so the Gallery clears its
                // "+N new opinions" badge for this scan.
                OpinionPrefs.markViewedOpinions(activity, ownScanId, n)
                // Show activity on the user's OWN shared scan whenever there is
                // any opinions and/or views. The view count is deduped per
                // viewer server-side.
                if (n > 0 || v > 0) {
                    binding.btnViewOpinions.text = if (v > 0) {
                        activity.getString(R.string.result_view_activity, n, v)
                    } else {
                        activity.getString(R.string.result_view_opinions, n)
                    }
                    binding.btnViewOpinions.visibility = View.VISIBLE
                    val opinionName = displayName(repository.topClassKey(scan) ?: "", isRu)
                    binding.btnViewOpinions.setOnClickListener {
                        // Owner viewing their OWN scan: read-only.
                        CommunityOpinionSheet.showForOwner(
                            activity.supportFragmentManager,
                            ownScanId,
                            opinionName,
                        )
                    }
                    // Arrived from a notification / a Gallery row with new
                    // opinions → jump straight to them.
                    if (n > 0 && activity.intent.getBooleanExtra(ResultActivity.EXTRA_OPEN_OPINIONS, false)) {
                        activity.intent.removeExtra(ResultActivity.EXTRA_OPEN_OPINIONS)
                        binding.scrollView.post {
                            binding.btnViewOpinions.requestRectangleOnScreen(
                                Rect(
                                    0,
                                    0,
                                    binding.btnViewOpinions.width,
                                    binding.btnViewOpinions.height,
                                ),
                                false,
                            )
                            CommunityOpinionSheet.showForOwner(
                                activity.supportFragmentManager,
                                ownScanId,
                                opinionName,
                            )
                        }
                    }
                }
            }
        }
    }

    /**
     * Wire the "second opinion" control. Two modes: **Ask** (the scanner shares
     * this scan and durably flags it for a second opinion — the tap is consent,
     * so it works even with global sharing off) and **View** (once replies
     * exist, switch to the read-only opinions sheet). We probe for existing
     * opinions on load so a returning user lands straight in the view state.
     */
    fun setupSecondOpinion(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val btn = binding.btnSecondOpinion
        val name = displayName(repository.verdictClassKey(scan) ?: "", isRu)

        fun openReplies() {
            CommunityOpinionSheet.showForOwner(
                activity.supportFragmentManager,
                scan.clientScanId,
                name,
            )
        }
        fun showViewReplies(count: Int) {
            btn.isEnabled = true
            btn.text = activity.getString(R.string.result_second_opinion_view, count)
            btn.setOnClickListener { openReplies() }
        }
        fun showWaiting() {
            // The request is durably recorded server-side, so "waiting" always
            // means the community can see the ask. Tappable to check for replies.
            btn.isEnabled = true
            btn.setText(R.string.result_second_opinion_waiting)
            btn.setOnClickListener { openReplies() }
        }

        fun showAsk() {
            btn.isEnabled = true
            btn.setText(R.string.result_get_second_opinion)
            btn.setOnClickListener {
                btn.isEnabled = false
                btn.setText(R.string.result_second_opinion_waiting)
                ensureNotificationPermission()
                val pseudoId = TelemetryIds.userPseudoId(activity)
                activity.lifecycleScope.launch {
                    // Ensure the scan is in the community corpus so others can
                    // see the ask (force = explicit consent).
                    communityUploader.uploadFromEntity(scan, force = true)
                    repository.requestSecondOpinion(scan.clientScanId, pseudoId)
                        .onSuccess {
                            showWaiting()
                            // Seed the notify baseline at 0 so the very next
                            // reply counts as new, and flip the global init flag.
                            OpinionPrefs.markInitialized(activity)
                            OpinionPrefs.setSeen(activity, scan.clientScanId, 0)
                        }
                        .onFailure {
                            showAsk()
                            Toast.makeText(
                                activity,
                                R.string.result_second_opinion_failed,
                                Toast.LENGTH_SHORT,
                            ).show()
                        }
                }
            }
        }

        showAsk()
        // Upgrade to "view replies" if the community already responded.
        activity.lifecycleScope.launch {
            repository.getCommunityOpinions(scan.clientScanId).onSuccess { resp ->
                if (resp.opinions.isNotEmpty()) showViewReplies(resp.opinions.size)
            }
        }
    }

    fun bindSimilarScans(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val card = binding.cardSimilarScans
        val rv = binding.rvSimilarScans
        val empty = binding.tvSimilarEmpty
        // Default state: hidden until we know what to show. When community is
        // OFF we render only the in-context opt-in (below) and fetch NO scans;
        // when ON we fetch and show the read-only similar-scans strip.
        card.visibility = View.GONE
        rv.visibility = View.GONE
        empty.visibility = View.GONE
        binding.btnEnableSimilar.visibility = View.GONE

        // Scope the carousel by the verdict the user actually SAW — the
        // server's reroute-aware displayed class — not the raw argmax.
        val diseaseKey = repository.verdictClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')
        val pseudoId = TelemetryIds.userPseudoId(activity)
        val adapter = SimilarScansAdapter(
            displayName = { key -> displayName(key, isRu) },
            onClick = { item ->
                // Read-only browse only — the strip itself is gated behind the
                // community opt-in (handled below), and leaving a second opinion
                // never happens from here.
                recordView(item.scanId, pseudoId)
                CommunityOpinionSheet.showReadOnly(
                    activity.supportFragmentManager,
                    item,
                    displayName(item.disease, isRu),
                )
            },
        )
        rv.adapter = adapter
        rv.layoutManager = LinearLayoutManager(activity, LinearLayoutManager.HORIZONTAL, false)

        // Community OFF: show the card with ONLY the in-context opt-in — no
        // scans at all (not even the read-only strip) until the user enables.
        // Tapping opts in, then loads the feed.
        if (!CommunityPrefs.uploadsEnabled(activity)) {
            card.visibility = View.VISIBLE
            rv.visibility = View.GONE
            empty.visibility = View.GONE
            binding.tvSimilarSubtitle.text = activity.getString(R.string.community_enable_blurb)
            binding.btnEnableSimilar.visibility = View.VISIBLE
            binding.btnEnableSimilar.setOnClickListener {
                CommunityPrefs.enable(activity)
                ensureNotificationPermission()
                binding.btnEnableSimilar.visibility = View.GONE
                fetchAndRender(adapter, diseaseKey, scan.speciesScientific, isRu)
            }
            return
        }

        fetchAndRender(adapter, diseaseKey, scan.speciesScientific, isRu)
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
        val rv = binding.rvSimilarScans
        val empty = binding.tvSimilarEmpty
        activity.lifecycleScope.launch {
            // Best-effort coarse location for the nearby scope; if it's
            // unavailable (e.g. the user never granted location), the server
            // widens to country/global scope, so the carousel still fills.
            val region = runCatching { RegionResolver.resolveRegion(activity) }.getOrNull()
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
                userPseudoId = TelemetryIds.userPseudoId(activity),
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
                    rv.visibility = View.GONE
                } else {
                    // Count what the strip actually SHOWS, not the regional
                    // all-disease total.
                    binding.tvSimilarSubtitle.text = activity.resources.getQuantityString(
                        R.plurals.similar_scans_subtitle_count,
                        items.size,
                        items.size,
                        label,
                    )
                    rv.visibility = View.VISIBLE
                    empty.visibility = View.GONE
                    adapter.submit(items)
                }
            }.onFailure {
                // Stay hidden on error — never surface a broken card.
                card.visibility = View.GONE
            }
        }
    }
}
