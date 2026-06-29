package com.plantdiseases.app.ui.garden.detail

import android.content.Intent
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.setPadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.ui.garden.PhotoViewerActivity
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject
import java.io.File

/**
 * Scan timeline tab: the per-scan cards plus the long-press "share this
 * diagnosis" flow (a branded composed image + bilingual caption handed to the
 * OS share sheet).
 */
class DetailTimelineController(
    private val activity: AppCompatActivity,
) : KoinComponent {

    private val plantRepository: PlantRepository by inject()
    private var plantId: Long = -1L

    fun renderTimeline(
        timelineContainer: LinearLayout,
        plantId: Long,
        scans: List<ScanEntity>,
    ) {
        this.plantId = plantId
        timelineContainer.removeAllViews()
        if (scans.isEmpty()) {
            timelineContainer.addView(
                activity.emptyState(
                    R.drawable.illustration_empty_scans,
                    activity.getString(R.string.empty_plant_scans_title),
                    activity.getString(R.string.empty_plant_scans_hint),
                ),
            )
            return
        }
        scans.forEach { scan -> timelineContainer.addView(scanCard(scan)) }
    }

    private fun scanCard(scan: ScanEntity): View {
        val card = activity.baseCard()
        val row = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(14.dp())
        }
        row.addView(
            ImageView(activity).apply {
                layoutParams = LinearLayout.LayoutParams(72.dp(), 72.dp())
                scaleType = ImageView.ScaleType.CENTER_CROP
                Glide.with(activity)
                    .load(File(scan.imagePath))
                    .transform(CenterCrop(), RoundedCorners(16.dp()))
                    .placeholder(R.drawable.ic_plant_placeholder)
                    .into(this)
                isClickable = true
                setOnClickListener {
                    activity.lifecycleScope.launch {
                        val all = plantRepository.getScansForPlant(plantId)
                            .sortedBy { it.timestamp }
                            .map { it.imagePath }
                        val start = all.indexOf(scan.imagePath).coerceAtLeast(0)
                        PhotoViewerActivity.open(activity, all, start)
                    }
                }
            },
        )
        val text = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 12.dp() }
        }
        val isRu = LocaleHelper.isRussian(activity)
        text.addView(
            TextView(activity).apply {
                this.text = if (isRu) scan.diseaseNameRu else scan.diseaseName
                textSize = 16f
                setTextColor(activity.getColor(R.color.on_surface))
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
                maxLines = 2
            },
        )
        text.addView(
            TextView(activity).apply {
                this.text = activity.getString(
                    R.string.timeline_scan_meta,
                    ImageUtils.formatRelativeDate(activity, scan.timestamp),
                    (scan.confidence * 100).toInt(),
                )
                textSize = 12f
                setTextColor(activity.getColor(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 5.dp() }
            },
        )
        row.addView(text)
        card.addView(row)
        card.setOnClickListener {
            activity.startActivity(
                Intent(activity, ResultActivity::class.java)
                    .putExtra(ResultActivity.EXTRA_SCAN_ID, scan.id),
            )
        }
        card.isLongClickable = true
        card.setOnLongClickListener {
            Haptics.medium(activity)
            shareScan(scan)
            true
        }
        return card
    }

    /** Hand a scan off to the OS share sheet — branded image (the
     *  scan photo + verdict overlay rendered into a square PNG) +
     *  bilingual caption. Saves the user the round-trip through
     *  ResultActivity if they just want to send a recent diagnosis
     *  to a friend or a forum. */
    private fun shareScan(scan: ScanEntity) {
        val isRu = LocaleHelper.isRussian(activity)
        val caption = buildString {
            append(activity.getString(R.string.share_diagnosis_header))
            append("\n\n")
            append(if (isRu) scan.diseaseNameRu else scan.diseaseName)
            append(" (")
            append((scan.confidence * 100).toInt())
            append("%)")
        }
        val imageFile = File(scan.imagePath)
        if (!imageFile.exists()) {
            // Image already gone — share text only.
            activity.startActivity(
                Intent.createChooser(
                    Intent(Intent.ACTION_SEND).apply {
                        type = "text/plain"
                        putExtra(Intent.EXTRA_TEXT, caption)
                    },
                    activity.getString(R.string.share_scan_chooser),
                ),
            )
            return
        }
        // Render the branded share image off the UI thread.
        activity.lifecycleScope.launch {
            val composed = withContext(Dispatchers.IO) {
                runCatching { composeShareImage(scan, isRu) }.getOrNull()
            }
            if (composed == null) {
                activity.startActivity(
                    Intent.createChooser(
                        Intent(Intent.ACTION_SEND).apply {
                            type = "image/jpeg"
                            putExtra(Intent.EXTRA_TEXT, caption)
                            putExtra(
                                Intent.EXTRA_STREAM,
                                androidx.core.content.FileProvider.getUriForFile(
                                    activity,
                                    "${activity.packageName}.fileprovider",
                                    imageFile,
                                ),
                            )
                            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        },
                        activity.getString(R.string.share_scan_chooser),
                    ),
                )
                return@launch
            }
            val uri = androidx.core.content.FileProvider.getUriForFile(
                activity,
                "${activity.packageName}.fileprovider",
                composed,
            )
            activity.startActivity(
                Intent.createChooser(
                    Intent(Intent.ACTION_SEND).apply {
                        type = "image/jpeg"
                        putExtra(Intent.EXTRA_TEXT, caption)
                        putExtra(Intent.EXTRA_STREAM, uri)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    },
                    activity.getString(R.string.share_scan_chooser),
                ),
            )
        }
    }

    /** Compose a 1080×1350 share image: scan photo on top (square
     *  letterbox-fit), verdict + confidence + app brand overlaid at
     *  the bottom. Written to cache as JPEG and handed back. */
    private fun composeShareImage(scan: ScanEntity, isRu: Boolean): File {
        val w = 1080
        val photoH = 1080
        val footerH = 270
        val totalH = photoH + footerH
        val bitmap = android.graphics.Bitmap.createBitmap(
            w,
            totalH,
            android.graphics.Bitmap.Config.ARGB_8888,
        )
        val canvas = android.graphics.Canvas(bitmap)
        canvas.drawColor(activity.getColor(R.color.surface))
        // Photo: load + center-crop into the top square.
        val source = android.graphics.BitmapFactory.decodeFile(scan.imagePath)
        if (source != null) {
            val srcSide = minOf(source.width, source.height)
            val sx = (source.width - srcSide) / 2
            val sy = (source.height - srcSide) / 2
            val srcRect = android.graphics.Rect(sx, sy, sx + srcSide, sy + srcSide)
            val dstRect = android.graphics.Rect(0, 0, w, photoH)
            val paint = android.graphics.Paint(android.graphics.Paint.FILTER_BITMAP_FLAG)
            canvas.drawBitmap(source, srcRect, dstRect, paint)
            source.recycle()
        }
        // Footer: solid surface + verdict text.
        val footerPaint = android.graphics.Paint().apply {
            color = activity.getColor(R.color.surface)
        }
        canvas.drawRect(0f, photoH.toFloat(), w.toFloat(), totalH.toFloat(), footerPaint)
        val titlePaint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            color = activity.getColor(R.color.on_surface)
            textSize = 64f
            typeface = android.graphics.Typeface.create(
                "sans-serif-medium",
                android.graphics.Typeface.NORMAL,
            )
        }
        val subPaint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            color = activity.getColor(R.color.on_surface_secondary)
            textSize = 38f
        }
        val brandPaint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            color = activity.getColor(R.color.primary)
            textSize = 32f
            typeface = android.graphics.Typeface.create(
                "sans-serif-medium",
                android.graphics.Typeface.NORMAL,
            )
        }
        val title = if (isRu) scan.diseaseNameRu else scan.diseaseName
        val confidenceStr = activity.getString(
            R.string.share_confidence_format,
            (scan.confidence * 100).toInt(),
        )
        canvas.drawText(title, 48f, photoH + 90f, titlePaint)
        canvas.drawText(confidenceStr, 48f, photoH + 150f, subPaint)
        canvas.drawText("🌿 PlantDiseases", 48f, photoH + 230f, brandPaint)

        val outFile = File(activity.cacheDir, "share_scan_${scan.id}_${System.currentTimeMillis()}.jpg")
        outFile.outputStream().use {
            bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 92, it)
        }
        bitmap.recycle()
        return outFile
    }

    private fun Int.dp(): Int = dp(activity)
}
