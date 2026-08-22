package com.plantdiseases.app.ui.result

import android.animation.ValueAnimator
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.util.TypedValue
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.widget.Toast
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
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingContext
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingCoordinator
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.InAppReviewManager
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.OnboardingStateStore
import com.plantdiseases.app.util.enableWhileOnline
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

/**
 * Shows one saved scan's diagnosis. The activity is a thin host: it owns the
 * screen chrome (insets, toolbar, pinch-zoom, sticky header, skeleton) and the
 * status/image header, then hands each independent section to a controller —
 * [DiagnosisContentController], [CommunityController],
 * [CarePlanController], [FeedbackController], [GardenAttachController] and
 * [ShareController]. The activity-result permission launchers stay here because
 * they must be registered before the activity is STARTED.
 */
@AndroidEntryPoint
class ResultActivity : AppCompatActivity() {

    private enum class ConfidenceAdvisory {
        LOW,
        VERY_LOW,
    }

    private lateinit var binding: ActivityResultBinding

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var onboardingState: OnboardingStateStore

    @Inject
    lateinit var connectivity: ConnectivityObserver

    @Inject
    lateinit var plantRepository: PlantRepository

    @Inject
    lateinit var careRepository: CareRepository

    @Inject
    lateinit var communityUploader: CommunityUploader

    private var scaleFactor = 1.0f
    private lateinit var scaleGestureDetector: ScaleGestureDetector
    private var skeletonAnimator: ValueAnimator? = null
    private lateinit var onboardingCoordinator: OnboardingCoordinator

    private var pendingScanForToolbar: ScanEntity? = null

    /** True for the one render that follows a just-completed analysis; see
     *  [EXTRA_FRESH_SCAN]. Cleared as soon as it is spent. */
    private var freshScan = false

    // RuStore in-app review prompt, shared by every "the user just had a good
    // moment" controller below — one cooldown across all of them (see
    // InAppReviewManager), not one per call site.
    private val reviewManager by lazy { InAppReviewManager(this) }

    private val diagnosisContent by lazy { DiagnosisContentController(this, binding, plantRepository) }
    private val carePlan by lazy {
        CarePlanController(this, binding, scanRepository, plantRepository, reviewManager)
    }
    private val feedback by lazy { FeedbackController(this, binding, scanRepository, reviewManager) }
    private val gardenAttach by lazy {
        GardenAttachController(
            this,
            binding,
            plantRepository,
            scanRepository,
            careRepository,
            reviewManager,
        )
    }
    private val share by lazy { ShareController(this, scanRepository) }
    private val community by lazy {
        CommunityController(
            this,
            binding,
            scanRepository,
            communityUploader,
            connectivity,
            ::ensureNotificationPermission,
            ::requestCommunityOptIn,
        )
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

    // In-context community opt-in from the result screen (the "enable community"
    // button under the similar-scans card and the second-opinion gate). Mirrors
    // NearbyScansActivity: request coarse location ONLY and flip community ON
    // when it's granted — so a denial leaves the "enable community" button in
    // place instead of silently enabling with no location. Notifications are
    // NOT part of joining; they're requested when the user asks for a second
    // opinion (see ensureNotificationPermission). The pending callback carries
    // the grant result back to whichever surface asked.
    private var pendingCommunityOptIn: ((Boolean) -> Unit)? = null

    private val communityOptInLauncher = registerForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val locationGranted = granted ||
            androidx.core.content.ContextCompat.checkSelfPermission(
                this,
                android.Manifest.permission.ACCESS_COARSE_LOCATION,
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        pendingCommunityOptIn?.invoke(locationGranted)
        pendingCommunityOptIn = null
    }

    private fun requestCommunityOptIn(onResult: (Boolean) -> Unit) {
        pendingCommunityOptIn = onResult
        communityOptInLauncher.launch(android.Manifest.permission.ACCESS_COARSE_LOCATION)
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
        // Only a scan that JUST finished analysing earns the success buzz.
        // Opening a saved scan from the gallery or a plant's timeline was
        // buzzing too, which read as "we scanned something" when nothing had
        // happened. `savedInstanceState == null` keeps a rotation from
        // re-firing it on the same scan.
        freshScan = savedInstanceState == null &&
            intent.getBooleanExtra(EXTRA_FRESH_SCAN, false)
        // Edge-to-edge: let the gradient app bar fill the status-bar area.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityResultBinding.inflate(layoutInflater)
        setContentView(binding.root)
        onboardingCoordinator = OnboardingCoordinator(
            host = this,
            store = onboardingState,
            navigateToContext = { target ->
                if (target == OnboardingContext.CAMERA || target == OnboardingContext.HOME) {
                    finish()
                    true
                } else {
                    false
                }
            },
        )
        onboardingCoordinator.setContext(OnboardingContext.RESULT)

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
        // The diagnosis itself is already saved locally and reads fine offline,
        // so there is no strip on this screen. Only the follow-ups that need
        // the server are held back: the AI doctor and re-running an offline
        // scan online. (Whether they are shown at all is decided per-verdict in
        // displayResult; this only decides whether a shown one can be used
        // right now.) The second-opinion control is deliberately absent — it
        // also has to stay disabled while an ask is in flight, so
        // CommunityController owns its enabled state outright.
        enableWhileOnline(
            connectivity.isOnline,
            binding.btnAskDoctor,
            binding.btnImproveOnline,
        )
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
            if (freshScan) {
                freshScan = false
                Haptics.success(this@ResultActivity)
            }
            onboardingCoordinator.registerAnchor(
                OnboardingAnchorKey.RESULT_DIAGNOSIS,
                binding.cardStatus,
            )
            onboardingCoordinator.registerAnchor(
                OnboardingAnchorKey.RESULT_ADD_TO_GARDEN,
                binding.btnAddToGarden,
                available = { binding.btnAddToGarden.isShown },
                pauseAfterTarget = true,
            )
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
        val warnings = repository.parseWarnings(scan.warnings)
        // Trust the server's final verdict end-to-end (isHealthy/diseaseName),
        // not a re-derived argmax over all_probs — overriding it fought the
        // server's own weak-healthy reroute and flipped the visible verdict.
        val isHealthyVerdict = scan.isHealthy
        // A not_a_plant verdict carrying the blur warning is really "couldn't
        // read the photo", not a confident "this isn't a plant" — present it as
        // blurry-or-NAP so a genuine blurry plant isn't told it isn't one.
        val isBlurryReject = isNotAPlant &&
            "blurry_image" in warnings
        // On-device (Aqua) scan: local-first and deliberately limited. It has no
        // server-issued scan_id, so it never joins the community feed, can't be
        // added to the garden, and has no feedback channel. The offline warning
        // card (DiagnosisContentController) marks it in the result body.
        val isOfflineAqua = "offline_aqua" in warnings
        val confidenceAdvisory = confidenceAdvisoryFor(scan, warnings, isNotAPlant)
        val diseaseName = when {
            isBlurryReject -> getString(R.string.blurry_or_not_a_plant_diagnosis_name)
            isNotAPlant -> getString(R.string.not_a_plant_diagnosis_name)
            isRu -> scan.diseaseNameRu
            else -> scan.diseaseName
        }
        val resultDescription = when {
            isBlurryReject -> getString(R.string.blurry_or_not_a_plant_result_body)
            isNotAPlant -> getString(R.string.not_a_plant_result_body)
            isRu -> scan.descriptionRu
            else -> scan.description
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
            tvDiseaseName.post { fitDiseaseNameToQuestionButton() }

            // Both the guide and example image move into the compact question
            // overlay, keeping the scrollable result focused on care actions.
            val guideId = GuideDataProvider.guideIdForDisease(repository.verdictClassKey(scan) ?: "")
            // The example-photo preview is keyed off the DISPLAYED verdict
            // (verdictClassKey), not the raw argmax, so a rerouted scan opens
            // the right reference image.
            val appearanceImageUrl =
                GuideDataProvider.exampleImageUrlForDisease(repository.verdictClassKey(scan) ?: "")
            btnDiseaseAppearance.visibility = if (isNotAPlant) View.GONE else View.VISIBLE
            btnDiseaseAppearance.setOnClickListener {
                if (supportFragmentManager.findFragmentByTag(DISEASE_APPEARANCE_TAG) == null) {
                    DiseaseAppearanceDialog.newInstance(
                        imageUrl = appearanceImageUrl,
                        diseaseName = diseaseName,
                        description = resultDescription,
                        guideId = guideId,
                    ).show(supportFragmentManager, DISEASE_APPEARANCE_TAG)
                }
            }

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

            val statusBackgroundColor = when {
                isBlurryReject -> {
                    tvStatusLabel.text = getString(R.string.status_blurry_or_not_a_plant)
                    tvStatusLabel.setTextColor(getColor(R.color.neutral_blue))
                    ivStatusIcon.setImageResource(R.drawable.ic_warning)
                    ivStatusIcon.setColorFilter(getColor(R.color.neutral_blue))
                    R.color.neutral_blue_bg
                }
                isNotAPlant -> {
                    tvStatusLabel.text = getString(R.string.status_not_a_plant)
                    tvStatusLabel.setTextColor(getColor(R.color.neutral_blue))
                    ivStatusIcon.setImageResource(R.drawable.ic_warning)
                    ivStatusIcon.setColorFilter(getColor(R.color.neutral_blue))
                    R.color.neutral_blue_bg
                }
                isHealthyVerdict -> {
                    tvStatusLabel.text = getString(R.string.status_healthy)
                    tvStatusLabel.setTextColor(getColor(R.color.healthy_green))
                    ivStatusIcon.setImageResource(R.drawable.ic_healthy)
                    ivStatusIcon.clearColorFilter()
                    R.color.healthy_green_bg
                }
                else -> {
                    tvStatusLabel.text = getString(R.string.status_disease)
                    tvStatusLabel.setTextColor(getColor(R.color.disease_red))
                    ivStatusIcon.setImageResource(R.drawable.ic_disease)
                    ivStatusIcon.clearColorFilter()
                    R.color.disease_red_bg
                }
            }
            bindInlineConfidenceAdvisory(confidenceAdvisory, statusBackgroundColor)

            // Description: for NAP we swap in a retry hint and silence the
            // disease-only sections below.
            if (isNotAPlant) {
                cardDescription.visibility = View.VISIBLE
                tvDescription.text = resultDescription
                cardAlternatives.visibility = View.GONE
                cardSpecies.visibility = View.GONE
                cardSimilarScans.visibility = View.GONE
                cardPipelineWarning.visibility = View.GONE
                btnAskDoctor.visibility = View.GONE
                // Share / add-to-garden don't make sense for NAP — collapse the
                // bottom bar to just the "new scan" button.
                btnShare.visibility = View.GONE
                btnAddToGarden.visibility = View.GONE
            } else {
                // Plant descriptions now live in the question-mark overlay
                // alongside their visual example and full guide.
                cardDescription.visibility = View.GONE
                btnShare.visibility = View.VISIBLE
                // Offline Aqua scans can't be added to the garden (limited, local).
                btnAddToGarden.visibility = if (isOfflineAqua) View.GONE else View.VISIBLE
                diagnosisContent.bindPipelineWarnings(scan, repository)
                diagnosisContent.bindAlternatives(scan, repository, isRu)
                binding.cardSpecies.bind(scan.speciesScientific, scan.speciesCommon)
                community.bindSimilarScans(scan, repository, isRu)
                btnAskDoctor.visibility = View.VISIBLE
                diagnosisContent.setupAskDoctor(scan)
                carePlan.bind(scan, repository, isRu)
            }

            // Toxicity renders for every plant verdict but stays hidden on NAP.
            // Feedback binds to a server-issued scan_id; an offline Aqua scan
            // has none, so it's hidden rather than offering an action that
            // can't post. NAP gets a dedicated single-action variant: a
            // wrongly-rejected plant photo is high-value retraining signal,
            // so the card offers "This is a plant" (+ optional note) instead
            // of the yes/no pair.
            if (isNotAPlant) {
                if (isOfflineAqua) {
                    cardFeedback.visibility = View.GONE
                } else {
                    feedback.bindNotAPlant(scan)
                }
            } else {
                if (isOfflineAqua) {
                    cardFeedback.visibility = View.GONE
                } else {
                    feedback.bind(scan)
                }
            }

            tvDate.text = ImageUtils.formatRelativeTime(this@ResultActivity, scan.timestamp)

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

            // Community second opinion on the user's OWN scan — offered on
            // every server-diagnosed plant verdict (not just low-confidence
            // ones; the owner may doubt any result). Hidden for NAP (the
            // feed hides NAP scans, so nobody could answer) and for offline
            // scans (no server scan_id to share).
            if (!isNotAPlant && !isOfflineAqua) {
                btnSecondOpinion.visibility = View.VISIBLE
                community.setupSecondOpinion(scan, isRu)
            } else {
                btnSecondOpinion.visibility = View.GONE
            }
            // The row only exists to pair the two follow-up actions; it goes
            // with them so no empty gap is left above the care plan.
            resultActionRow.visibility = if (
                btnSecondOpinion.visibility == View.VISIBLE ||
                btnAskDoctor.visibility == View.VISIBLE
            ) {
                View.VISIBLE
            } else {
                View.GONE
            }

            animateCardsIn()

            // Toolbar overflow menu owns the delete action.
            pendingScanForToolbar = scan
        }
    }

    /**
     * Treat the server warning as the source of truth, while retaining the
     * old numeric fallbacks for scans saved by older app/server versions.
     * Very low always wins when both flags are present.
     */
    private fun confidenceAdvisoryFor(
        scan: ScanEntity,
        warnings: List<String>,
        isNotAPlant: Boolean,
    ): ConfidenceAdvisory? {
        if (isNotAPlant) return null
        return when {
            "very_low_confidence" in warnings || scan.confidence < VERY_LOW_CONFIDENCE_FALLBACK ->
                ConfidenceAdvisory.VERY_LOW
            "low_confidence" in warnings || scan.confidence < LOW_CONFIDENCE_FALLBACK ->
                ConfidenceAdvisory.LOW
            else -> null
        }
    }

    /**
     * Keep the diagnosis label beside the help button on every display width.
     * The server can return long localized names, so reduce only this label
     * until its measured text fits the space left by the fixed-size button.
     */
    private fun fitDiseaseNameToQuestionButton() {
        if (binding.btnDiseaseAppearance.visibility != View.VISIBLE) return

        val textView = binding.tvDiseaseName
        val questionButton = binding.btnDiseaseAppearance
        val marginStart = (questionButton.layoutParams as? ViewGroup.MarginLayoutParams)
            ?.marginStart
            ?: 0
        val availableWidth = (
            binding.diseaseStatusTextColumn.width - questionButton.width - marginStart
            ).coerceAtLeast(1)

        textView.maxWidth = availableWidth
        var textSizeSp = 18f
        textView.setTextSize(TypedValue.COMPLEX_UNIT_SP, textSizeSp)
        while (textSizeSp > 14f && textView.paint.measureText(textView.text.toString()) > availableWidth) {
            textSizeSp -= 0.5f
            textView.setTextSize(TypedValue.COMPLEX_UNIT_SP, textSizeSp)
        }
    }

    /** Keeps uncertainty and its recovery action in the diagnosis card. */
    private fun bindInlineConfidenceAdvisory(
        advisory: ConfidenceAdvisory?,
        standardBackgroundColor: Int,
    ) = binding.run {
        val isVisible = advisory != null
        statusConfidenceGradient.visibility = if (isVisible) View.VISIBLE else View.GONE
        inlineConfidenceNotice.visibility = if (isVisible) View.VISIBLE else View.GONE
        cardStatus.setCardBackgroundColor(
            if (isVisible) Color.TRANSPARENT else getColor(standardBackgroundColor),
        )

        if (advisory != null) {
            tvInlineConfidenceTitle.setText(
                when (advisory) {
                    ConfidenceAdvisory.VERY_LOW -> R.string.warning_very_low_confidence_title
                    ConfidenceAdvisory.LOW -> R.string.warning_low_confidence_title
                },
            )
            tvInlineConfidenceBody.setText(R.string.inline_confidence_retake)
        }
    }

    /** Re-run an offline scan's photo through the full online model. Needs the
     *  server to be reachable; the online result replaces the offline scan in
     *  place (AnalysisActivity's force-family + replace-scan flow).
     *
     *  The check is the app's shared one. This screen used to re-derive it from
     *  NetworkCapabilities on its own, which passed happily on a stale VPN
     *  tunnel — sending the user into an analysis that could only time out. */
    private fun improveOnline(scan: ScanEntity) {
        if (!connectivity.lastKnownOnline) {
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

    private fun animateCardsIn() {
        val cards = listOf(
            binding.cardStatus,
            binding.cardSpecies,
            binding.cardPipelineWarning,
            binding.resultActionRow,
            binding.cardDescription,
            binding.cardSimilarScans,
            binding.cardAlternatives,
            binding.cardFeedback,
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

    override fun onResume() {
        super.onResume()
        // Catches a request/reply state change made on MyScanRepliesActivity
        // (e.g. Stop) — that screen is separate now, so this is how the
        // second-opinion control finds out, same as every other on-resume
        // refresh in this app (no Flow/LiveData plumbing over Room here).
        community.onResume()
    }

    override fun onDestroy() {
        skeletonAnimator?.cancel()
        skeletonAnimator = null
        super.onDestroy()
    }

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"

        /** Set by [com.plantdiseases.app.ui.analysis.AnalysisActivity] only.
         *  Every other entry point (gallery, plant timeline, notifications)
         *  is opening history, and history does not buzz. */
        const val EXTRA_FRESH_SCAN = "extra_fresh_scan"

        private const val LOW_CONFIDENCE_FALLBACK = 0.5f
        private const val VERY_LOW_CONFIDENCE_FALLBACK = 0.4f
        private const val DISEASE_APPEARANCE_TAG = "disease_appearance"
    }
}
