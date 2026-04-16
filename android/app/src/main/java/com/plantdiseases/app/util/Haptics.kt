package com.plantdiseases.app.util

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

object Haptics {

    fun tick(context: Context) = vibrate(context, 10L)

    fun light(context: Context) = vibrate(context, 20L)

    fun medium(context: Context) = vibrate(context, 50L)

    fun success(context: Context) = playEffect(
        context,
        VibrationEffect.createWaveform(longArrayOf(0, 30, 80, 30), -1)
    )

    private fun vibrate(context: Context, durationMs: Long) =
        playEffect(context, VibrationEffect.createOneShot(durationMs, VibrationEffect.DEFAULT_AMPLITUDE))

    private fun playEffect(context: Context, effect: VibrationEffect) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val manager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
                manager.defaultVibrator.vibrate(effect)
            } else {
                @Suppress("DEPRECATION")
                val vibrator = context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
                vibrator.vibrate(effect)
            }
        } catch (_: Exception) {
        }
    }
}
