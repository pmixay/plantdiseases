package com.plantdiseases.app.ui.widget

import android.content.Context
import android.graphics.RenderEffect
import android.graphics.Shader
import android.os.Build
import android.util.AttributeSet
import android.view.LayoutInflater
import android.view.View
import android.widget.FrameLayout
import com.google.android.material.button.MaterialButton
import com.plantdiseases.app.R

/** Frosted, accessible in-place preview used by paid feature surfaces. */
class ProLockedOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : FrameLayout(context, attrs, defStyle) {
    private val unlockButton: MaterialButton
    private var preview: View? = null
    private var previousAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_AUTO

    init {
        LayoutInflater.from(context).inflate(R.layout.view_pro_locked_overlay, this, true)
        unlockButton = findViewById(R.id.pro_gate_button)
        visibility = View.VISIBLE
    }

    fun bindPreview(target: View, locked: Boolean, onUnlock: () -> Unit) {
        if (preview !== target) {
            preview?.let(::clearEffect)
            preview = target
            previousAccessibility = target.importantForAccessibility
        }
        setOnClickListener { if (locked) onUnlock() }
        unlockButton.setOnClickListener { if (locked) onUnlock() }
        visibility = if (locked) View.VISIBLE else View.GONE
        isClickable = locked
        isFocusable = locked
        if (locked) applyEffect(target) else clearEffect(target)
    }

    private fun applyEffect(target: View) {
        target.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            target.alpha = 0.78f
            target.setRenderEffect(RenderEffect.createBlurEffect(9f, 9f, Shader.TileMode.CLAMP))
        } else {
            // RenderEffect starts at API 31. The opaque tonal scrim plus a
            // softened preview is the stable Android 10/11 fallback.
            target.alpha = 0.42f
        }
    }

    private fun clearEffect(target: View) {
        target.alpha = 1f
        target.importantForAccessibility = previousAccessibility
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) target.setRenderEffect(null)
    }
}
