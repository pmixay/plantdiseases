package com.plantdiseases.app.ui.result

import android.animation.ValueAnimator
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.widget.Toast
import android.os.Bundle
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.ui.guide.GuideDetailSheet
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import java.io.File

/**
 * Shows one saved scan's diagnosis. The activity is a thin host: it owns the
 * screen chrome (insets, toolbar, pinch-zoom, sticky header, skeleton) and the
 * status/image header, then hands each independent section to a controller —
 * [DiagnosisContentController], [SpeciesCardController], [CommunityController],
 * [CarePlanController], [FeedbackController], [GardenAttachController] and
 * [ShareController]. The activity-result permission launchers stay here because
 * they must be registered before the activity is STARTED.
 */
class ResultActivity : AppCompatActivity() {

    private lateinit var binding: ActivityResultBinding

    private val scanRepository: ScanRepository by inject()

    private var scaleFactor = 1.0f
    private lateinit var scaleGestureDetector: ScaleGestureDetector
    private var skeletonAnimator: ValueAnimator? = null

    private var pendingScanForToolbar: ScanEntity? = null

    // Treatment/Prevention tab to restore after the (async) result load
    // rebuilds the tabs — survives rotation and process-death.
    private var restoredTabIndex = 0

    private val diagnosisContent by lazy { DiagnosisContentController(this, binding) }
    private val speciesCard by lazy { SpeciesCardController(this, binding) }
    private val carePlan by lazy { CarePlanController(this, binding) }
    private val feedback by lazy { FeedbackController(this, binding) }
    private val gardenAttach by lazy { GardenAttachController(this, binding) }
    private val share by lazy { ShareController(this) }
    private val community by lazy {
        CommunityController(this, binding, ::ensureNotificationPermission)
    }

    // Notifications-only prompt, fired when the user asks for a second opinion
    // so the reply can actually reach them. nm.notify() no-ops if denied.
    private val notifPermissionLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission(),
    ) { granted ->
        com.plantdiseases.app.util.OpinionPrefs.setNotificationsEnabled(this, granted)
        if (granted) {
            startOpinionDelivery()
        }
    }

    private fun ensureNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.TIRAMISU) {
            com.plantdiseases.app.util.OpinionPrefs.setNotificationsEnabled(this, true)
            startOpinionDelivery()
        } else if (androidx.core.content.ContextCompat.checkSelfPermission(
                this,
                android.Manifest.permission.POST_NOTIFICATIONS,
            ) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            notifPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        } else {
            com.plantdiseases.app.util.OpinionPrefs.setNotificationsEnabled(this, true)
            startOpinionDelivery()
        }
    }

    private fun startOpinionDelivery() {
        com.plantdiseases.app.push.PushManager.registerIfEnabled(this)
        (application as PlantDiseasesApp).scheduleOpinionChecks()
        (application as PlantDiseasesApp).checkOpinionsNow()
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        restoredTabIndex = savedInstanceState?.getInt(KEY_RESULT_TAB, 0) ?: 0
        // Edge-to-edge: let the gradient app bar fill the status-bar area.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityResultBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.appBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(top = bars.top)
            // Lift the bottom action bar above the gesture / nav bar. It's a
            // MaterialCardView whose setPadding() is a no-op (CardView manages
            // its own padding), so setContentPadding() is the inset it honours;
            // the frosted-glass background still fills down behind the bar.
            binding.bottomActionBar.setContentPadding(0, 0, 0, bars.bottom)
            insets
        }

        // Keep the scroll content clear of the floating bottom bar. The bar's
        // height isn't known until layout — a static paddingBottom left the
        // last card hidden behind the buttons — so match it to the measured
        // height instead.
        binding.bottomActionBar.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            val h = binding.bottomActionBar.height
            if (h > 0 && binding.scrollView.paddingBottom != h) {
                binding.scrollView.updatePadding(bottom = h)
            }
        }

        val scanId = intent.getLongExtra(EXTRA_SCAN_ID, -1)
        if (scanId == -1L) {
            finish()
            return
        }

        binding.toolbar.setNavigationOnClickListener { finish() }
        setupToolbarMenu()
        setupPinchToZoom()
        setupStickyHeader()
        // Registered in onCreate so the handoffs survive the activity + sheet
        // being recreated together (rotation, process death with a sheet open).
        feedback.registerCorrectionListener(scanId) { pendingScanForToolbar }
        gardenAttach.registerCreatePlantListener()
        loadResult(scanId)
    }

    private fun setupToolbarMenu() {
        binding.toolbar.inflateMenu(R.menu.result_toolbar_menu)
        binding.toolbar.setOnMenuItemClickListener { item ->
            val scan = pendingScanForToolbar ?: return@setOnMenuItemClickListener false
            when (item.itemId) {
                R.id.action_delete -> {
                    onDeleteAction(scan)
                    true
                }
                else -> false
            }
        }
    }

    private fun onDeleteAction(scan: ScanEntity) {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.delete_scan)
            .setMessage(R.string.delete_scan_confirm)
            .setPositiveButton(R.string.delete) { _, _ ->
                lifecycleScope.launch {
                    runCatching { File(scan.imagePath).delete() }
                    scanRepository.deleteScan(scan.id)
                    finish()
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun setupPinchToZoom() {
        scaleGestureDetector = ScaleGestureDetector(
            this,
            object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
                override fun onScale(detector: ScaleGestureDetector): Boolean {
                    scaleFactor *= detector.scaleFactor
                    scaleFactor = scaleFactor.coerceIn(1.0f, 3.0f)
                    binding.imageFrame.scaleX = scaleFactor
                    binding.imageFrame.scaleY = scaleFactor
                    return true
                }
            }
        )

        binding.cardImage.setOnTouchListener { _, event ->
            scaleGestureDetector.onTouchEvent(event)
            if (event.action == MotionEvent.ACTION_UP && event.pointerCount <= 1) {
                if (scaleFactor > 1.01f) {
                    binding.imageFrame.animate().scaleX(1f).scaleY(1f).setDuration(200).start()
                    scaleFactor = 1.0f
                }
            }
            scaleGestureDetector.isInProgress
        }
    }

    private fun setupStickyHeader() {
        binding.scrollView.setOnScrollChangeListener { _, _, scrollY, _, _ ->
            binding.stickyHeader.visibility =
                if (scrollY > binding.cardStatus.bottom) View.VISIBLE else View.GONE
        }
    }

    private fun loadResult(scanId: Long) {
        binding.skeletonLayout.visibility = View.VISIBLE
        binding.scrollView.visibility = View.GONE
        binding.bottomActionBar.visibility = View.GONE
        animateSkeleton()

        lifecycleScope.launch {
            val scan = scanRepository.getScanById(scanId)
            if (scan == null) {
                finish()
                return@launch
            }

            skeletonAnimator?.cancel()
            binding.skeletonLayout.visibility = View.GONE
            binding.scrollView.visibility = View.VISIBLE
            binding.bottomActionBar.visibility = View.VISIBLE

            displayResult(scan, scanRepository)
            Haptics.success(this@ResultActivity)
        }
    }

    private fun animateSkeleton() {
        skeletonAnimator?.cancel()
        // Walk the first child of the ShimmerFrameLayout (the inner LinearLayout
        // of placeholder cards) so each block still fades independently.
        val container = binding.skeletonLayout.getChildAt(0) as? ViewGroup
            ?: binding.skeletonLayout
        val animator = ValueAnimator.ofFloat(0.3f, 0.7f).apply {
            duration = 800
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener { anim ->
                val alpha = anim.animatedValue as Float
                for (i in 0 until container.childCount) {
                    container.getChildAt(i).alpha = alpha
                }
            }
        }
        skeletonAnimator = animator
        animator.start()
    }

    private fun displayResult(scan: ScanEntity, repository: ScanRepository) {
        val isRu = LocaleHelper.isRussian(this)
        val isNotAPlant = repository.isNotAPlantScan(scan)
        // Trust the server's final verdict end-to-end (isHealthy/diseaseName),
        // not a re-derived argmax over all_probs — overriding it fought the
        // server's own weak-healthy reroute and flipped the visible verdict.
        val isHealthyVerdict = scan.isHealthy
        // A not_a_plant verdict carrying the blur warning is really "couldn't
        // read the photo", not a confident "this isn't a plant" — present it as
        // blurry-or-NAP so a genuine blurry plant isn't told it isn't one.
        val isBlurryReject = isNotAPlant &&
            "blurry_image" in repository.parseWarnings(scan.warnings)
        // On-device (Aqua) scan: local-first and deliberately limited. It has no
        // server-issued scan_id, so it never joins the community feed, can't be
        // added to the garden, and has no feedback channel. The offline warning
        // card (DiagnosisContentController) marks it in the result body.
        val isOfflineAqua = "offline_aqua" in repository.parseWarnings(scan.warnings)
        val diseaseName = when {
            isBlurryReject -> getString(R.string.blurry_or_not_a_plant_diagnosis_name)
            isNotAPlant -> getString(R.string.not_a_plant_diagnosis_name)
            isRu -> scan.diseaseNameRu
            else -> scan.diseaseName
        }

        binding.apply {
            Glide.with(this@ResultActivity)
                .load(File(scan.imagePath))
                .transform(CenterCrop(), RoundedCorners(24))
                .into(ivPlant)

            // YOLOv8 bbox overlay — only on a disease verdict with regions. The
            // bbox coords are in the server-side image's pixel space (the scan
            // row carries those dims); render against them directly. Stage 1 is
            // a single-class leaf detector and "diseased" is one whole-image
            // verdict, so mark EVERY detected leaf red (not just the primary
            // crop) — the primary box just gets a thicker stroke.
            val regions = repository.parseRegions(scan.regions)
            if (!isNotAPlant && !isHealthyVerdict && regions.isNotEmpty()) {
                val primaryIdx = scan.primaryRegionIndex ?: -1
                val overlayBoxes = regions.mapIndexed { i, r ->
                    HeatmapOverlayView.Box(
                        x = r.x,
                        y = r.y,
                        width = r.width,
                        height = r.height,
                        diseased = true,
                        primary = i == primaryIdx,
                    )
                }
                heatmapOverlay.setBoxes(overlayBoxes, scan.detectionImageWidth, scan.detectionImageHeight)
                heatmapOverlay.postDelayed({
                    heatmapOverlay.animateIn()
                    tvHeatmapLabel.visibility = View.VISIBLE
                    tvHeatmapLabel.alpha = 0f
                    tvHeatmapLabel.animate().alpha(1f).setDuration(600).start()
                }, 500)
            }

            tvDiseaseName.text = diseaseName
            tvStickyName.text = diseaseName

            // "Read the full guide" — deep-link to the offline doc for this
            // verdict's class, when one exists (not_a_plant has none).
            val guideId = GuideDataProvider.guideIdForDisease(repository.verdictClassKey(scan) ?: "")
            if (guideId != null) {
                btnOpenGuide.visibility = View.VISIBLE
                btnOpenGuide.setOnClickListener {
                    GuideDetailSheet.newInstance(guideId).show(supportFragmentManager, "guide_detail")
                }
            } else {
                btnOpenGuide.visibility = View.GONE
            }

            // Bundled example photo of this diagnosis. Keyed off the DISPLAYED
            // verdict (verdictClassKey), not the raw argmax, so a rerouted scan
            // looks up the right class.
            tvDiseasePhotosLabel.visibility = View.GONE
            hsvDiseasePhotos.visibility = View.GONE
            GuideDataProvider.exampleImageUrlForDisease(repository.verdictClassKey(scan) ?: "")
                ?.let { diagnosisContent.loadDiseasePhotos(it) }

            community.bindOwnScanOpinions(scan, isRu)

            val confidencePercent = (scan.confidence * 100).toInt()
            // For NAP we don't expose a confidence bar — "100% confident this
            // isn't a plant" reads as the model being certain about a diagnosis.
            if (isNotAPlant) {
                tvConfidence.visibility = View.GONE
                progressConfidence.visibility = View.GONE
                tvStickyConfidence.visibility = View.GONE
            } else {
                tvConfidence.visibility = View.VISIBLE
                progressConfidence.visibility = View.VISIBLE
                tvStickyConfidence.visibility = View.VISIBLE
                tvConfidence.text = getString(R.string.confidence_format, confidencePercent)
                tvStickyConfidence.text = getString(R.string.confidence_format, confidencePercent)
                progressConfidence.max = 100
                progressConfidence.progress = 0
                ValueAnimator.ofInt(0, confidencePercent).apply {
                    duration = 800
                    interpolator = DecelerateInterpolator(2.5f)
                    addUpdateListener { anim -> progressConfidence.progress = anim.animatedValue as Int }
                }.start()
            }

            when {
                isBlurryReject -> {
                    cardStatus.setCardBackgroundColor(getColor(R.color.neutral_blue_bg))
                    tvStatusLabel.text = getString(R.string.status_blurry_or_not_a_plant)
                    tvStatusLabel.setTextColor(getColor(R.color.neutral_blue))
                    ivStatusIcon.setImageResource(R.drawable.ic_warning)
                    ivStatusIcon.setColorFilter(getColor(R.color.neutral_blue))
                }
                isNotAPlant -> {
                    cardStatus.setCardBackgroundColor(getColor(R.color.neutral_blue_bg))
                    tvStatusLabel.text = getString(R.string.status_not_a_plant)
                    tvStatusLabel.setTextColor(getColor(R.color.neutral_blue))
                    ivStatusIcon.setImageResource(R.drawable.ic_warning)
                    ivStatusIcon.setColorFilter(getColor(R.color.neutral_blue))
                }
                isHealthyVerdict -> {
                    cardStatus.setCardBackgroundColor(getColor(R.color.healthy_green_bg))
                    tvStatusLabel.text = getString(R.string.status_healthy)
                    tvStatusLabel.setTextColor(getColor(R.color.healthy_green))
                    ivStatusIcon.setImageResource(R.drawable.ic_healthy)
                    ivStatusIcon.clearColorFilter()
                }
                else -> {
                    cardStatus.setCardBackgroundColor(getColor(R.color.disease_red_bg))
                    tvStatusLabel.text = getString(R.string.status_disease)
                    tvStatusLabel.setTextColor(getColor(R.color.disease_red))
                    ivStatusIcon.setImageResource(R.drawable.ic_disease)
                    ivStatusIcon.clearColorFilter()
                }
            }

            // Description: for NAP we swap in a retry hint and silence the
            // disease-only sections below.
            if (isNotAPlant) {
                tvDescription.text = getString(
                    if (isBlurryReject) {
                        R.string.blurry_or_not_a_plant_result_body
                    } else {
                        R.string.not_a_plant_result_body
                    },
                )
                cardTreatmentPrevention.visibility = View.GONE
                cardAlternatives.visibility = View.GONE
                cardSpecies.visibility = View.GONE
                cardSimilarScans.visibility = View.GONE
                cardPipelineWarning.visibility = View.GONE
                cardLowConfidence.visibility = View.GONE
                // Share / add-to-garden don't make sense for NAP — collapse the
                // bottom bar to just the "new scan" button.
                btnShare.visibility = View.GONE
                btnAddToGarden.visibility = View.GONE
            } else {
                tvDescription.text = if (isRu) scan.descriptionRu else scan.description
                btnShare.visibility = View.VISIBLE
                // Offline Aqua scans can't be added to the garden (limited, local).
                btnAddToGarden.visibility = if (isOfflineAqua) View.GONE else View.VISIBLE
                diagnosisContent.bindPipelineWarnings(scan, repository)
                diagnosisContent.bindAlternatives(scan, repository, isRu)
                speciesCard.bind(scan)
                community.bindSimilarScans(scan, repository, isRu)
                diagnosisContent.setupTreatmentPrevention(scan, repository, isRu, restoredTabIndex)
                carePlan.bind(scan, repository, isRu)
            }

            // Toxicity + feedback render for every plant verdict (incl. healthy)
            // but stay hidden on NAP.
            if (isNotAPlant) {
                cardToxicity.visibility = View.GONE
                cardFeedback.visibility = View.GONE
            } else {
                diagnosisContent.bindToxicity(scan, repository)
                // Feedback binds to a server-issued scan_id; an offline Aqua scan
                // has none, so hide it rather than offer an action that can't post.
                if (isOfflineAqua) {
                    cardFeedback.visibility = View.GONE
                } else {
                    feedback.bind(scan)
                }
            }

            tvDate.text = ImageUtils.formatTimestamp(scan.timestamp)

            btnNewScan.setOnClickListener {
                // Always land back on the scanner tab — finish() alone would
                // return to whichever tab launched the result (e.g. Gallery).
                startActivity(
                    Intent(this@ResultActivity, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                        putExtra(MainActivity.EXTRA_OPEN_SCANNER, true)
                    },
                )
                finish()
            }

            btnAddToGarden.text = if (scan.plantId == null) {
                getString(R.string.add_to_garden)
            } else {
                getString(R.string.added_to_garden)
            }
            btnAddToGarden.setOnClickListener { gardenAttach.showAddToGardenDialog(scan) }
            btnShare.setOnClickListener { share.share(scan, repository, isRu) }

            // Offline (Aqua) scans can be upgraded: re-run the same photo through
            // the full online model. Shown in the offline warning card only.
            btnImproveOnline.visibility = if (isOfflineAqua) View.VISIBLE else View.GONE
            if (isOfflineAqua) {
                btnImproveOnline.setOnClickListener { improveOnline(scan) }
            }

            // Low-confidence card carries its own retry button (the action
            // belongs next to the warning, not in the global bottom bar).
            if (!isNotAPlant && scan.confidence < 0.5f && !isHealthyVerdict) {
                cardLowConfidence.visibility = View.VISIBLE
                btnRetryAnalysis.visibility = View.VISIBLE
            } else {
                cardLowConfidence.visibility = View.GONE
            }

            // No-photo fallback CTA: offer the symptom checker whenever the
            // pipeline couldn't get a confident plant read or the diagnosis is
            // low-confidence, so the user still gets guidance.
            val showSymptomChecker = isNotAPlant || (scan.confidence < 0.5f && !isHealthyVerdict)
            btnSymptomChecker.visibility = if (showSymptomChecker) View.VISIBLE else View.GONE
            btnSymptomChecker.setOnClickListener {
                startActivity(
                    Intent(this@ResultActivity, com.plantdiseases.app.ui.symptom.SymptomCheckerActivity::class.java),
                )
            }

            btnRetryAnalysis.setOnClickListener {
                startActivity(
                    Intent(this@ResultActivity, AnalysisActivity::class.java).apply {
                        putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, scan.imagePath)
                    },
                )
                finish()
            }

            // When unsure, let the user raise a hand for a community second
            // opinion on their OWN scan — or, once replies arrive, view them.
            community.setupSecondOpinion(scan, repository, isRu)

            animateCardsIn()

            // Toolbar overflow menu owns the delete action.
            pendingScanForToolbar = scan
        }
    }

    /** Re-run an offline scan's photo through the full online model. Requires a
     *  validated connection; the online result replaces the offline scan in
     *  place (AnalysisActivity's force-family + replace-scan flow). */
    private fun improveOnline(scan: ScanEntity) {
        if (!hasValidatedInternet()) {
            Toast.makeText(this, R.string.improve_online_no_network, Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(
            Intent(this, AnalysisActivity::class.java).apply {
                putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, scan.imagePath)
                putExtra(AnalysisActivity.EXTRA_FORCE_FAMILY, ModelFamily.GAMMA.id)
                putExtra(AnalysisActivity.EXTRA_REPLACE_SCAN_ID, scan.id)
            },
        )
        finish()
    }

    private fun hasValidatedInternet(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    private fun animateCardsIn() {
        val cards = listOf(
            binding.cardStatus,
            binding.cardDescription,
            binding.cardTreatmentPrevention,
            binding.cardAlternatives,
            binding.cardSpecies,
            binding.cardSimilarScans,
            binding.cardToxicity,
            binding.cardFeedback,
            binding.cardPipelineWarning,
            binding.cardLowConfidence,
        ).filter { it.visibility == View.VISIBLE }

        cards.forEachIndexed { index, view ->
            view.translationY = 100f
            view.alpha = 0f
            view.animate()
                .translationY(0f)
                .alpha(1f)
                .setDuration(400)
                .setStartDelay((index * 80).toLong())
                .setInterpolator(DecelerateInterpolator())
                .start()
        }
    }

    override fun onDestroy() {
        skeletonAnimator?.cancel()
        skeletonAnimator = null
        super.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        // Persist the selected Treatment/Prevention tab so it survives rotation
        // and process-death (the scan itself reloads from the DB by scanId).
        outState.putInt(
            KEY_RESULT_TAB,
            binding.tabsTreatmentPrevention.selectedTabPosition.coerceAtLeast(0),
        )
    }

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"

        private const val KEY_RESULT_TAB = "result_tab"

        /** When true, auto-open the community opinions for this scan once it
         *  loads — set from an opinion notification tap and from tapping a
         *  Gallery row that has new opinions. */
        const val EXTRA_OPEN_OPINIONS = "extra_open_opinions"
    }
}
