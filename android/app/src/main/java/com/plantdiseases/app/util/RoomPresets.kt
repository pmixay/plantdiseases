package com.plantdiseases.app.util

import androidx.annotation.DrawableRes
import com.plantdiseases.app.R

/**
 * Suggested room names + icons for the create-room dialog. Not a closed
 * list — the user can type any name; a preset only seeds the icon. Icon
 * resolution falls back to the generic room glyph for a free-typed name
 * or an unrecognised stored key (e.g. from a future preset the app no
 * longer ships).
 */
object RoomPresets {

    data class Preset(val key: String, @DrawableRes val iconRes: Int, val labelRes: Int)

    val presets: List<Preset> = listOf(
        Preset("living", R.drawable.ic_room, R.string.room_preset_living),
        Preset("bedroom", R.drawable.ic_room, R.string.room_preset_bedroom),
        Preset("kitchen", R.drawable.ic_room, R.string.room_preset_kitchen),
        Preset("office", R.drawable.ic_room, R.string.room_preset_office),
        Preset("balcony", R.drawable.ic_light, R.string.room_preset_balcony),
        Preset("bathroom", R.drawable.ic_humidity, R.string.room_preset_bathroom),
        Preset("other", R.drawable.ic_room, R.string.room_preset_other),
    )

    fun byKey(key: String?): Preset? = presets.firstOrNull { it.key == key }

    @DrawableRes
    fun iconRes(iconKey: String?): Int = byKey(iconKey)?.iconRes ?: R.drawable.ic_room
}
