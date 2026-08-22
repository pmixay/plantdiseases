package com.plantdiseases.app.util

import android.content.Context
import android.os.VibrationEffect
import android.os.VibratorManager

/**
 * Short haptic feedback helpers, named by intensity ([tick] < [light] <
 * [medium]) plus a two-pulse [success] pattern. Swallows any failure, so
 * callers can fire these fire-and-forget without guarding for devices that
 * lack a vibrator.
 */
object Haptics {

    /** Faintest pulse (~10 ms) — for selection/scroll ticks. */
    fun tick(context: Context) = vibrate(context, 10L)

    /** Light pulse (~20 ms) — for taps and toggles. */
    fun light(context: Context) = vibrate(context, 20L)

    /** Medium pulse (~50 ms) — for confirmations. */
    fun medium(context: Context) = vibrate(context, 50L)

    /** Two-pulse pattern signalling a completed action. */
    fun success(context: Context) =
        playEffect(context, VibrationEffect.createWaveform(longArrayOf(0, 30, 80, 30), -1))

    private fun vibrate(context: Context, durationMs: Long) =
        playEffect(context, VibrationEffect.createOneShot(durationMs, VibrationEffect.DEFAULT_AMPLITUDE))

    private fun playEffect(context: Context, effect: VibrationEffect) {
        try {
            val manager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
            manager.defaultVibrator.vibrate(effect)
        } catch (_: Exception) {
        }
    }
}
