package com.plantdiseases.app.ui.garden

import android.content.Context
import android.text.InputFilter
import android.util.TypedValue
import android.widget.LinearLayout
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.RoomEntity
import com.plantdiseases.app.data.repository.RoomRepository
import kotlinx.coroutines.launch

// Create/rename/delete-room dialogs, shared between GardenFragment
// ("+ Add room" and a room card's long-press menu) and RoomDetailActivity
// (its toolbar overflow) — same sharing rationale as PlantDialogs.kt.

/**
 * Add a room ([existing] `null`) or rename one — a single dialog, the way
 * `showAddEditPlantDialog` already handles add and edit together; the two
 * bodies differed only in the title, the seeded name and which repository
 * call ran. [onDone] fires once the write lands.
 */
fun FragmentActivity.showRoomNameDialog(
    roomRepository: RoomRepository,
    existing: RoomEntity?,
    onDone: (Long) -> Unit,
) {
    val root = this
    val (layout, input) = roomNameField(root, existing?.name)
    val container = LinearLayout(root).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(24.dp(root), 24.dp(root), 24.dp(root), 0)
        addView(layout)
    }
    val dialog = MaterialAlertDialogBuilder(root)
        .setTitle(if (existing == null) R.string.room_add else R.string.room_rename)
        .setView(container)
        .setNegativeButton(R.string.cancel, null)
        .setPositiveButton(R.string.save, null)
        .show()
    dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
        val name = input.text?.toString().orEmpty()
        root.lifecycleScope.launch {
            val result = if (existing == null) {
                roomRepository.createRoom(name, icon = null)
            } else {
                roomRepository.renameRoom(existing.id, name)
            }
            when (result) {
                is RoomRepository.RoomNameResult.Success -> {
                    dialog.dismiss()
                    onDone(result.roomId)
                }
                RoomRepository.RoomNameResult.BlankName -> layout.error = getString(R.string.plant_name_required)
                RoomRepository.RoomNameResult.NameTaken -> layout.error = getString(R.string.room_name_taken)
                // The room was deleted from another screen while this dialog
                // was open. There is nothing left to rename and no error worth
                // showing on the field — close and let the caller's reload
                // drop the room from the UI. existing is non-null on every
                // path that can reach NotFound (only renameRoom returns it,
                // and only when existing != null), so the fallback below is
                // unreachable in practice — it exists only to satisfy the
                // type, not as a real sentinel a caller should branch on.
                RoomRepository.RoomNameResult.NotFound -> {
                    dialog.dismiss()
                    onDone(existing?.id ?: -1L)
                }
            }
        }
    }
}

fun FragmentActivity.confirmDeleteRoomDialog(roomRepository: RoomRepository, room: RoomEntity, onDeleted: () -> Unit) {
    val root = this
    MaterialAlertDialogBuilder(root)
        .setTitle(room.name)
        .setMessage(R.string.room_delete_confirm)
        .setNegativeButton(R.string.cancel, null)
        .setPositiveButton(R.string.room_delete) { _, _ ->
            root.lifecycleScope.launch {
                roomRepository.deleteRoom(room.id)
                onDeleted()
            }
        }
        .show()
}

private const val NAME_MAX_LENGTH = 120

private fun roomNameField(ctx: Context, value: String?): Pair<TextInputLayout, TextInputEditText> {
    val input = TextInputEditText(ctx).apply {
        filters = arrayOf(InputFilter.LengthFilter(NAME_MAX_LENGTH))
        setText(value.orEmpty())
        setSingleLine(true)
    }
    val layout = TextInputLayout(ctx).apply {
        hint = ctx.getString(R.string.room_name_hint)
        addView(input)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        )
    }
    return layout to input
}

private fun Int.dp(ctx: Context): Int = TypedValue.applyDimension(
    TypedValue.COMPLEX_UNIT_DIP,
    this.toFloat(),
    ctx.resources.displayMetrics,
).toInt()
