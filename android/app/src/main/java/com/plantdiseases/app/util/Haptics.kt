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

    private fun vibrate(context: Context, durationMs: Long) {
        val effect = VibrationEffect.createOneShot(durationMs, VibrationEffect.DEFAULT_AMPLITUDE)
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
