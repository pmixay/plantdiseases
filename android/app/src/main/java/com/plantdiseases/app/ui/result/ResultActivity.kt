package com.plantdiseases.app.ui.result

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.animation.ValueAnimator
import android.view.animation.DecelerateInterpolator
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.tabs.TabLayout
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.TopProb
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.ui.guide.GuideDetailSheet
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream

class ResultActivity : AppCompatActivity() {

    private lateinit var binding: ActivityResultBinding

    private var scaleFactor = 1.0f
    private lateinit var scaleGestureDetector: ScaleGestureDetector
    private var skeletonAnimator: ValueAnimator? = null

    private var carePlanCard: CarePlanCardView? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Edge-to-edge: let the gradient app bar fill the status-bar area.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityResultBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Extend the gradient app bar behind the status bar, and lift the
        // bottom action bar above the navigation bar.
        ViewCompat.setOnApplyWindowInsetsListener(binding.appBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(top = bars.top)
            binding.bottomActionBar.updatePadding(bottom = bars.bottom)
            insets
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
        loadResult(scanId)
    }

    private var pendingScanForToolbar: ScanEntity? = null

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
        MaterialAlertDialogBuilder(this@ResultActivity)
            .setTitle(R.string.delete_scan)
            .setMessage(R.string.delete_scan_confirm)
            .setPositiveButton(R.string.delete) { _, _ ->
                val appInstance = application as PlantDiseasesApp
                lifecycleScope.launch {
                    runCatching { File(scan.imagePath).delete() }
                    appInstance.scanRepository.deleteScan(scan.id)
                    finish()
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun setupPinchToZoom() {
        scaleGestureDetector = ScaleGestureDetector(this, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                scaleFactor *= detector.scaleFactor
                scaleFactor = scaleFactor.coerceIn(1.0f, 3.0f)
                binding.imageFrame.scaleX = scaleFactor
                binding.imageFrame.scaleY = scaleFactor
                return true
            }
        })

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
            val statusCardBottom = binding.cardStatus.bottom
            if (scrollY > statusCardBottom) {
                binding.stickyHeader.visibility = View.VISIBLE
            } else {
                binding.stickyHeader.visibility = View.GONE
            }
        }
    }

    private fun loadResult(scanId: Long) {
        val app = application as PlantDiseasesApp

        binding.skeletonLayout.visibility = View.VISIBLE
        binding.scrollView.visibility = View.GONE
        binding.bottomActionBar.visibility = View.GONE
        animateSkeleton()

        lifecycleScope.launch {
            val scan = app.scanRepository.getScanById(scanId)
            if (scan == null) {
                finish()
                return@launch
            }

            skeletonAnimator?.cancel()
            binding.skeletonLayout.visibility = View.GONE
            binding.scrollView.visibility = View.VISIBLE
            binding.bottomActionBar.visibility = View.VISIBLE

            displayResult(scan, app.scanRepository)
            Haptics.success(this@ResultActivity)
        }
    }

    private fun animateSkeleton() {
        skeletonAnimator?.cancel()
        // Walk the first child of the ShimmerFrameLayout (the inner
        // LinearLayout that holds the placeholder cards) so we keep the
        // original "each block fades independently" feel even though
        // the layout is now wrapped in a shimmer host.
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
        // Trust the server's final verdict end-to-end. The previous
        // version overrode `diseaseName` / `confidence` with the
        // top-class key from `all_probs`, which fought the server's
        // own weak-healthy reroute: when the server downgraded a
        // 47%-healthy / 41%-fungal scan to "Fungal Leaf Spot", the
        // override flipped the visible verdict back to "Healthy
        // Plant" while the description and stored `diseaseName`
        // still said fungal — the cause of the screenshot bug.
        val isHealthyVerdict = scan.isHealthy
        val diseaseName = when {
            isNotAPlant -> getString(R.string.not_a_plant_diagnosis_name)
            isRu -> scan.diseaseNameRu
            else -> scan.diseaseName
        }

        binding.apply {
            // Image
            Glide.with(this@ResultActivity)
                .load(File(scan.imagePath))
                .transform(CenterCrop(), RoundedCorners(24))
                .into(ivPlant)

            // YOLOv8 bbox overlay — only when we actually found a diseased
            // region. NAP and healthy verdicts skip the overlay.
            // Bbox coordinates are in the *server-side* image's pixel
            // space (the upload after PIL decode, which Android
            // pre-rotates + downscales to <=1024 px). The server echoes
            // those dims back on every response and the scan row carries
            // them; render against those values directly.
            val regions = repository.parseRegions(scan.regions)
            if (!isNotAPlant && !isHealthyVerdict && regions.isNotEmpty()) {
                val origBounds = Pair(scan.detectionImageWidth, scan.detectionImageHeight)
                val primaryIdx = scan.primaryRegionIndex ?: -1
                val overlayBoxes = regions.mapIndexed { i, r ->
                    HeatmapOverlayView.Box(
                        x = r.x, y = r.y, width = r.width, height = r.height,
                        diseased = (i == primaryIdx),
                        primary = i == primaryIdx,
                    )
                }
                heatmapOverlay.setBoxes(overlayBoxes, origBounds.first, origBounds.second)
                heatmapOverlay.postDelayed({
                    heatmapOverlay.animateIn()
                    tvHeatmapLabel.visibility = View.VISIBLE
                    tvHeatmapLabel.alpha = 0f
                    tvHeatmapLabel.animate().alpha(1f).setDuration(600).start()
                }, 500)
            }

            // Disease name
            tvDiseaseName.text = diseaseName

            // Sticky header data
            tvStickyName.text = diseaseName

            // "Read the full guide" — deep-link to the offline doc article for
            // this verdict's class, when one exists (not_a_plant has none).
            val guideId = GuideDataProvider.guideIdForDisease(repository.topClassKey(scan) ?: "")
            if (guideId != null) {
                btnOpenGuide.visibility = View.VISIBLE
                btnOpenGuide.setOnClickListener {
                    GuideDetailSheet.newInstance(guideId)
                        .show(supportFragmentManager, "guide_detail")
                }
            } else {
                btnOpenGuide.visibility = View.GONE
            }

            // Community opinions others left on THIS (the user's own) scan.
            // Loaded lazily; the button appears only if there are any.
            btnViewOpinions.visibility = View.GONE
            val ownScanId = scan.clientScanId
            if (ownScanId.isNotBlank()) {
                lifecycleScope.launch {
                    repository.getCommunityOpinions(ownScanId).onSuccess { resp ->
                        val n = resp.opinions.size
                        if (n > 0) {
                            binding.btnViewOpinions.text =
                                getString(R.string.result_view_opinions, n)
                            binding.btnViewOpinions.visibility = View.VISIBLE
                            binding.btnViewOpinions.setOnClickListener {
                                CommunityOpinionSheet.show(
                                    supportFragmentManager,
                                    ownScanId,
                                    classKeyDisplayName(repository.topClassKey(scan) ?: "", isRu),
                                )
                            }
                        }
                    }
                }
            }

            val confidencePercent = (scan.confidence * 100).toInt()
            // For NAP we don't expose a confidence bar — the "verdict" itself
            // is "we can't classify this". Showing 100% confidence next to
            // a "not a plant" reads as the model being certain about the
            // diagnosis, which is misleading.
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
                val confidenceAnimator = ValueAnimator.ofInt(0, confidencePercent).apply {
                    duration = 800
                    interpolator = DecelerateInterpolator(2.5f)
                    addUpdateListener { anim ->
                        progressConfidence.progress = anim.animatedValue as Int
                    }
                }
                confidenceAnimator.start()
            }

            when {
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

            // Description: for NAP we replace it with a dedicated retry hint
            // and silence the disease-only sections below.
            if (isNotAPlant) {
                tvDescription.text = getString(R.string.not_a_plant_result_body)
                cardTreatmentPrevention.visibility = View.GONE
                cardAlternatives.visibility = View.GONE
                cardSpecies.visibility = View.GONE
                cardSimilarScans.visibility = View.GONE
                cardPipelineWarning.visibility = View.GONE
                cardLowConfidence.visibility = View.GONE
                // Share / add-to-garden don't make sense for a NAP verdict —
                // collapse the bottom bar to just the "new scan" button so
                // it expands to fill the row.
                btnShare.visibility = View.GONE
                btnAddToGarden.visibility = View.GONE
            } else {
                tvDescription.text = if (isRu) scan.descriptionRu else scan.description
                btnShare.visibility = View.VISIBLE
                btnAddToGarden.visibility = View.VISIBLE
                buildPipelineWarnings(scan, repository)
                buildAlternativeDiagnoses(scan, repository, isRu)
                buildSpeciesCard(scan)
                buildSimilarScansCard(scan, repository, isRu)
                setupTreatmentPreventionTabs(scan, repository, isRu)
                buildCarePlanCard(scan, repository, isRu)
            }

            // Toxicity card + feedback card render for every plant verdict
            // (including healthy) — they're not disease-only — but stay
            // hidden on NAP since neither makes sense without a plant.
            if (isNotAPlant) {
                cardToxicity.visibility = View.GONE
                cardFeedback.visibility = View.GONE
            } else {
                buildToxicityCard(scan, repository)
                buildFeedbackCard(scan)
            }

            // Date
            tvDate.text = ImageUtils.formatTimestamp(scan.timestamp)

            btnNewScan.setOnClickListener { finish() }

            btnAddToGarden.text = if (scan.plantId == null) {
                getString(R.string.add_to_garden)
            } else {
                getString(R.string.added_to_garden)
            }
            btnAddToGarden.setOnClickListener {
                showAddToGardenDialog(scan)
            }

            btnShare.setOnClickListener {
                shareWithImage(scan, repository, isRu)
            }

            // Low-confidence card carries its own retry button now —
            // the action belongs next to the warning text, not in the
            // global bottom bar where it duplicates "New scan".
            if (!isNotAPlant && scan.confidence < 0.5f && !isHealthyVerdict) {
                cardLowConfidence.visibility = View.VISIBLE
                btnRetryAnalysis.visibility = View.VISIBLE
            } else {
                cardLowConfidence.visibility = View.GONE
            }

            btnRetryAnalysis.setOnClickListener {
                val intent = Intent(this@ResultActivity, AnalysisActivity::class.java).apply {
                    putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, scan.imagePath)
                }
                startActivity(intent)
                finish()
            }

            // When unsure, let the user crowd-source a verdict on their own
            // (already community-shared) scan, keyed by its client scan id.
            btnSecondOpinion.setOnClickListener {
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    scan.clientScanId,
                    classKeyDisplayName(repository.topClassKey(scan) ?: "", isRu),
                )
            }

            // Animate cards from bottom
            animateCardsIn()

            // Toolbar overflow menu owns the delete action.
            pendingScanForToolbar = scan
        }
    }

    private fun showAddToGardenDialog(scan: ScanEntity) {
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            val plants = app.plantRepository.getPlants()
            if (plants.isEmpty()) {
                showCreatePlantForScanDialog(scan)
                return@launch
            }

            val labels = plants.map { it.plant.name }.toTypedArray()

            MaterialAlertDialogBuilder(this@ResultActivity)
                .setTitle(R.string.add_to_garden)
                .setItems(labels) { _, which ->
                    lifecycleScope.launch {
                        app.plantRepository.assignScanToPlant(scan.id, plants[which].plant.id)
                        binding.btnAddToGarden.text = getString(R.string.added_to_garden)
                        Toast.makeText(
                            this@ResultActivity,
                            getString(R.string.scan_added_to_garden),
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
                .setPositiveButton(R.string.create_new_plant) { _, _ ->
                    showCreatePlantForScanDialog(scan)
                }
                .setNegativeButton(R.string.cancel, null)
                .show()
        }
    }

    private fun showCreatePlantForScanDialog(scan: ScanEntity) {
        CreatePlantSheet.show(
            supportFragmentManager,
            scan,
            initialName = scan.diseaseName,
        ) { plantName, species, scientific, common ->
            val app = application as PlantDiseasesApp
            lifecycleScope.launch {
                val plantId = app.plantRepository.savePlant(
                    name = plantName,
                    species = species,
                    scientificName = scientific,
                    commonName = common,
                )
                app.plantRepository.assignScanToPlant(scan.id, plantId)
                binding.btnAddToGarden.text = getString(R.string.added_to_garden)
                Toast.makeText(
                    this@ResultActivity,
                    getString(R.string.scan_added_to_garden),
                    Toast.LENGTH_SHORT,
                ).show()
            }
        }
    }

    private fun setupTreatmentPreventionTabs(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val treatment = repository.parseStringList(
            if (isRu) scan.treatmentRu else scan.treatment
        )
        val prevention = repository.parseStringList(
            if (isRu) scan.preventionRu else scan.prevention
        )

        if (treatment.isEmpty() && prevention.isEmpty()) {
            binding.cardTreatmentPrevention.visibility = View.GONE
            return
        }

        binding.cardTreatmentPrevention.visibility = View.VISIBLE

        // Add tabs
        if (treatment.isNotEmpty()) {
            binding.tabsTreatmentPrevention.addTab(
                binding.tabsTreatmentPrevention.newTab().setText(R.string.treatment_label).setTag("treatment")
            )
            buildStepsList(binding.treatmentStepsLayout, treatment, R.drawable.bg_step_circle, R.color.primary)
        }
        if (prevention.isNotEmpty()) {
            binding.tabsTreatmentPrevention.addTab(
                binding.tabsTreatmentPrevention.newTab().setText(R.string.prevention_label).setTag("prevention")
            )
            buildStepsList(binding.preventionStepsLayout, prevention, R.drawable.bg_step_circle_green, R.color.healthy_green)
        }

        // Show first tab content by default
        if (treatment.isNotEmpty()) {
            binding.treatmentStepsLayout.visibility = View.VISIBLE
            binding.preventionStepsLayout.visibility = View.GONE
        } else {
            binding.treatmentStepsLayout.visibility = View.GONE
            binding.preventionStepsLayout.visibility = View.VISIBLE
        }

        binding.tabsTreatmentPrevention.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                when (tab.tag) {
                    "treatment" -> {
                        binding.treatmentStepsLayout.visibility = View.VISIBLE
                        binding.preventionStepsLayout.visibility = View.GONE
                    }
                    "prevention" -> {
                        binding.treatmentStepsLayout.visibility = View.GONE
                        binding.preventionStepsLayout.visibility = View.VISIBLE
                    }
                }
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
    }

    private fun buildPipelineWarnings(scan: ScanEntity, repository: ScanRepository) {
        val warnings = repository.parseWarnings(scan.warnings)
        if (warnings.isEmpty()) {
            binding.cardPipelineWarning.visibility = View.GONE
            return
        }

        val isRu = LocaleHelper.isRussian(this)

        // The server can attach a dynamic `disease_suspected:<class>`
        // warning alongside a healthy verdict — match by prefix and
        // surface the suspected class name (an exact-string `in`
        // check could never match it).
        val suspectedKey = warnings
            .firstOrNull { it.startsWith("disease_suspected:") }
            ?.substringAfter(":")

        // Priority order: very_low_confidence (re-shoot is the only
        // honest path — the verdict in the card is essentially a guess)
        // > blur > not-a-leaf gate > weak-healthy reroute (verdict was
        // rewritten) > uncertain_healthy / disease_suspected (soft
        // hedge) > low_confidence (mild hedge).
        binding.cardPipelineWarning.visibility = View.VISIBLE
        when {
            "very_low_confidence" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_very_low_confidence_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_very_low_confidence)
            }
            "blurry_image" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_blurry_image_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_blurry_image)
            }
            "nap_ood_gated" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_nap_ood_gated_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_nap_ood_gated)
            }
            "weak_healthy_rerouted" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_weak_healthy_rerouted_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_weak_healthy_rerouted)
            }
            suspectedKey != null -> {
                val label = classKeyDisplayName(suspectedKey, isRu) ?: suspectedKey
                binding.tvPipelineWarningTitle.setText(R.string.warning_disease_suspected_title)
                binding.tvPipelineWarningBody.text =
                    getString(R.string.warning_disease_suspected, label)
            }
            "uncertain_healthy" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_uncertain_healthy_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_uncertain_healthy)
            }
            "low_confidence" in warnings -> {
                binding.tvPipelineWarningTitle.setText(R.string.warning_low_confidence_title)
                binding.tvPipelineWarningBody.setText(R.string.warning_low_confidence)
            }
            else -> {
                binding.cardPipelineWarning.visibility = View.GONE
            }
        }
    }

    private fun buildSpeciesCard(scan: ScanEntity) {
        // Render the stored species fields (populated at save time
        // from the server's PlantNet fan-out). The card stays hidden
        // when we have neither a scientific nor a common name —
        // that's the case when PlantNet wasn't configured or every
        // candidate scored zero.
        val sci = scan.speciesScientific?.takeIf { it.isNotBlank() }
        val common = scan.speciesCommon?.takeIf { it.isNotBlank() }
        if (sci == null && common == null) {
            binding.cardSpecies.visibility = View.GONE
            return
        }
        binding.cardSpecies.visibility = View.VISIBLE
        binding.tvSpeciesBest.text = common ?: sci.orEmpty()
        binding.tvSpeciesScientific.text = sci.orEmpty()
        binding.tvSpeciesScientific.visibility =
            if (sci.isNullOrBlank() || sci == common) View.GONE else View.VISIBLE
        val score = scan.speciesScore
        if (score != null && score > 0f) {
            binding.tvSpeciesScore.visibility = View.VISIBLE
            binding.tvSpeciesScore.text = getString(
                R.string.species_card_score,
                (score * 100).toInt().coerceIn(0, 100),
            )
        } else {
            binding.tvSpeciesScore.visibility = View.GONE
        }
        // Alternates intentionally left empty — we only persist the
        // best match on the ScanEntity. The full candidates list is
        // available on the live AnalysisResponse and could be wired
        // later if we add multi-pick UX.
        binding.speciesAltsLayout.removeAllViews()

        // Enrich with a localized Wikipedia blurb + lead photo, and a strip of
        // other community scans of this species — best-effort, both need the
        // scientific name and stay hidden on failure.
        binding.ivSpeciesPhoto.visibility = View.GONE
        binding.tvSpeciesBlurb.visibility = View.GONE
        binding.tvSpeciesSource.visibility = View.GONE
        binding.tvSpeciesCommunityLabel.visibility = View.GONE
        binding.rvSpeciesCommunity.visibility = View.GONE
        if (sci != null) loadSpeciesEnrichment(sci)
    }

    /** Best-effort species enrichment: a localized Wikipedia blurb + lead
     *  photo, and a horizontal strip of other community scans of the species. */
    private fun loadSpeciesEnrichment(scientific: String) {
        val repo = (application as PlantDiseasesApp).scanRepository
        val isRu = LocaleHelper.isRussian(this)
        val pseudoId = TelemetryIds.userPseudoId(this)

        lifecycleScope.launch {
            repo.getSpeciesInfo(scientific, if (isRu) "ru" else "en").onSuccess { info ->
                if (!info.available) return@onSuccess
                info.extract?.takeIf { it.isNotBlank() }?.let {
                    binding.tvSpeciesBlurb.text = it
                    binding.tvSpeciesBlurb.visibility = View.VISIBLE
                }
                info.imageUrl?.takeIf { it.isNotBlank() }?.let { url ->
                    Glide.with(this@ResultActivity)
                        .load(url)
                        .transform(CenterCrop(), RoundedCorners(24))
                        .into(binding.ivSpeciesPhoto)
                    binding.ivSpeciesPhoto.visibility = View.VISIBLE
                }
                val page = info.pageUrl
                if (!page.isNullOrBlank()) {
                    binding.tvSpeciesSource.visibility = View.VISIBLE
                    binding.tvSpeciesSource.setOnClickListener {
                        runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(page))) }
                    }
                }
            }
        }

        val adapter = SimilarScansAdapter(
            displayName = { key -> classKeyDisplayName(key, isRu) ?: key },
            onClick = { item ->
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    item.scanId,
                    classKeyDisplayName(item.disease, isRu) ?: item.disease,
                )
            },
        )
        binding.rvSpeciesCommunity.layoutManager =
            androidx.recyclerview.widget.LinearLayoutManager(
                this, androidx.recyclerview.widget.LinearLayoutManager.HORIZONTAL, false,
            )
        binding.rvSpeciesCommunity.adapter = adapter
        lifecycleScope.launch {
            repo.communityScansBySpecies(scientific, pseudoId).onSuccess { resp ->
                if (resp.items.isNotEmpty()) {
                    adapter.submit(resp.items)
                    binding.tvSpeciesCommunityLabel.text =
                        getString(R.string.species_community_others)
                    binding.tvSpeciesCommunityLabel.visibility = View.VISIBLE
                    binding.rvSpeciesCommunity.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun buildSimilarScansCard(
        scan: ScanEntity,
        repository: ScanRepository,
        isRu: Boolean,
    ) {
        val card = binding.cardSimilarScans
        val rv = binding.rvSimilarScans
        val empty = binding.tvSimilarEmpty
        val chips = binding.chipGroupTopDiseases
        // Default state: hidden until the network call returns.
        card.visibility = View.GONE
        rv.visibility = View.GONE
        empty.visibility = View.GONE
        chips.visibility = View.GONE

        val diseaseKey = repository.topClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')
        val app = application as PlantDiseasesApp
        val pseudoId = com.plantdiseases.app.util.TelemetryIds.userPseudoId(this)
        val adapter = SimilarScansAdapter(
            displayName = { key -> classKeyDisplayName(key, isRu) ?: key },
            onClick = { item ->
                // Tapping a "near you" scan lets the user weigh in with a
                // second opinion on someone else's uncertain verdict.
                CommunityOpinionSheet.show(
                    supportFragmentManager,
                    item.scanId,
                    classKeyDisplayName(item.disease, isRu) ?: item.disease,
                )
            },
        )
        rv.adapter = adapter
        rv.layoutManager = androidx.recyclerview.widget.LinearLayoutManager(
            this, androidx.recyclerview.widget.LinearLayoutManager.HORIZONTAL, false,
        )

        lifecycleScope.launch {
            // First-scan-after-permission may not have a cached
            // region yet because the MainActivity warmer races with
            // this code. Resolve actively so the card lights up on
            // the same scan instead of the next one — Geocoder /
            // LocationManager block, so dispatch to IO; lifecycleScope
            // is Main by default.
            val resolved = withContext(Dispatchers.IO) {
                com.plantdiseases.app.util.RegionResolver.cachedRegion(this@ResultActivity)
                    ?: com.plantdiseases.app.util.RegionResolver.resolveRegion(this@ResultActivity)
            }
            if (resolved == null) {
                // No region yet. Render a soft hint so the user
                // knows why the card is empty.
                card.visibility = View.VISIBLE
                binding.tvSimilarSubtitle.text = getString(R.string.similar_scans_subtitle_no_region)
                empty.visibility = View.GONE
                return@launch
            }
            fetchAndRenderSimilarScans(
                adapter = adapter,
                resolved = resolved,
                pseudoId = pseudoId,
                initialDisease = diseaseKey,
                filterDisease = null,
                speciesScientific = scan.speciesScientific,
                isRu = isRu,
            )
        }
    }

    /**
     * Fetch + render the carousel for one disease filter. ``filterDisease``
     * being non-null means the user tapped a chip; otherwise we use
     * ``initialDisease`` (the current scan's diagnosis) so the server
     * surfaces same-diagnosis rows first. Called both on first render
     * and on every chip tap.
     *
     * The chip row itself is only built on the first call (when
     * ``filterDisease`` is ``null``) — subsequent chip-tap re-fetches
     * leave the chip view tree alone so the user's selection state
     * survives and the row doesn't flicker.
     */
    private fun fetchAndRenderSimilarScans(
        adapter: SimilarScansAdapter,
        resolved: com.plantdiseases.app.util.ResolvedRegion,
        pseudoId: String,
        initialDisease: String,
        filterDisease: String?,
        speciesScientific: String?,
        isRu: Boolean,
    ) {
        val card = binding.cardSimilarScans
        val rv = binding.rvSimilarScans
        val empty = binding.tvSimilarEmpty
        val chips = binding.chipGroupTopDiseases
        val app = application as PlantDiseasesApp
        val effectiveDisease = filterDisease ?: initialDisease
        lifecycleScope.launch {
            val result = app.scanRepository.communityNearby(
                country = resolved.country,
                region = resolved.locality,
                disease = effectiveDisease,
                // Narrow by detected scientific name so a "Powdery
                // mildew on Monstera" scan doesn't render "Powdery
                // mildew on Ficus" thumbnails — different species,
                // different care. Server falls back gracefully when
                // the species bucket is empty.
                species = speciesScientific,
                userPseudoId = pseudoId,
                limit = 12,
            )
            result.onSuccess { response ->
                card.visibility = View.VISIBLE
                val label = resolved.displayLabel
                // When the user filters by chip, restrict the carousel
                // strictly — the server may return same-locality
                // fillers of other diseases when the chip's bucket is
                // sparse, and those would defeat the purpose of the
                // filter.
                val items = if (filterDisease != null) {
                    response.items.filter { it.disease == filterDisease }
                } else {
                    response.items
                }
                val total = response.summary?.last7d ?: items.size

                if (items.isEmpty()) {
                    binding.tvSimilarSubtitle.text = getString(
                        R.string.similar_scans_subtitle_region, label,
                    )
                    empty.text = getString(R.string.similar_scans_empty)
                    empty.visibility = View.VISIBLE
                    rv.visibility = View.GONE
                } else {
                    binding.tvSimilarSubtitle.text = getString(
                        R.string.similar_scans_subtitle, total, label,
                    )
                    rv.visibility = View.VISIBLE
                    empty.visibility = View.GONE
                    adapter.submit(items)
                }
                // Build the chip row only once, on the initial fetch.
                // Chip-tap re-fetches keep the existing chips so the
                // user's selection persists visually.
                if (filterDisease == null && chips.childCount == 0) {
                    renderTopDiseaseChips(
                        chips = chips,
                        top = response.summary?.topDiseases.orEmpty(),
                        isRu = isRu,
                        onChipToggled = { selectedDisease ->
                            fetchAndRenderSimilarScans(
                                adapter = adapter,
                                resolved = resolved,
                                pseudoId = pseudoId,
                                initialDisease = initialDisease,
                                filterDisease = selectedDisease,
                                speciesScientific = speciesScientific,
                                isRu = isRu,
                            )
                        },
                    )
                }
            }.onFailure {
                // Stay hidden on error — never surface a broken card.
                if (filterDisease == null) card.visibility = View.GONE
            }
        }
    }

    /**
     * Render the regional top-diseases chip row. Each chip shows the
     * localised disease label and the number of recent scans for it.
     * Caps at 4 chips so the row stays readable on narrow phones.
     *
     * Chips are checkable; tapping one calls ``onChipToggled`` with
     * the disease key, tapping the same chip again calls it with
     * ``null`` (the "clear filter" signal). ``not_a_plant`` is
     * stripped defensively — the server already drops it, but cached
     * responses from older deployments shouldn't surface a chip
     * for it.
     */
    private fun renderTopDiseaseChips(
        chips: com.google.android.material.chip.ChipGroup,
        top: List<com.plantdiseases.app.data.model.CommunityTopDisease>,
        isRu: Boolean,
        onChipToggled: (String?) -> Unit,
    ) {
        chips.removeAllViews()
        val visible = top
            .asSequence()
            .filter { it.disease != "not_a_plant" }
            .take(4)
            .toList()
        if (visible.size < 2) {
            // One-chip filter is useless — collapse the row entirely
            // so the user isn't presented with a no-op control.
            chips.visibility = View.GONE
            return
        }
        for (entry in visible) {
            val label = classKeyDisplayName(entry.disease, isRu) ?: entry.disease
            val chip = com.google.android.material.chip.Chip(this).apply {
                text = getString(R.string.similar_scans_chip_format, label, entry.count)
                isCheckable = true
                isClickable = true
                tag = entry.disease
                setOnClickListener {
                    val newFilter = if (isChecked) entry.disease else null
                    onChipToggled(newFilter)
                }
            }
            chips.addView(chip)
        }
        chips.visibility = View.VISIBLE
    }

    private fun buildAlternativeDiagnoses(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val allProbs = repository.parseAllProbs(scan.allProbs)
        if (allProbs.isEmpty()) {
            binding.cardAlternatives.visibility = View.GONE
            return
        }

        val top3 = allProbs.entries
            .sortedByDescending { it.value }
            .take(3)

        if (top3.isEmpty()) {
            binding.cardAlternatives.visibility = View.GONE
            return
        }

        binding.cardAlternatives.visibility = View.VISIBLE
        binding.alternativesLayout.removeAllViews()

        top3.forEachIndexed { index, entry ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply {
                    if (index > 0) topMargin = 12.dpToPx()
                }
            }

            val percent = (entry.value * 100).toInt()
            val displayName = classKeyDisplayName(entry.key, isRu) ?: entry.key
            val isMain = index == 0

            val nameRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }

            val nameView = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                text = displayName
                textSize = if (isMain) 14f else 13f
                setTextColor(getColor(if (isMain) R.color.on_surface else R.color.on_surface_secondary))
                if (isMain) typeface = android.graphics.Typeface.DEFAULT_BOLD
            }

            val percentView = TextView(this).apply {
                text = "$percent%"
                textSize = if (isMain) 14f else 13f
                setTextColor(getColor(if (isMain) R.color.primary else R.color.on_surface_secondary))
                if (isMain) typeface = android.graphics.Typeface.DEFAULT_BOLD
            }

            nameRow.addView(nameView)
            nameRow.addView(percentView)

            val progressBar = LinearProgressIndicator(this).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = 4.dpToPx() }
                max = 100
                progress = 0
                trackCornerRadius = 4.dpToPx()
                trackThickness = if (isMain) 8.dpToPx() else 6.dpToPx()
                setIndicatorColor(getColor(if (isMain) R.color.primary else R.color.primary_light))
                trackColor = getColor(R.color.surface_variant)
            }

            val barAnimator = ValueAnimator.ofInt(0, percent).apply {
                duration = 600
                startDelay = (index * 150).toLong()
                interpolator = DecelerateInterpolator(2.5f)
                addUpdateListener { anim ->
                    progressBar.progress = anim.animatedValue as Int
                }
            }
            barAnimator.start()

            row.addView(nameRow)
            row.addView(progressBar)
            binding.alternativesLayout.addView(row)
        }
    }

    private fun shareWithImage(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val shareText = buildString {
            append(if (isRu) "🌿 PlantDiseases — Диагноз\n\n" else "🌿 PlantDiseases — Diagnosis\n\n")
            append(if (isRu) scan.diseaseNameRu else scan.diseaseName)
            append(" (${(scan.confidence * 100).toInt()}%)\n\n")
            append(if (isRu) scan.descriptionRu else scan.description)
            val treatmentList = repository.parseStringList(if (isRu) scan.treatmentRu else scan.treatment)
            if (treatmentList.isNotEmpty()) {
                append("\n\n")
                append(if (isRu) "💊 Лечение:\n" else "💊 Treatment:\n")
                treatmentList.forEachIndexed { i, t -> append("${i + 1}. $t\n") }
            }
        }

        val imageFile = File(scan.imagePath)
        if (!imageFile.exists()) {
            launchShareTextOnly(shareText)
            return
        }

        // Image composition (decode + canvas + JPEG encode) is off the UI thread.
        lifecycleScope.launch {
            val shareFile = try {
                withContext(Dispatchers.IO) { createShareableImage(scan) }
            } catch (e: Exception) {
                Log.w(TAG, "Share image composition failed, falling back to text-only", e)
                null
            }

            if (shareFile == null || isFinishing) {
                if (!isFinishing) launchShareTextOnly(shareText)
                return@launch
            }

            val imageUri = try {
                FileProvider.getUriForFile(this@ResultActivity, "${packageName}.fileprovider", shareFile)
            } catch (e: IllegalArgumentException) {
                Log.w(TAG, "FileProvider URI failed, falling back to text-only", e)
                launchShareTextOnly(shareText)
                return@launch
            }

            val sendIntent = Intent().apply {
                action = Intent.ACTION_SEND
                putExtra(Intent.EXTRA_TEXT, shareText)
                putExtra(Intent.EXTRA_STREAM, imageUri)
                type = "image/jpeg"
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(Intent.createChooser(sendIntent, getString(R.string.share)))
        }
    }

    private fun launchShareTextOnly(shareText: String) {
        val sendIntent = Intent().apply {
            action = Intent.ACTION_SEND
            putExtra(Intent.EXTRA_TEXT, shareText)
            type = "text/plain"
        }
        startActivity(Intent.createChooser(sendIntent, getString(R.string.share)))
    }

    private fun createShareableImage(scan: ScanEntity): File {
        cacheDir.listFiles { file -> file.name.startsWith("share_") }?.forEach { it.delete() }

        val maxDim = 1920
        val boundsOpts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(scan.imagePath, boundsOpts)
        val rawW = boundsOpts.outWidth
        val rawH = boundsOpts.outHeight
        if (rawW <= 0 || rawH <= 0) {
            throw ImageUtils.ImageDecodeException("Source image cannot be decoded: ${scan.imagePath}")
        }
        var inSampleSize = 1
        while (rawW / (inSampleSize * 2) >= maxDim && rawH / (inSampleSize * 2) >= maxDim) {
            inSampleSize *= 2
        }
        val decodeOpts = BitmapFactory.Options().apply {
            this.inSampleSize = inSampleSize
            inMutable = true
        }
        val result = BitmapFactory.decodeFile(scan.imagePath, decodeOpts)
            ?: throw ImageUtils.ImageDecodeException("Failed to decode image: ${scan.imagePath}")

        val finalBitmap = if (maxOf(result.width, result.height) > maxDim) {
            val ratio = maxDim.toFloat() / maxOf(result.width, result.height)
            val scaled = Bitmap.createScaledBitmap(
                result, (result.width * ratio).toInt(), (result.height * ratio).toInt(), true
            )
            result.recycle()
            scaled
        } else {
            result
        }

        // Skip the red highlight when the verdict is healthy.
        val repo = (application as PlantDiseasesApp).scanRepository
        if (!repo.isHealthyVerdict(scan)) {
            val regions = repo.parseRegions(scan.regions)
            val primary = regions.getOrNull(scan.primaryRegionIndex ?: -1)
            if (primary != null) {
                // Bbox coordinates live in the server's decoded image space
                // (the <=1024 px upload the server echoes back), NOT the
                // on-disk original — so scale against the detection dims to
                // match the on-screen overlay. Fall back to the raw dims for
                // legacy scans that predate detection-dimension persistence.
                val detW = scan.detectionImageWidth.takeIf { it > 0 } ?: rawW
                val detH = scan.detectionImageHeight.takeIf { it > 0 } ?: rawH
                val scaleX = finalBitmap.width.toFloat() / detW
                val scaleY = finalBitmap.height.toFloat() / detH
                val canvas = Canvas(finalBitmap)
                val cx = (primary.x + primary.width / 2f) * scaleX
                val cy = (primary.y + primary.height / 2f) * scaleY
                val radius = (primary.width + primary.height) / 4f * ((scaleX + scaleY) / 2f)

                val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG)
                val gradient = android.graphics.RadialGradient(
                    cx, cy, radius.coerceAtLeast(1f),
                    intArrayOf(
                        android.graphics.Color.argb(120, 255, 0, 0),
                        android.graphics.Color.argb(80, 255, 165, 0),
                        android.graphics.Color.argb(40, 255, 255, 0),
                        android.graphics.Color.TRANSPARENT
                    ),
                    floatArrayOf(0f, 0.4f, 0.7f, 1f),
                    android.graphics.Shader.TileMode.CLAMP
                )
                paint.shader = gradient
                canvas.drawCircle(cx, cy, radius, paint)
            }
        }

        val shareFile = File(cacheDir, "share_${System.currentTimeMillis()}.jpg")
        FileOutputStream(shareFile).use { out ->
            finalBitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
        }
        finalBitmap.recycle()
        return shareFile
    }

    private fun buildStepsList(
        container: LinearLayout,
        steps: List<String>,
        bgRes: Int,
        colorRes: Int
    ) {
        container.removeAllViews()
        val color = getColor(colorRes)

        steps.forEachIndexed { index, step ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.TOP
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply {
                    if (index > 0) topMargin = 12.dpToPx()
                }
            }

            val numberView = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(28.dpToPx(), 28.dpToPx())
                setBackgroundResource(bgRes)
                text = "${index + 1}"
                setTextColor(color)
                textSize = 13f
                gravity = Gravity.CENTER
                typeface = android.graphics.Typeface.DEFAULT_BOLD
            }

            val textView = TextView(this).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginStart = 12.dpToPx()
                }
                text = step
                setTextColor(getColor(R.color.on_surface_secondary))
                textSize = 14f
                setLineSpacing(4f.dpToPxF(), 1f)
            }

            row.addView(numberView)
            row.addView(textView)
            container.addView(row)
        }
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
            binding.cardLowConfidence
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

    private fun Int.dpToPx(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics
    ).toInt()

    private fun Float.dpToPxF(): Float = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this, resources.displayMetrics
    )

    override fun onDestroy() {
        skeletonAnimator?.cancel()
        skeletonAnimator = null
        super.onDestroy()
    }

    // ─── Care Plan card (server-side /api/care-plan) ───────────────────────

    private fun buildCarePlanCard(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val container = binding.contentLayout
        val classKey = repository.topClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')

        // Reuse a single card across re-binds.
        val card = carePlanCard ?: CarePlanCardView(this).also { fresh ->
            carePlanCard = fresh
            val lp = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dpToPx() }
            fresh.layoutParams = lp
            // Insert just after the Treatment / Prevention card so the
            // "what's wrong → how to treat → ongoing care" reading order
            // stays intact.
            val anchorIdx = container.indexOfChild(binding.cardTreatmentPrevention)
                .takeIf { it >= 0 } ?: (container.childCount - 1)
            container.addView(fresh, anchorIdx + 1)
        }

        val app = application as PlantDiseasesApp
        card.showLoading(null)

        lifecycleScope.launch {
            val plantSpecies = scan.plantId?.let { pid ->
                val plant = app.plantRepository.getPlant(pid)
                plant?.scientificName?.takeIf { it.isNotBlank() }
                    ?: plant?.commonName?.takeIf { it.isNotBlank() }
                    ?: plant?.species?.takeIf { it.isNotBlank() }
            }
            card.showLoading(plantSpecies)

            val request = CarePlanRequest(
                disease = classKey,
                species = plantSpecies,
                light = "bright_indirect",
                locale = if (isRu) "ru" else "en",
            )
            val result = app.scanRepository.fetchCarePlan(request)
            result.onSuccess { plan ->
                card.bind(
                    plan = plan,
                    speciesDisplay = plantSpecies,
                    showApplyToReminders = scan.plantId != null,
                    onApplyToReminders = { applied ->
                        applyCarePlanToReminders(scan, applied)
                    },
                )
            }.onFailure {
                card.showError(onRetry = { buildCarePlanCard(scan, repository, isRu) })
            }
        }
    }

    private fun applyCarePlanToReminders(
        scan: ScanEntity,
        plan: com.plantdiseases.app.data.model.CarePlanResponse,
    ) {
        val plantId = scan.plantId ?: return
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            val plant = app.plantRepository.getPlant(plantId) ?: return@launch
            com.plantdiseases.app.ui.garden.ReminderScheduleSheet.showWithCarePlanDefaults(
                supportFragmentManager, plant, plan,
            )
            Toast.makeText(
                this@ResultActivity,
                getString(R.string.care_plan_reminders_applied),
                Toast.LENGTH_SHORT,
            ).show()
        }
    }

    // ─── Pet toxicity card ────────────────────────────────────────────────

    private fun buildToxicityCard(scan: ScanEntity, repository: ScanRepository) {
        val card = binding.cardToxicity
        val title = binding.tvToxicityTitle
        val body = binding.tvToxicityBody
        val icon = binding.ivToxicityIcon

        val speciesList = repository.parseToxicSpeciesList(scan.toxicSpeciesList)
        when (scan.toxicitySource) {
            "db" -> if (scan.toxicToPets) {
                card.visibility = View.VISIBLE
                card.setCardBackgroundColor(getColor(R.color.disease_red_bg))
                title.setTextColor(getColor(R.color.disease_red))
                icon.setColorFilter(getColor(R.color.disease_red))
                title.setText(R.string.toxicity_toxic_title)
                body.setTextColor(getColor(R.color.disease_red))
                body.text = getString(
                    R.string.toxicity_toxic_body,
                    speciesList.joinToString(", "),
                )
            } else {
                card.visibility = View.VISIBLE
                card.setCardBackgroundColor(getColor(R.color.healthy_green_bg))
                title.setTextColor(getColor(R.color.healthy_green))
                icon.setColorFilter(getColor(R.color.healthy_green))
                icon.setImageResource(R.drawable.ic_healthy)
                title.setText(R.string.toxicity_safe_title)
                body.setTextColor(getColor(R.color.healthy_green))
                body.setText(R.string.toxicity_safe_body)
            }
            else -> {
                // Unknown source: the server couldn't pin a species
                // (PlantNet wasn't configured / no candidates / the
                // species isn't in our toxicity DB). Showing a big
                // amber "pet safety unconfirmed" card on every scan
                // by default reads as a warning the user can't act
                // on — the previous behaviour. Hide the card and let
                // the user pick a species via the create-plant flow
                // to trigger a real lookup on the next request.
                card.visibility = View.GONE
            }
        }
    }

    // ─── Diagnosis feedback ──────────────────────────────────────────────

    private fun buildFeedbackCard(scan: ScanEntity) {
        val card = binding.cardFeedback
        card.visibility = View.VISIBLE

        // Already-submitted feedback persists across re-opens of the same
        // scan — show the confirmation state and skip the buttons so the
        // user can't double-submit.
        if (isFeedbackAlreadySubmitted(scan.id)) {
            binding.feedbackChoiceRow.visibility = View.GONE
            binding.tvFeedbackStatus.visibility = View.VISIBLE
            binding.tvFeedbackStatus.setText(R.string.feedback_sent)
            return
        }

        binding.tvFeedbackStatus.visibility = View.GONE
        binding.feedbackChoiceRow.visibility = View.VISIBLE
        binding.btnFeedbackYes.isEnabled = true
        binding.btnFeedbackNo.isEnabled = true

        binding.btnFeedbackYes.setOnClickListener {
            Haptics.tick(this)
            submitFeedback(scan, correct = true)
        }
        binding.btnFeedbackNo.setOnClickListener {
            Haptics.tick(this)
            showCorrectionSheet(scan)
        }
    }

    private fun showCorrectionSheet(scan: ScanEntity) {
        val isRu = LocaleHelper.isRussian(this)
        val repository = (application as PlantDiseasesApp).scanRepository
        val predictedKey = repository.topClassKey(scan)
        val predictedDisplay = predictedKey?.let { classKeyDisplayName(it, isRu) }
            ?: (if (isRu) scan.diseaseNameRu else scan.diseaseName)
        FeedbackCorrectionSheet.show(
            supportFragmentManager,
            predictedDisplayName = predictedDisplay,
        ) { correctedClass, reason, note ->
            submitFeedback(
                scan,
                correct = false,
                correctedClass = correctedClass,
                reason = reason,
                note = note,
            )
        }
    }

    private fun submitFeedback(
        scan: ScanEntity,
        correct: Boolean,
        correctedClass: String? = null,
        reason: String? = null,
        note: String? = null,
    ) {
        binding.btnFeedbackYes.isEnabled = false
        binding.btnFeedbackNo.isEnabled = false
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            val request = buildFeedbackRequest(
                scan = scan,
                correct = correct,
                correctedClass = correctedClass,
                reason = reason,
                note = note,
            )
            val result = app.scanRepository.submitFeedback(request)
            result.onSuccess {
                markFeedbackSubmitted(scan.id)
                binding.feedbackChoiceRow.visibility = View.GONE
                binding.tvFeedbackStatus.visibility = View.VISIBLE
                binding.tvFeedbackStatus.setText(R.string.feedback_sent)
                Haptics.success(this@ResultActivity)
                // On negative feedback the original image is high-value
                // for retraining. Best-effort upload: failures are
                // swallowed (the JSON feedback row has already
                // landed). Filename on the server is the SHA-256 of
                // the bytes, matching the ``image_hash`` we already
                // sent in the JSON payload.
                if (!correct) {
                    val scanId = request.scanId
                    val imageFile = java.io.File(scan.imagePath)
                    if (imageFile.exists()) {
                        runCatching {
                            app.scanRepository.uploadFeedbackImage(scanId, imageFile)
                        }
                    }
                }
            }.onFailure { error ->
                binding.btnFeedbackYes.isEnabled = true
                binding.btnFeedbackNo.isEnabled = true
                val msgRes = when (error) {
                    is ScanRepository.RateLimitException -> R.string.feedback_rate_limited
                    else -> R.string.feedback_failed
                }
                Toast.makeText(this@ResultActivity, msgRes, Toast.LENGTH_SHORT).show()
            }
        }
    }

    /**
     * Assemble the full feedback payload from a [ScanEntity] + the
     * user's three correction signals. All metadata is best-effort:
     * when a field is unknown we send null rather than fabricating a
     * value, so server-side metrics don't get polluted by defaults.
     */
    private suspend fun buildFeedbackRequest(
        scan: ScanEntity,
        correct: Boolean,
        correctedClass: String?,
        reason: String?,
        note: String?,
    ): FeedbackRequest {
        val repository = (application as PlantDiseasesApp).scanRepository
        // Prefer the server-issued ``displayedClassKey`` — that's the
        // verdict the user actually saw (post-weak-healthy-reroute).
        // Falling back to argmax(all_probs) only when the column is
        // empty (legacy scans saved before this build, or a server
        // old enough that the field wasn't returned). Without this
        // we'd echo the pre-reroute argmax back as
        // ``predicted_class`` and rot the per-class accuracy
        // metrics on the server side.
        val displayedKey = scan.displayedClassKey?.takeIf { it.isNotBlank() }
        val argmaxKey = repository.topClassKey(scan)
        val predictedKey = displayedKey ?: argmaxKey
        val allProbs = repository.parseAllProbs(scan.allProbs)
        val predictedConfidence = predictedKey
            ?.let { allProbs[it] }
            ?: scan.confidence
        val top3 = allProbs.entries
            .asSequence()
            .sortedByDescending { it.value }
            .take(3)
            .map { TopProb(classKey = it.key, prob = it.value) }
            .toList()
            .takeIf { it.isNotEmpty() }
        val regionCount = repository.parseRegions(scan.regions).size
        val isRu = LocaleHelper.isRussian(this)
        val latencyMs = (System.currentTimeMillis() - scan.timestamp).coerceAtLeast(0L)

        // ``client_scan_id`` is the random UUID we mint at insert time.
        // We never send the local autoincrement primary key.
        val scanId = scan.clientScanId.takeIf { it.isNotBlank() }
            ?: java.util.UUID.randomUUID().toString()

        // SHA-256 of the original on-disk image so the corrections
        // corpus can be joined back to image bytes during retraining
        // without uploading the image itself. Runs off the UI thread;
        // returns null when the file is gone — the server treats the
        // field as optional.
        val imageHashHex = withContext(Dispatchers.IO) {
            ImageUtils.sha256Hex(java.io.File(scan.imagePath))
        }

        // Parse the persisted prediction set (JSON array of class
        // keys, stored at saveScan time) back into a list. Stale
        // gallery rows from before v2 of the local schema won't have
        // it; that's fine — the server treats the field as optional.
        val predictionSet: List<String>? = scan.predictionSet?.let { raw ->
            runCatching {
                com.google.gson.Gson()
                    .fromJson(raw, Array<String>::class.java).toList()
            }.getOrNull()?.takeIf { it.isNotEmpty() }
        }

        return FeedbackRequest(
            scanId = scanId,
            correct = correct,
            correctedClass = correctedClass,
            reason = reason,
            note = note,
            predictedClass = predictedKey,
            predictedConfidence = predictedConfidence,
            top3 = top3,
            modelVersion = SERVER_MODEL_VERSION,
            appVersion = BuildConfig.VERSION_NAME,
            locale = if (isRu) "ru" else "en",
            platform = "android",
            userPseudoId = TelemetryIds.userPseudoId(this),
            regionCount = regionCount,
            primaryRegionIndex = scan.primaryRegionIndex,
            feedbackLatencyMs = latencyMs,
            imageHash = imageHashHex,
            predictionSet = predictionSet,
        )
    }

    private fun classKeyDisplayName(key: String, isRu: Boolean): String? = when (key) {
        // PlantVision v2.2 — 8 in-head classes + 2 deferred
        // (edema, sunburn_or_scorch) accepted on the feedback path.
        "healthy" -> if (isRu) "Здоровое растение" else "Healthy Plant"
        "powdery_mildew" -> if (isRu) "Мучнистая роса" else "Powdery Mildew"
        "gray_mold_botrytis" -> if (isRu) "Серая гниль (ботритис)" else "Gray Mold (Botrytis)"
        "leaf_spot" -> if (isRu) "Пятнистость листьев" else "Leaf Spot"
        // legacy keys from pre-merge models / historical scans:
        "leaf_spot_fungal" -> if (isRu) "Грибковая пятнистость" else "Fungal Leaf Spot"
        "bacterial_leaf_spot" -> if (isRu) "Бактериальная пятнистость" else "Bacterial Leaf Spot"
        "viral_disease" -> if (isRu) "Вирусное заболевание" else "Viral Disease"
        "edema" -> if (isRu) "Эдема (отёчность)" else "Edema (Oedema)"
        "sunburn_or_scorch" -> if (isRu) "Солнечный ожог" else "Sunburn / Light Scorch"
        "leaf_scorch_underwatering" -> if (isRu) "Краевой ожог (недолив)" else "Leaf Scorch (Under-watering)"
        "nutrient_deficiency" -> if (isRu) "Дефицит питания" else "Nutrient Deficiency"
        "not_a_plant" -> if (isRu) "Не растение" else "Not a Plant"
        else -> null
    }

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"
        private const val TAG = "ResultActivity"
        private const val FEEDBACK_PREFS = "plantdiseases_feedback_prefs"
        private const val FEEDBACK_KEY_PREFIX = "scan_"
        // Pinned to the classifier release in server/models/classes.json.
        // When the server exposes model_version in /api/analyze we'll
        // start trusting that value per-scan instead.
        private const val SERVER_MODEL_VERSION = "2.2"
    }

    private fun isFeedbackAlreadySubmitted(scanId: Long): Boolean =
        getSharedPreferences(FEEDBACK_PREFS, MODE_PRIVATE)
            .getBoolean("$FEEDBACK_KEY_PREFIX$scanId", false)

    private fun markFeedbackSubmitted(scanId: Long) {
        getSharedPreferences(FEEDBACK_PREFS, MODE_PRIVATE).edit()
            .putBoolean("$FEEDBACK_KEY_PREFIX$scanId", true)
            .apply()
    }
}
