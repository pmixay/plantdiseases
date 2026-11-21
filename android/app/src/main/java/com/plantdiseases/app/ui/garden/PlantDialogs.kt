package com.plantdiseases.app.ui.garden

import android.content.Context
import android.graphics.Typeface
import android.text.InputFilter
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.util.SpeciesIdentity
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The add/edit-plant dialog and the delete-plant confirmation, shared
 * between [GardenFragment] (the FAB "+" — always a new plant) and
 * [RoomDetailActivity] (long-press on a plant row — edit or delete an
 * existing one). Extension functions on [FragmentActivity] so both a
 * Fragment (via `requireActivity()`) and an Activity (`this`) can call
 * them without duplicating ~150 lines of dialog-building code twice.
 */
fun FragmentActivity.showAddEditPlantDialog(
    plantRepository: PlantRepository,
    roomRepository: RoomRepository,
    rooms: List<RoomRepository.RoomSummary>,
    existing: PlantEntity?,
    /** Room the dialog opens pre-selected to, for the "+" on a room's own
     *  screen. Ignored when [existing] is non-null (an edit always starts
     *  from the plant's current room, not this). */
    preselectedRoomId: Long? = null,
    onSaved: () -> Unit,
) {
    val root = this
    val container = LinearLayout(root).apply { orientation = LinearLayout.VERTICAL }

    val header = LinearLayout(root).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(18.dp(root), 18.dp(root), 18.dp(root), 18.dp(root))
        setBackgroundResource(R.drawable.bg_garden_hero)
    }
    header.addView(
        FrameLayout(root).apply {
            setBackgroundResource(R.drawable.bg_circle_semi)
            layoutParams = LinearLayout.LayoutParams(52.dp(root), 52.dp(root))
            addView(
                ImageView(root).apply {
                    setImageResource(R.drawable.ic_garden)
                    setColorFilter(root.getColor(R.color.on_hero))
                    layoutParams = FrameLayout.LayoutParams(28.dp(root), 28.dp(root), Gravity.CENTER)
                }
            )
        }
    )
    header.addView(
        LinearLayout(root).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 14.dp(root) }
            addView(
                TextView(root).apply {
                    text = getString(if (existing == null) R.string.add_plant else R.string.edit_plant)
                    textSize = 20f
                    setTextColor(root.getColor(R.color.on_hero))
                    typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                }
            )
            addView(
                TextView(root).apply {
                    text = getString(
                        if (existing == null) R.string.plant_dialog_add_subtitle else R.string.plant_dialog_edit_subtitle,
                    )
                    textSize = 13f
                    setTextColor(root.getColor(R.color.on_hero_secondary))
                    setLineSpacing(3.dp(root).toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 3.dp(root) }
                }
            )
        }
    )

    val fields = LinearLayout(root).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(18.dp(root), 18.dp(root), 18.dp(root), 4.dp(root))
    }
    val name = inputField(root, getString(R.string.plant_name), existing?.name)
    val initialSpeciesDisplay = existing?.species
        ?: SpeciesIdentity.canonicalDisplay(existing?.scientificName, existing?.commonName)
    val species = inputField(root, getString(R.string.plant_species), initialSpeciesDisplay)
    fields.addView(name.first)
    fields.addView(species.first)
    var selectedRoomId = existing?.roomId ?: preselectedRoomId
    fields.addView(
        buildRoomPickerRow(root, roomRepository, rooms, selectedRoomId) { picked -> selectedRoomId = picked },
    )
    container.addView(header)
    container.addView(fields)

    val dialog = MaterialAlertDialogBuilder(root)
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
        hideKeyboard(root, dialog.window?.decorView ?: name.second)
        dialog.dismiss()
        val typedSpecies = species.second.text?.toString()
        val manualClearsCanonical = SpeciesIdentity.manualEditClearsCanonical(
            typedSpecies,
            existing?.scientificName,
            existing?.commonName,
        )
        val fillsPreviouslyBlankCanonical = existing != null &&
            SpeciesIdentity.canonicalDisplay(existing.scientificName, existing.commonName) == null &&
            SpeciesIdentity.displayChanged(initialSpeciesDisplay, typedSpecies)
        root.lifecycleScope.launch {
            plantRepository.savePlant(
                plantName,
                typedSpecies,
                existing,
                roomId = selectedRoomId,
                canonicalSpeciesUpdate = if (manualClearsCanonical || fillsPreviouslyBlankCanonical) {
                    PlantRepository.CanonicalSpeciesUpdate.REPLACE
                } else {
                    PlantRepository.CanonicalSpeciesUpdate.PRESERVE
                },
            )
            awaitKeyboardClosed(root)
            onSaved()
        }
    }
}

fun FragmentActivity.confirmDeletePlantDialog(
    plantRepository: PlantRepository,
    plant: PlantEntity,
    onDeleted: () -> Unit,
) {
    val root = this
    MaterialAlertDialogBuilder(root)
        .setTitle(plant.name)
        .setMessage(R.string.delete_plant_confirm)
        .setNegativeButton(R.string.cancel, null)
        .setPositiveButton(R.string.delete) { _, _ ->
            val appContext = root.applicationContext
            root.lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
                withContext(NonCancellable) {
                    ReminderManager.cancelAll(appContext, plant.id, plantRepository.getReminders(plant.id))
                    plantRepository.deletePlant(plant.id)
                }
                if (!root.isDestroyed) {
                    Toast.makeText(root, R.string.plant_deleted, Toast.LENGTH_SHORT).show()
                    onDeleted()
                }
            }
        }
        .show()
}

/** Sentinel row in the picker's choice list — never a real room id (Room's
 *  autoincrement PKs start at 1) and distinct from `null` (Unassigned). */
private const val CREATE_ROOM_SENTINEL = -1L

/** Tap-to-pick room row: current selection + a chevron; tapping opens a
 *  single-choice list of every room + "Unassigned" + "Add room" (creating
 *  one re-fetches the list and selects it, so a fresh install isn't stuck
 *  choosing between Unassigned and nothing). No new dropdown widget needed
 *  — reuses the same MaterialAlertDialogBuilder every other dialog on this
 *  screen already depends on. Internal (not private) so
 *  [com.plantdiseases.app.ui.result.CreatePlantSheet] can reuse it instead
 *  of building a second room picker. */
internal fun buildRoomPickerRow(
    activity: FragmentActivity,
    roomRepository: RoomRepository,
    initialRooms: List<RoomRepository.RoomSummary>,
    initialRoomId: Long?,
    onPicked: (Long?) -> Unit,
): View {
    val ctx = activity
    var rooms = initialRooms
    val label = TextView(ctx).apply {
        textSize = 13f
        setTextColor(ctx.getColor(R.color.on_surface))
    }
    var currentRoomId = initialRoomId
    fun applyLabel() {
        label.text = rooms.firstOrNull { it.room.id == currentRoomId }?.room?.name
            ?: ctx.getString(R.string.plant_room_unassigned)
    }
    applyLabel()

    val row = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        isClickable = true
        isFocusable = true
        setBackgroundResource(R.drawable.bg_thumb_rounded)
        setPadding(14.dp(ctx), 14.dp(ctx), 14.dp(ctx), 14.dp(ctx))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = 10.dp(ctx) }
        addView(
            ImageView(ctx).apply {
                setImageResource(R.drawable.ic_room)
                setColorFilter(ctx.getColor(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(20.dp(ctx), 20.dp(ctx))
            }
        )
        addView(
            LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                    .apply { marginStart = 10.dp(ctx) }
                addView(
                    TextView(ctx).apply {
                        text = ctx.getString(R.string.plant_room)
                        textSize = 12f
                        typeface = android.graphics.Typeface.DEFAULT_BOLD
                        setTextColor(ctx.getColor(R.color.on_surface))
                    }
                )
                addView(label)
            }
        )
        addView(
            ImageView(ctx).apply {
                setImageResource(R.drawable.ic_chevron_down)
                setColorFilter(ctx.getColor(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(18.dp(ctx), 18.dp(ctx))
            }
        )
    }

    // A picker with no way to create a room dead-ends the moment a fresh
    // install has zero of them: Unassigned plus nothing else to pick.
    fun showPicker() {
        val names = (
            listOf(ctx.getString(R.string.plant_room_unassigned)) +
                rooms.map { it.room.name } +
                ctx.getString(R.string.room_add)
            ).toTypedArray()
        val ids = listOf<Long?>(null) + rooms.map { it.room.id } + CREATE_ROOM_SENTINEL
        val checkedIndex = ids.indexOf(currentRoomId).coerceAtLeast(0)
        MaterialAlertDialogBuilder(ctx)
            .setTitle(R.string.plant_room)
            .setSingleChoiceItems(names, checkedIndex) { dialog, which ->
                val picked = ids[which]
                if (picked == CREATE_ROOM_SENTINEL) {
                    dialog.dismiss()
                    activity.showRoomNameDialog(roomRepository, existing = null) { newRoomId ->
                        activity.lifecycleScope.launch {
                            rooms = roomRepository.getRoomsForPicker()
                            currentRoomId = newRoomId
                            applyLabel()
                            onPicked(currentRoomId)
                        }
                    }
                } else {
                    currentRoomId = picked
                    applyLabel()
                    onPicked(currentRoomId)
                    dialog.dismiss()
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }
    row.setOnClickListener { showPicker() }
    return row
}

private const val TEXT_FIELD_MAX_LENGTH = 1024

private fun inputField(ctx: Context, hint: String, value: String?): Pair<TextInputLayout, TextInputEditText> {
    val input = TextInputEditText(ctx).apply {
        filters = arrayOf(InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH))
        setText(value.orEmpty())
        inputType = InputType.TYPE_CLASS_TEXT
        setSingleLine(true)
    }
    val layout = TextInputLayout(ctx).apply {
        this.hint = hint
        addView(input)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = 10.dp(ctx) }
    }
    return layout to input
}

private fun hideKeyboard(ctx: Context, anchor: View) {
    (ctx.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager)
        .hideSoftInputFromWindow(anchor.windowToken, 0)
}

private suspend fun awaitKeyboardClosed(activity: FragmentActivity) {
    val root = activity.findViewById<View>(android.R.id.content)
    repeat(10) {
        val imeVisible = ViewCompat.getRootWindowInsets(root)?.isVisible(WindowInsetsCompat.Type.ime()) == true
        if (!imeVisible) {
            delay(48)
            return
        }
        delay(32)
    }
    delay(96)
}

private fun Int.dp(ctx: Context): Int = TypedValue.applyDimension(
    TypedValue.COMPLEX_UNIT_DIP,
    this.toFloat(),
    ctx.resources.displayMetrics,
).toInt()
