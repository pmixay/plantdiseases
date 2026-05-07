package com.plantdiseases.app.util

import android.graphics.RenderEffect
import android.graphics.Shader
import android.os.Build
import android.view.View
import android.view.ViewGroup
import androidx.annotation.RequiresApi
import androidx.core.view.children

/**
 * Liquid-glass helpers.
 *
 * Real Gaussian backdrop blur landed on Android with the
 * `RenderEffect` / `setRenderEffect` APIs in **API 31 (Android 12)**.
 * On older devices we still ship the frosted look — the translucent
 * `glass_white*` colours plus the inner-stroke drawables we authored
 * carry the bulk of the visual identity — but the actual blur falls
 * back to a no-op so we don't pay for an off-screen render pass we
 * can't display.
 *
 * Usage:
 * ```
 * GlassEffects.applyBlur(myCard, GlassEffects.BLUR_LIGHT)
 * ```
 */
object GlassEffects {

    /** Light frost — toolbar / inline cards. */
    const val BLUR_LIGHT = 18f

    /** Heavy frost — modal sheets / fullscreen cards. */
    const val BLUR_HEAVY = 32f

    /** True if the runtime supports a backdrop blur via [RenderEffect]. */
    val isSupported: Boolean
        get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

    /**
     * Apply a Gaussian-blur backdrop to [view]. No-op on API ≤ 30.
     *
     * The blur is applied to the view itself (its content gets blurred,
     * not what's behind it) — we render the cards as semi-transparent
     * over the page so the page content visually sits behind them. For
     * a true backdrop blur of arbitrary content, layer two views: the
     * source content on the bottom, a blurred copy clipped to the card
     * rect on top. That's heavy enough that we leave it to the few
     * screens that genuinely benefit (camera shutter row, AI Doctor
     * sheet) — see [applyContainerBlur] for that path.
     */
    fun applyBlur(view: View?, radius: Float = BLUR_LIGHT) {
        if (view == null || !isSupported) return
        view.applyBlurApi31(radius)
    }

    /**
     * Apply a backdrop blur effect to every direct child of [container]
     * EXCEPT [skip] (typically the glass card itself, so it stays
     * sharp). Used by sheets that want the page behind to blur out.
     */
    fun applyContainerBlur(
        container: ViewGroup?,
        radius: Float = BLUR_LIGHT,
        skip: View? = null,
    ) {
        if (container == null || !isSupported) return
        container.children.forEach { child ->
            if (child !== skip) child.applyBlurApi31(radius)
        }
    }

    /** Remove a previously applied blur (e.g. when a sheet is dismissed). */
    fun clearBlur(view: View?) {
        if (view == null || !isSupported) return
        view.clearBlurApi31()
    }

    @RequiresApi(Build.VERSION_CODES.S)
    private fun View.applyBlurApi31(radius: Float) {
        val safeRadius = radius.coerceAtLeast(0.5f)
        setRenderEffect(
            RenderEffect.createBlurEffect(
                safeRadius, safeRadius, Shader.TileMode.CLAMP,
            ),
        )
    }

    @RequiresApi(Build.VERSION_CODES.S)
    private fun View.clearBlurApi31() {
        setRenderEffect(null)
    }
}
