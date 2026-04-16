package com.plantdiseases.app.ui.camera

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import com.plantdiseases.app.R

/**
 * Draws a rule-of-thirds grid overlay on the camera preview.
 */
class GridOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.scan_frame_color)
        strokeWidth = 1f
        style = Paint.Style.STROKE
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()

        // Two vertical lines
        canvas.drawLine(w / 3f, 0f, w / 3f, h, paint)
        canvas.drawLine(2f * w / 3f, 0f, 2f * w / 3f, h, paint)

        // Two horizontal lines
        canvas.drawLine(0f, h / 3f, w, h / 3f, paint)
        canvas.drawLine(0f, 2f * h / 3f, w, 2f * h / 3f, paint)
    }
}
