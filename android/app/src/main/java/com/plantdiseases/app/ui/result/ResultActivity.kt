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
import android.animation.ValueAnimator
import android.view.animation.DecelerateInterpolator
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.tabs.TabLayout
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
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
        setupPinchToZoom()
        setupStickyHeader()
        loadResult(scanId)
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
        val diseaseName = if (isRu) scan.diseaseNameRu else scan.diseaseName

        binding.apply {
            // Image
            Glide.with(this@ResultActivity)
                .load(File(scan.imagePath))
                .transform(CenterCrop(), RoundedCorners(24))
                .into(ivPlant)

            // Heatmap overlay for diseased plants
            if (!scan.isHealthy) {
                if (scan.regionX != null && scan.regionY != null &&
                    scan.regionWidth != null && scan.regionHeight != null) {
                    val origBounds = ImageUtils.getImageDimensions(scan.imagePath)
                    heatmapOverlay.setDetectionRegion(
                        scan.regionX, scan.regionY,
                        scan.regionWidth, scan.regionHeight,
                        origBounds.first, origBounds.second
                    )
                }
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
            tvConfidence.text = getString(R.string.confidence_format, confidencePercent)
            tvStickyConfidence.text = getString(R.string.confidence_format, confidencePercent)

            // Animate confidence bar with easeOutCubic
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

            if (scan.isHealthy) {
                cardStatus.setCardBackgroundColor(getColor(R.color.healthy_green_bg))
                tvStatusLabel.text = getString(R.string.status_healthy)
                tvStatusLabel.setTextColor(getColor(R.color.healthy_green))
                ivStatusIcon.setImageResource(R.drawable.ic_healthy)
            } else {
                cardStatus.setCardBackgroundColor(getColor(R.color.disease_red_bg))
                tvStatusLabel.text = getString(R.string.status_disease)
                tvStatusLabel.setTextColor(getColor(R.color.disease_red))
                ivStatusIcon.setImageResource(R.drawable.ic_disease)
            }

            // Description
            tvDescription.text = if (isRu) scan.descriptionRu else scan.description

            // Top-3 alternative diagnoses
            buildAlternativeDiagnoses(scan, repository, isRu)

            setupTreatmentPreventionTabs(scan, repository, isRu)

            // Date
            tvDate.text = ImageUtils.formatTimestamp(scan.timestamp)

            btnNewScan.setOnClickListener { finish() }

            btnShare.setOnClickListener {
                shareWithImage(scan, repository, isRu)
            }

            // Low confidence
            if (scan.confidence < 0.5f && !scan.isHealthy) {
                cardLowConfidence.visibility = View.VISIBLE
                btnRetryAnalysis.visibility = View.VISIBLE
            } else {
                cardLowConfidence.visibility = View.GONE
                btnRetryAnalysis.visibility = View.GONE
            }

            btnRetryAnalysis.setOnClickListener {
                val intent = Intent(this@ResultActivity, AnalysisActivity::class.java).apply {
                    putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, scan.imagePath)
                }
                startActivity(intent)
                finish()
            }

            btnDelete.setOnClickListener {
                MaterialAlertDialogBuilder(this@ResultActivity)
                    .setTitle(R.string.delete_scan)
                    .setMessage(R.string.delete_scan_confirm)
                    .setPositiveButton(R.string.delete) { _, _ ->
                        val appInstance = application as PlantDiseasesApp
                        lifecycleScope.launch {
                            try { File(scan.imagePath).delete() } catch (_: Exception) {}
                            appInstance.scanRepository.deleteScan(scan.id)
                            finish()
                        }
                    }
                    .setNegativeButton(R.string.cancel, null)
                    .show()
            }

            // Animate cards from bottom
            animateCardsIn()
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
            "healthy" to (if (isRu) "Здоровое растение" else "Healthy Plant"),
            "bacterial_spot" to (if (isRu) "Бактериальная пятнистость" else "Bacterial Spot"),
            "early_blight" to (if (isRu) "Ранний фитофтороз" else "Early Blight"),
            "late_blight" to (if (isRu) "Фитофтороз" else "Late Blight"),
            "leaf_mold" to (if (isRu) "Листовая плесень" else "Leaf Mold"),
            "septoria_leaf_spot" to (if (isRu) "Септориоз" else "Septoria Leaf Spot"),
            "spider_mites" to (if (isRu) "Паутинный клещ" else "Spider Mites"),
            "target_spot" to (if (isRu) "Мишеневидная пятнистость" else "Target Spot"),
            "mosaic_virus" to (if (isRu) "Мозаичный вирус" else "Mosaic Virus"),
            "yellow_leaf_curl" to (if (isRu) "Жёлтое скручивание" else "Yellow Leaf Curl"),
            "powdery_mildew" to (if (isRu) "Мучнистая роса" else "Powdery Mildew"),
            "rust" to (if (isRu) "Ржавчина" else "Rust"),
            "root_rot" to (if (isRu) "Корневая гниль" else "Root Rot"),
            "anthracnose" to (if (isRu) "Антракноз" else "Anthracnose"),
            "botrytis" to (if (isRu) "Серая гниль" else "Gray Mold")
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

        if (!scan.isHealthy && scan.regionX != null && scan.regionY != null &&
            scan.regionWidth != null && scan.regionHeight != null) {
            val scaleX = finalBitmap.width.toFloat() / rawW
            val scaleY = finalBitmap.height.toFloat() / rawH
            val canvas = Canvas(finalBitmap)
            val cx = (scan.regionX + scan.regionWidth / 2f) * scaleX
            val cy = (scan.regionY + scan.regionHeight / 2f) * scaleY
            val radius = (scan.regionWidth + scan.regionHeight) / 4f * ((scaleX + scaleY) / 2f)

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

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"
        private const val TAG = "ResultActivity"
    }
}
