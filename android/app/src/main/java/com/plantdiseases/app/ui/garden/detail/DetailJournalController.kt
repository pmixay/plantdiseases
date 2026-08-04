package com.plantdiseases.app.ui.garden.detail

import android.net.Uri
import android.text.InputFilter
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.setPadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DecodeFormat
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantJournalEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.ui.garden.PhotoViewerActivity
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import kotlinx.coroutines.launch

/**
 * Journal tab: the per-entry cards, their long-press context menu, and the
 * edit / delete dialogs and full-screen photo viewer they open.
 */
class DetailJournalController(
    private val activity: AppCompatActivity,
    private val plantRepository: PlantRepository,
    private val onReload: () -> Unit,
) {

    private var plantId: Long = -1L

    fun renderJournal(
        journalContainer: LinearLayout,
        plantId: Long,
        entries: List<PlantJournalEntity>,
    ) {
        this.plantId = plantId
        journalContainer.removeAllViews()
        if (entries.isEmpty()) {
            journalContainer.addView(
                activity.emptyState(
                    R.drawable.illustration_empty_reminders,
                    activity.getString(R.string.empty_journal_title),
                    activity.getString(R.string.empty_journal_hint),
                ),
            )
            return
        }
        entries.forEach { entry -> journalContainer.addView(journalCard(entry)) }
    }

    private fun journalCard(entry: PlantJournalEntity): View {
        val card = activity.baseCard()
        val row = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.TOP
            setPadding(14.dp())
        }
        row.addView(
            FrameLayout(activity).apply {
                setBackgroundResource(R.drawable.bg_icon_circle)
                layoutParams = LinearLayout.LayoutParams(46.dp(), 46.dp())
                addView(
                    ImageView(activity).apply {
                        setImageResource(
                            when (PlantRepository.JournalType.from(entry.type)) {
                                PlantRepository.JournalType.NOTE -> R.drawable.ic_care
                                PlantRepository.JournalType.PHOTO -> R.drawable.ic_gallery
                                PlantRepository.JournalType.WATERING -> R.drawable.ic_watering
                                PlantRepository.JournalType.FERTILISING -> R.drawable.ic_treatment
                                PlantRepository.JournalType.REPOTTING -> R.drawable.ic_garden
                                PlantRepository.JournalType.PRUNING -> R.drawable.ic_note_pencil
                                PlantRepository.JournalType.MISTING -> R.drawable.ic_warning
                            },
                        )
                        setColorFilter(activity.getColor(R.color.primary))
                        layoutParams = FrameLayout.LayoutParams(24.dp(), 24.dp(), Gravity.CENTER)
                    },
                )
            },
        )
        val text = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 12.dp() }
        }
        text.addView(
            TextView(activity).apply {
                this.text = when (PlantRepository.JournalType.from(entry.type)) {
                    PlantRepository.JournalType.NOTE -> activity.getString(R.string.note_entry)
                    PlantRepository.JournalType.PHOTO -> activity.getString(R.string.photo_entry)
                    PlantRepository.JournalType.WATERING -> activity.getString(R.string.watering_entry)
                    PlantRepository.JournalType.FERTILISING -> activity.getString(R.string.fertilising_entry)
                    PlantRepository.JournalType.REPOTTING -> activity.getString(R.string.repotting_entry)
                    PlantRepository.JournalType.PRUNING -> activity.getString(R.string.pruning_entry)
                    PlantRepository.JournalType.MISTING -> activity.getString(R.string.misting_entry)
                }
                textSize = 15f
                setTextColor(activity.getColor(R.color.on_surface))
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            },
        )
        text.addView(
            TextView(activity).apply {
                this.text = ImageUtils.formatRelativeDate(activity, entry.timestamp)
                textSize = 12f
                setTextColor(activity.getColor(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 2.dp() }
            },
        )
        entry.text?.takeIf { it.isNotBlank() }?.let { body ->
            text.addView(
                TextView(activity).apply {
                    this.text = body
                    textSize = 14f
                    setTextColor(activity.getColor(R.color.on_surface_secondary))
                    setLineSpacing(3.dp().toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 7.dp() }
                },
            )
        }
        entry.photoUri?.let { uri ->
            text.addView(
                ImageView(activity).apply {
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        160.dp(),
                    ).apply { topMargin = 10.dp() }
                    scaleType = ImageView.ScaleType.CENTER_CROP
                    Glide.with(activity)
                        .load(Uri.parse(uri))
                        // RGB_565 for the photos-grid thumbnails; the full-screen
                        // viewer (openJournalPhotoViewer) still loads at full quality.
                        .format(DecodeFormat.PREFER_RGB_565)
                        .transform(CenterCrop(), RoundedCorners(18.dp()))
                        .into(this)
                    isClickable = true
                    setOnClickListener { openJournalPhotoViewer(uri) }
                },
            )
        }
        row.addView(text)
        card.addView(row)
        card.isLongClickable = true
        card.setOnLongClickListener { anchor ->
            Haptics.medium(activity)
            showJournalContextMenu(anchor, entry)
            true
        }
        return card
    }

    private fun showJournalContextMenu(anchor: View, entry: PlantJournalEntity) {
        PopupMenu(activity, anchor).apply {
            menu.add(0, 1, 0, activity.getString(R.string.edit_entry))
            menu.add(0, 2, 1, activity.getString(R.string.delete_entry))
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> {
                        showEditJournalDialog(entry)
                        true
                    }
                    2 -> {
                        confirmDeleteJournalEntry(entry)
                        true
                    }
                    else -> false
                }
            }
            show()
        }
    }

    private fun showEditJournalDialog(entry: PlantJournalEntity) {
        val type = PlantRepository.JournalType.from(entry.type)
        val hintRes = when (type) {
            PlantRepository.JournalType.NOTE -> R.string.note_hint
            PlantRepository.JournalType.WATERING -> R.string.watering_hint
            PlantRepository.JournalType.PHOTO -> R.string.photo_caption_hint
            PlantRepository.JournalType.FERTILISING -> R.string.fertilising_hint
            PlantRepository.JournalType.REPOTTING -> R.string.repotting_hint
            PlantRepository.JournalType.PRUNING -> R.string.pruning_hint
            PlantRepository.JournalType.MISTING -> R.string.misting_hint
        }
        val allowEmpty = type != PlantRepository.JournalType.NOTE

        val view = activity.layoutInflater.inflate(R.layout.dialog_gradient_input, null)
        view.findViewById<ImageView>(R.id.iv_dialog_icon).setImageResource(R.drawable.ic_note_pencil)
        view.findViewById<TextView>(R.id.tv_dialog_title).setText(R.string.edit_entry)
        val inputLayout = view.findViewById<TextInputLayout>(R.id.til_dialog_input).apply {
            hint = activity.getString(hintRes)
        }
        val input = view.findViewById<TextInputEditText>(R.id.et_dialog_input).apply {
            filters = arrayOf(InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
            maxLines = 6
            setText(entry.text.orEmpty())
        }
        val dialog = MaterialAlertDialogBuilder(activity)
            .setView(view)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .show()
        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val text = input.text?.toString()?.trim().orEmpty()
            if (!allowEmpty && text.isEmpty()) {
                inputLayout.error = activity.getString(R.string.journal_text_required)
                return@setOnClickListener
            }
            activity.lifecycleScope.launch {
                plantRepository.updateJournalEntryText(entry.id, text)
                Toast.makeText(
                    activity,
                    R.string.entry_updated,
                    Toast.LENGTH_SHORT,
                ).show()
                onReload()
            }
            dialog.dismiss()
        }
    }

    private fun confirmDeleteJournalEntry(entry: PlantJournalEntity) {
        MaterialAlertDialogBuilder(activity)
            .setTitle(R.string.delete_entry)
            .setMessage(R.string.delete_entry_confirm)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.delete) { _, _ ->
                activity.lifecycleScope.launch {
                    plantRepository.deleteJournalEntry(entry.id)
                    Toast.makeText(
                        activity,
                        R.string.entry_deleted,
                        Toast.LENGTH_SHORT,
                    ).show()
                    onReload()
                }
            }
            .show()
    }

    /** Open the full-screen photo viewer rooted at a journal photo entry.
     *  Builds the viewer's chronological list from every PHOTO entry on
     *  this plant so the user can swipe between them. */
    private fun openJournalPhotoViewer(tappedUri: String) {
        activity.lifecycleScope.launch {
            val photos = plantRepository.getJournalEntries(plantId)
                .filter { it.type == PlantRepository.JournalType.PHOTO.value }
                .mapNotNull { it.photoUri }
                .sorted() // approximates chrono order; the journal-render
            // function already returns newest-first
            val ordered = photos.distinct()
            val start = ordered.indexOf(tappedUri).coerceAtLeast(0)
            PhotoViewerActivity.open(activity, ordered, start)
        }
    }

    private fun Int.dp(): Int = dp(activity)

    private companion object {
        private const val TEXT_FIELD_MAX_LENGTH = 1024
    }
}
