package com.plantdiseases.app.ui.result

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
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.SimilarScans
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.util.onConnectivityChanged
import kotlinx.coroutines.launch

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
    private val repository: ScanRepository,
    private val communityUploader: CommunityUploader,
    private val connectivity: ConnectivityObserver,
    private val ensureNotificationPermission: () -> Unit,
    // Requests coarse location (+ notifications) and reports back whether
    // location was granted. Owned by the activity because permission launchers
    // must be registered before the activity starts. See ResultActivity.
    private val requestCommunityOptIn: (onResult: (Boolean) -> Unit) -> Unit,
) {

    /**
     * True from the moment an ask is sent until the server answers. The
     * control must stay disabled across a STOPPED→STARTED cycle in that
     * window: the button still reads "waiting…" but keeps `showAsk`'s
     * listener, so re-enabling it fires a *second* request — a duplicate
     * community upload and a duplicate ask.
     */
    private var opinionRequestInFlight = false

    // Remembered so onResume() can re-probe the request/reply state after
    // the user comes back from MyScanRepliesActivity (e.g. having tapped
    // Stop) — that screen is a separate Activity now, not a bottom sheet
    // over this one, so there's no Fragment Result callback to catch.
    private var lastScan: ScanEntity? = null
    private var lastIsRu: Boolean = false

    fun onResume() {
        val scan = lastScan ?: return
        refreshSecondOpinion(scan, lastIsRu)
    }

    /**
     * The one writer of this button's enabled state.
     *
     * It has two independent reasons to be off — no server, or an ask already
     * in flight — and they used to be owned by different code that overwrote
     * each other in whichever order ran last. Everything that renders the
     * control goes through here.
     */
    private fun applySecondOpinionEnabled() {
        binding.btnSecondOpinion.isEnabled = connectivity.lastKnownOnline && !opinionRequestInFlight
    }

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
    fun setupSecondOpinion(scan: ScanEntity, isRu: Boolean) {
        lastScan = scan
        lastIsRu = isRu
        // This control is not in the activity's enableWhileOnline set — it
        // needs the in-flight rule too, so it observes reachability itself.
        activity.onConnectivityChanged(connectivity.isOnline) { applySecondOpinionEnabled() }
        showAsk(scan, isRu)
        refreshSecondOpinion(scan, isRu)
    }

    private fun refreshSecondOpinion(scan: ScanEntity, isRu: Boolean) {
        activity.lifecycleScope.launch {
            repository.getCommunityOpinions(scan.clientScanId).onSuccess { resp ->
                val n = resp.opinions.size
                when {
                    resp.wantsOpinion ->
                        showOpenRequest(scan, isRu, n, resp.opinionRequestedAt)
                    n > 0 -> showViewReplies(scan, isRu, n)
                    else -> showAsk(scan, isRu)
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
        isRu: Boolean,
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

    private fun showViewReplies(scan: ScanEntity, isRu: Boolean, count: Int) {
        val btn = binding.btnSecondOpinion
        btn.clearAnimation()
        applySecondOpinionEnabled()
        btn.text = activity.getString(R.string.result_second_opinion_view, count)
        btn.setOnClickListener { openReplies(scan) }
    }

    private fun showAsk(scan: ScanEntity, isRu: Boolean) {
        val btn = binding.btnSecondOpinion
        btn.clearAnimation()
        applySecondOpinionEnabled()
        btn.setText(R.string.result_get_second_opinion)
        btn.setOnClickListener {
            // Second opinions come FROM the community, so they require the
            // community opt-in. Offer it in context instead of silently
            // sharing the scan of a user who never enabled anything.
            if (!CommunityPrefs.uploadsEnabled(activity)) {
                promptEnableCommunity(scan, isRu)
            } else {
                beginRequest(scan, isRu)
            }
        }
    }

    /** In-context community opt-in shown when a non-member asks for a second
     *  opinion. Confirming requests coarse location (notifications are asked
     *  separately, only once the request itself goes out). */
    private fun promptEnableCommunity(scan: ScanEntity, isRu: Boolean) {
        com.google.android.material.dialog.MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.second_opinion_needs_community_title)
            .setMessage(R.string.second_opinion_needs_community_body)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.community_enable_cta) { _, _ ->
                requestCommunityOptIn { locationGranted ->
                    if (!locationGranted) return@requestCommunityOptIn
                    CommunityPrefs.enable(activity)
                    com.plantdiseases.app.push.PushManager.registerIfEnabled(activity)
                    beginRequest(scan, isRu)
                }
            }
            .show()
    }

    private fun beginRequest(scan: ScanEntity, isRu: Boolean) {
        opinionRequestInFlight = true
        val btn = binding.btnSecondOpinion
        applySecondOpinionEnabled()
        btn.setText(R.string.result_second_opinion_waiting)
        // Notifications are requested HERE — the one moment they're clearly
        // needed (replies to YOUR ask) — not as part of the community opt-in.
        ensureNotificationPermission()
        val pseudoId = TelemetryIds.userPseudoId(activity)
        activity.lifecycleScope.launch {
            // Ensure the scan is in the community corpus so others can
            // see the ask (force = explicit consent).
            communityUploader.uploadFromEntity(scan, force = true)
            repository.requestSecondOpinion(scan.clientScanId, pseudoId)
                .onSuccess {
                    opinionRequestInFlight = false
                    // Fresh request: 0 replies, full 72 h window.
                    showOpenRequest(
                        scan,
                        isRu,
                        replyCount = 0,
                        requestedAtSec = System.currentTimeMillis() / 1000L,
                    )
                    // Seed the notify baseline at 0 so the very next
                    // reply counts as new, and flip the global init flag.
                    OpinionPrefs.markInitialized(activity)
                    OpinionPrefs.setSeen(activity, scan.clientScanId, 0)
                }
                .onFailure {
                    opinionRequestInFlight = false
                    showAsk(scan, isRu)
                    Toast.makeText(
                        activity,
                        R.string.result_second_opinion_failed,
                        Toast.LENGTH_SHORT,
                    ).show()
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
                // Community v2: commenting is unlocked here too — the server
                // already excludes the caller's own scans from this carousel
                // (userPseudoId doubles as exclude_user_pseudo_id below), so
                // the strip itself is gated behind the community opt-in
                // (handled below), not behind "is this someone else's scan".
                // The localClientScanIds() check is belt-and-braces for a
                // reinstalled app whose new pseudo-id doesn't match an old
                // scan still cached in the feed — same guard NearbyScansActivity
                // already applies to its own list.
                recordView(item.scanId, pseudoId)
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
                        // inside show()'s commit().
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
                // Same opt-in as the main community screen: request coarse
                // location (+ notifications) first. Only flip community ON and
                // load the feed once location is granted; on denial, leave the
                // "enable community" button in place so the user can retry.
                requestCommunityOptIn { locationGranted ->
                    if (!locationGranted) return@requestCommunityOptIn
                    CommunityPrefs.enable(activity)
                    com.plantdiseases.app.push.PushManager.registerIfEnabled(activity)
                    binding.btnEnableSimilar.visibility = View.GONE
                    fetchAndRender(adapter, diseaseKey, scan.speciesScientific, isRu)
                }
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
