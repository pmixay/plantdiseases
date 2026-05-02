package com.plantdiseases.app.ui.result

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.net.Uri
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.text.InputType
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.animation.ValueAnimator
import android.view.animation.DecelerateInterpolator
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.tabs.TabLayout
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.SpeciesCandidate
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.util.Locale

class ResultActivity : AppCompatActivity() {

    private lateinit var binding: ActivityResultBinding

    private var scaleFactor = 1.0f
    private lateinit var scaleGestureDetector: ScaleGestureDetector
    private var skeletonAnimator: ValueAnimator? = null

    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var carePlanCard: CarePlanCardView? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityResultBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val scanId = intent.getLongExtra(EXTRA_SCAN_ID, -1)
        if (scanId == -1L) {
            finish()
            return
        }

        binding.toolbar.setNavigationOnClickListener { finish() }
        setupToolbarMenu()
        setupPinchToZoom()
        setupStickyHeader()
        initTts()
        loadResult(scanId)
    }

    private var toolbarSpeakItem: android.view.MenuItem? = null
    private var pendingScanForToolbar: ScanEntity? = null

    private fun setupToolbarMenu() {
        binding.toolbar.inflateMenu(R.menu.result_toolbar_menu)
        toolbarSpeakItem = binding.toolbar.menu.findItem(R.id.action_speak)
        binding.toolbar.setOnMenuItemClickListener { item ->
            val scan = pendingScanForToolbar ?: return@setOnMenuItemClickListener false
            when (item.itemId) {
                R.id.action_speak -> {
                    onSpeakAction(scan)
                    true
                }
                R.id.action_delete -> {
                    onDeleteAction(scan)
                    true
                }
                else -> false
            }
        }
    }

    private fun onSpeakAction(scan: ScanEntity) {
        if (!ttsReady) {
            Toast.makeText(this, R.string.speak_unavailable, Toast.LENGTH_SHORT).show()
            return
        }
        Haptics.tick(this)
        if (tts?.isSpeaking == true) {
            tts?.stop()
            updateSpeakIcon(speaking = false)
        } else if (speakResult(scan, LocaleHelper.isRussian(this))) {
            updateSpeakIcon(speaking = true)
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

    private fun updateSpeakIcon(speaking: Boolean) {
        toolbarSpeakItem?.let { item ->
            item.setIcon(if (speaking) R.drawable.ic_close else R.drawable.ic_speak)
            item.title = getString(if (speaking) R.string.speak_stop else R.string.speak_read_aloud)
        }
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
        val animator = ValueAnimator.ofFloat(0.3f, 0.7f).apply {
            duration = 800
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener { anim ->
                val alpha = anim.animatedValue as Float
                for (i in 0 until binding.skeletonLayout.childCount) {
                    binding.skeletonLayout.getChildAt(i).alpha = alpha
                }
            }
        }
        skeletonAnimator = animator
        animator.start()
    }

    private fun displayResult(scan: ScanEntity, repository: ScanRepository) {
        val isRu = LocaleHelper.isRussian(this)
        val isNotAPlant = isNotAPlantScan(scan, repository)
        // Use the persisted classifier top-class as the source of truth
        // for the status badge: as long as the diagnosis text says
        // "Healthy Plant", the badge must agree. Falls back to the
        // server's `is_healthy` boolean for legacy rows that pre-date
        // `all_probs` persistence.
        val topClassKey = repository.parseAllProbs(scan.allProbs)
            .entries.maxByOrNull { it.value }?.key
        val isHealthyVerdict = when (topClassKey) {
            "healthy" -> true
            null -> scan.isHealthy
            else -> false
        }
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
            val regions = repository.parseRegions(scan.regions)
            if (!isNotAPlant && !isHealthyVerdict && regions.isNotEmpty()) {
                val origBounds = ImageUtils.getImageDimensions(scan.imagePath)
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
                setupTreatmentPreventionTabs(scan, repository, isRu)
                buildCarePlanCard(scan, repository, isRu)
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

            // Animate cards from bottom
            animateCardsIn()

            // Toolbar overflow menu owns the read-aloud / delete actions.
            pendingScanForToolbar = scan
            updateSpeakIcon(speaking = false)
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

            val labels = plants.map { summary ->
                summary.plant.nickname?.takeIf { it.isNotBlank() }
                    ?: summary.plant.name
            }.toTypedArray()

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
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 8.dpToPx(), 0, 0)
        }
        val name = resultInputField(getString(R.string.plant_name), scan.diseaseName)
        val species = resultInputField(getString(R.string.plant_species), null)
        val nickname = resultInputField(getString(R.string.plant_nickname), null)

        // PlantNet species suggestion strip
        var pickedScientific: String? = null
        var pickedCommon: String? = null
        val suggestionStatus = TextView(this).apply {
            text = getString(R.string.species_suggest_hint)
            textSize = 12f
            setTextColor(getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dpToPx(); bottomMargin = 8.dpToPx() }
        }
        val suggestBtn = com.google.android.material.button.MaterialButton(
            this, null, com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = getString(R.string.species_suggest_button)
            setIconResource(R.drawable.ic_scan)
            iconTint = android.content.res.ColorStateList.valueOf(getColor(R.color.primary))
            setTextColor(getColor(R.color.primary))
            strokeColor = android.content.res.ColorStateList.valueOf(getColor(R.color.primary))
            cornerRadius = 14.dpToPx()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 12.dpToPx() }
        }
        val candidateChips = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 8.dpToPx() }
        }

        suggestBtn.setOnClickListener {
            val app = application as PlantDiseasesApp
            val imageFile = File(scan.imagePath)
            if (!imageFile.exists()) {
                suggestionStatus.text = getString(R.string.species_suggest_unavailable_body)
                return@setOnClickListener
            }
            suggestBtn.isEnabled = false
            suggestionStatus.text = getString(R.string.species_suggest_loading)
            candidateChips.removeAllViews()
            candidateChips.visibility = View.GONE
            lifecycleScope.launch {
                val res = app.scanRepository.identifySpecies(imageFile)
                suggestBtn.isEnabled = true
                res.onSuccess { id ->
                    if (!id.available || id.results.isEmpty()) {
                        suggestionStatus.text = if (id.results.isEmpty() && id.available)
                            getString(R.string.species_suggest_no_results)
                        else
                            getString(R.string.species_suggest_unavailable_body)
                        return@onSuccess
                    }
                    suggestionStatus.text = getString(R.string.species_suggest_title)
                    id.results.take(3).forEach { candidate ->
                        candidateChips.addView(speciesChip(candidate) { picked ->
                            pickedScientific = picked.scientificName
                            pickedCommon = picked.displayCommonName
                            val display = picked.displayCommonName
                                ?: picked.scientificName
                                ?: getString(R.string.species_suggest_unknown_name)
                            species.second.setText(display)
                            // Auto-fill plant name with the friendly common
                            // name if the user hasn't typed anything yet.
                            if (name.second.text.isNullOrBlank() ||
                                name.second.text?.toString() == scan.diseaseName) {
                                name.second.setText(display)
                            }
                        })
                    }
                    candidateChips.visibility = View.VISIBLE
                }.onFailure {
                    suggestionStatus.text = getString(R.string.species_suggest_unavailable_body)
                }
            }
        }

        container.addView(name.first)
        container.addView(species.first)
        container.addView(nickname.first)
        container.addView(suggestionStatus)
        container.addView(candidateChips)
        container.addView(suggestBtn)

        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.create_new_plant)
            .setView(container)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .show()

        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val plantName = name.second.text?.toString()?.trim().orEmpty()
            if (plantName.isEmpty()) {
                name.first.error = getString(R.string.plant_name_required)
                return@setOnClickListener
            }
            val app = application as PlantDiseasesApp
            lifecycleScope.launch {
                val plantId = app.plantRepository.savePlant(
                    name = plantName,
                    species = species.second.text?.toString(),
                    nickname = nickname.second.text?.toString(),
                    scientificName = pickedScientific,
                    commonName = pickedCommon,
                )
                app.plantRepository.assignScanToPlant(scan.id, plantId)
                binding.btnAddToGarden.text = getString(R.string.added_to_garden)
                Toast.makeText(
                    this@ResultActivity,
                    getString(R.string.scan_added_to_garden),
                    Toast.LENGTH_SHORT,
                ).show()
                dialog.dismiss()
            }
        }
    }

    /** Single tap-to-pick chip in the species-suggestion list. */
    private fun speciesChip(
        candidate: SpeciesCandidate,
        onPick: (SpeciesCandidate) -> Unit,
    ): View {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = getDrawable(R.drawable.bg_species_chip)
            setPadding(12.dpToPx(), 10.dpToPx(), 12.dpToPx(), 10.dpToPx())
            isClickable = true
            isFocusable = true
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 6.dpToPx() }
            setOnClickListener { onPick(candidate) }
        }
        val text = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f
            )
        }
        text.addView(TextView(this).apply {
            // Qualify with `this.text` — the enclosing scope binds `text`
            // to the outer LinearLayout val and would otherwise win.
            this.text = candidate.displayCommonName
                ?: candidate.scientificName
                ?: getString(R.string.species_suggest_unknown_name)
            textSize = 14f
            setTextColor(getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        candidate.scientificName?.let { sci ->
            if (sci != (candidate.displayCommonName ?: "")) {
                text.addView(TextView(this).apply {
                    this.text = sci
                    textSize = 12f
                    setTextColor(getColor(R.color.on_surface_secondary))
                    setTypeface(typeface, android.graphics.Typeface.ITALIC)
                    maxLines = 1
                    ellipsize = android.text.TextUtils.TruncateAt.END
                })
            }
        }
        row.addView(text)
        row.addView(TextView(this).apply {
            this.text = getString(
                R.string.species_suggest_match,
                (candidate.score * 100).toInt().coerceIn(0, 100)
            )
            textSize = 12f
            setTextColor(getColor(R.color.primary))
            typeface = android.graphics.Typeface.create("sans-serif-medium",
                android.graphics.Typeface.NORMAL)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 10.dpToPx() }
        })
        return row
    }

    private fun resultInputField(
        hint: String,
        value: String?,
    ): Pair<TextInputLayout, TextInputEditText> {
        val input = TextInputEditText(this).apply {
            setText(value.orEmpty())
            inputType = InputType.TYPE_CLASS_TEXT
            setSingleLine(true)
        }
        val layout = TextInputLayout(this).apply {
            this.hint = hint
            addView(input)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 10.dpToPx() }
        }
        return layout to input
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

        // A detector/classifier mismatch is the more actionable flag —
        // it means the model disagreed with itself — so prefer it over
        // the softer "uncertain_healthy".
        val (titleRes, bodyRes) = when {
            "detector_classifier_mismatch" in warnings ->
                R.string.warning_detector_mismatch_title to
                    R.string.warning_detector_mismatch
            "uncertain_healthy" in warnings ->
                R.string.warning_uncertain_healthy_title to
                    R.string.warning_uncertain_healthy
            else -> {
                binding.cardPipelineWarning.visibility = View.GONE
                return
            }
        }

        binding.cardPipelineWarning.visibility = View.VISIBLE
        binding.tvPipelineWarningTitle.setText(titleRes)
        binding.tvPipelineWarningBody.setText(bodyRes)
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

        val diseaseNames = mapOf(
            // PlantVision v2.0 — 11 classes
            "healthy" to (if (isRu) "Здоровое растение" else "Healthy Plant"),
            "powdery_mildew" to (if (isRu) "Мучнистая роса" else "Powdery Mildew"),
            "gray_mold_botrytis" to (if (isRu) "Серая гниль (ботритис)" else "Gray Mold (Botrytis)"),
            "leaf_spot_fungal" to (if (isRu) "Грибковая пятнистость" else "Fungal Leaf Spot"),
            "bacterial_leaf_spot" to (if (isRu) "Бактериальная пятнистость" else "Bacterial Leaf Spot"),
            "viral_disease" to (if (isRu) "Вирусное заболевание" else "Viral Disease"),
            "edema" to (if (isRu) "Эдема (отёчность)" else "Edema (Oedema)"),
            "sunburn_or_scorch" to (if (isRu) "Солнечный ожог" else "Sunburn / Light Scorch"),
            "leaf_scorch_underwatering" to (if (isRu) "Краевой ожог (недолив)" else "Leaf Scorch (Under-watering)"),
            "nutrient_deficiency" to (if (isRu) "Дефицит питания" else "Nutrient Deficiency"),
            "not_a_plant" to (if (isRu) "Не растение" else "Not a Plant"),
        )

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
            val displayName = diseaseNames[entry.key] ?: entry.key
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

    /**
     * NAP detection. Two redundant signals:
     * 1. The classifier's top-class key (`not_a_plant`) read from the
     *    persisted `all_probs` JSON. This is locale-independent and the
     *    canonical signal — it survives renaming the displayed disease.
     * 2. The displayed disease name (EN or RU) as a fallback for legacy
     *    rows from before `all_probs` was persisted.
     *
     * We accept either signal. If the persisted top class is missing
     * (older scan without `all_probs`), we still recognise NAP via the
     * stored localized name.
     */
    private fun isNotAPlantScan(scan: ScanEntity, repository: ScanRepository): Boolean {
        val topKey = repository.parseAllProbs(scan.allProbs)
            .entries.maxByOrNull { it.value }?.key
        if (topKey == "not_a_plant") return true
        return scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
            scan.diseaseNameRu == "Не растение"
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

        // Skip the red highlight when the verdict is healthy or NAP.
        // Use the persisted top-class to be consistent with the badge
        // routing in `displayResult`; fall back to the legacy boolean.
        val repo = (application as PlantDiseasesApp).scanRepository
        val topClassKey = repo.parseAllProbs(scan.allProbs)
            .entries.maxByOrNull { it.value }?.key
        val isHealthyVerdict = when (topClassKey) {
            "healthy" -> true
            null -> scan.isHealthy
            else -> false
        }
        if (!isHealthyVerdict) {
            val regions = repo.parseRegions(scan.regions)
            val primary = regions.getOrNull(scan.primaryRegionIndex ?: -1)
            if (primary != null) {
                val scaleX = finalBitmap.width.toFloat() / rawW
                val scaleY = finalBitmap.height.toFloat() / rawH
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

    // ─── TTS ──────────────────────────────────────────────────────────────────

    private fun initTts() {
        tts = TextToSpeech(this) { status ->
            ttsReady = (status == TextToSpeech.SUCCESS)
            // Hook the progress listener once we know TTS is alive so the
            // toolbar speak icon can flip back to the play state when
            // playback ends or errors out.
            if (ttsReady) {
                tts?.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {}
                    override fun onDone(utteranceId: String?) {
                        runOnUiThread { updateSpeakIcon(speaking = false) }
                    }
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String?) {
                        runOnUiThread { updateSpeakIcon(speaking = false) }
                    }
                })
            }
        }
    }

    private fun speakResult(scan: ScanEntity, isRu: Boolean): Boolean {
        val repository = (application as PlantDiseasesApp).scanRepository
        val diseaseName = if (isRu) scan.diseaseNameRu else scan.diseaseName
        val description = if (isRu) scan.descriptionRu else scan.description
        val steps = repository.parseStringList(
            if (isRu) scan.treatmentRu else scan.treatment
        ).take(3)

        val text = buildString {
            append(diseaseName)
            append(". ")
            append(description)
            if (steps.isNotEmpty()) {
                append(". ")
                if (isRu) append("Лечение. ") else append("Treatment. ")
                steps.forEachIndexed { i, step ->
                    append("${i + 1}. $step. ")
                }
            }
        }

        val locale = if (isRu) Locale("ru") else Locale.ENGLISH
        val availability = tts?.setLanguage(locale) ?: TextToSpeech.LANG_MISSING_DATA
        if (availability == TextToSpeech.LANG_MISSING_DATA ||
            availability == TextToSpeech.LANG_NOT_SUPPORTED
        ) {
            Toast.makeText(this, R.string.speak_unavailable, Toast.LENGTH_SHORT).show()
            updateSpeakIcon(speaking = false)
            return false
        }
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "result")
        return true
    }

    override fun onPause() {
        super.onPause()
        tts?.stop()
        updateSpeakIcon(speaking = false)
    }

    override fun onDestroy() {
        skeletonAnimator?.cancel()
        skeletonAnimator = null
        tts?.shutdown()
        tts = null
        super.onDestroy()
    }

    // ─── Care Plan card (server-side /api/care-plan) ───────────────────────

    private fun buildCarePlanCard(scan: ScanEntity, repository: ScanRepository, isRu: Boolean) {
        val container = binding.contentLayout
        val classKey = repository.parseAllProbs(scan.allProbs)
            .entries.maxByOrNull { it.value }?.key
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

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"
        private const val TAG = "ResultActivity"
    }
}
