package com.plantdiseases.app.ui.garden.detail

import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.ui.garden.PhotoCompareActivity
import com.plantdiseases.app.ui.garden.PhotoViewerActivity

/**
 * Photos tab: every scan image + every PHOTO journal entry as a chronological
 * 3-column grid, plus the long-press before/after compare picker and the
 * hero-cover photo viewer.
 */
class DetailPhotosController(
    private val activity: AppCompatActivity,
) {

    /** Render the Photos tab: every scan image + every PHOTO journal
     *  entry as a chronological 3-column grid. Tap → open the
     *  full-screen PhotoViewerActivity at that index. Long-press →
     *  pick a second photo and launch PhotoCompareActivity. */
    fun renderPhotos(
        photosContainer: LinearLayout,
        scans: List<ScanEntity>,
        journal: List<PlantJournalEntity>,
    ) {
        photosContainer.removeAllViews()
        // Combine scan photos + journal PHOTO entries, oldest first.
        data class PhotoItem(val uri: String, val timestamp: Long)
        val items = mutableListOf<PhotoItem>()
        scans.forEach { items.add(PhotoItem(it.imagePath, it.timestamp)) }
        journal.filter { it.type == PlantRepository.JournalType.PHOTO.value }
            .forEach { e ->
                e.photoUri?.takeIf { it.isNotBlank() }?.let {
                    items.add(PhotoItem(it, e.timestamp))
                }
            }
        val ordered = items.sortedBy { it.timestamp }.distinctBy { it.uri }
        if (ordered.isEmpty()) {
            photosContainer.addView(
                activity.emptyState(
                    R.drawable.illustration_empty_reminders,
                    activity.getString(R.string.plant_photos_empty_title),
                    activity.getString(R.string.plant_photos_empty_hint),
                ),
            )
            return
        }
        // 3-column grid via plain LinearLayout rows. Each cell is a
        // square ImageView with rounded corners. Tap → viewer at idx.
        // Hoist the URI list out of the per-cell click listener
        // (otherwise we allocate a fresh List<String> for every cell
        // ON EVERY TAP — N^2 garbage on a grid that's already N
        // images).
        val orderedUris = ordered.map { it.uri }
        val cellsPerRow = 3
        val rowGap = 6.dp()
        val cellGap = 6.dp()
        val cellSize = (activity.resources.displayMetrics.widthPixels / cellsPerRow) - 18.dp()
        var rowView: LinearLayout? = null
        ordered.forEachIndexed { idx, item ->
            val col = idx % cellsPerRow
            if (col == 0) {
                rowView = LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { if (idx > 0) topMargin = rowGap }
                }
                photosContainer.addView(rowView)
            }
            val cell = ImageView(activity).apply {
                layoutParams = LinearLayout.LayoutParams(cellSize, cellSize).apply {
                    if (col > 0) marginStart = cellGap
                }
                scaleType = ImageView.ScaleType.CENTER_CROP
                clipToOutline = true
                Glide.with(activity)
                    .load(item.uri)
                    .transform(CenterCrop(), RoundedCorners(12.dp()))
                    .into(this)
                setOnClickListener {
                    PhotoViewerActivity.open(activity, orderedUris, idx)
                }
                setOnLongClickListener {
                    pickCompareTarget(item.uri, orderedUris)
                    true
                }
            }
            rowView?.addView(cell)
        }
    }

    /** Show a chooser for the second photo of a before/after compare,
     *  then launch [PhotoCompareActivity]. The first photo is the
     *  long-pressed one; the second is picked from the same chronologic
     *  ordering shown in the grid. */
    private fun pickCompareTarget(firstUri: String, allUris: List<String>) {
        val candidates = allUris.filter { it != firstUri }
        if (candidates.isEmpty()) {
            Toast.makeText(
                activity,
                R.string.photo_compare_need_two,
                Toast.LENGTH_SHORT,
            ).show()
            return
        }
        val labels = candidates.mapIndexed { i, _ -> activity.getString(R.string.photo_compare_pick_item, i + 1) }
            .toTypedArray()
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.photo_compare_pick_title)
            .setItems(labels) { _, which ->
                PhotoCompareActivity.open(
                    activity,
                    firstUri,
                    candidates[which],
                )
            }
            .show()
    }

    /** Open the viewer on the hero cover (the cover-photo if set, else
     *  the most recent scan). When the plant has multiple scans we
     *  hand the viewer the whole scan image list so the user can
     *  swipe through them as a growth gallery. */
    fun openHeroPhotoViewer(plant: PlantEntity, scans: List<ScanEntity>) {
        if (scans.isEmpty() && plant.coverPhotoUri == null) return
        val scanImages = scans.sortedBy { it.timestamp }.map { it.imagePath }
        val ordered = listOfNotNull(plant.coverPhotoUri) +
            scanImages.filter { it != plant.coverPhotoUri }
        val deduped = ordered.distinct()
        if (deduped.isEmpty()) return
        PhotoViewerActivity.open(activity, deduped, 0)
    }

    private fun Int.dp(): Int = dp(activity)
}
