package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.bumptech.glide.Glide
import com.bumptech.glide.request.target.CustomTarget
import com.bumptech.glide.request.transition.Transition
import com.plantdiseases.app.databinding.ActivityPhotoCompareBinding
import com.plantdiseases.app.util.LocaleHelper

/**
 * Before/after photo compare. Two images stacked under a draggable
 * vertical divider — drag the line left to reveal more of the
 * "after" image, right for the "before". Standard iOS-style
 * compare gesture.
 *
 * Inputs (intent extras):
 *   * EXTRA_FIRST_URI  — the "before" photo (typically older)
 *   * EXTRA_SECOND_URI — the "after"  photo (typically newer)
 *
 * Both URIs are loaded via Glide so file:// + content:// both work.
 */
class PhotoCompareActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPhotoCompareBinding

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val firstUri = intent.getStringExtra(EXTRA_FIRST_URI)
        val secondUri = intent.getStringExtra(EXTRA_SECOND_URI)
        if (firstUri.isNullOrBlank() || secondUri.isNullOrBlank()) {
            finish(); return
        }

        binding = ActivityPhotoCompareBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, bars.top, 0, bars.bottom)
            insets
        }

        val compare = binding.compareView
        binding.compareClose.setOnClickListener { finish() }

        // Glide loads asynchronously into CustomTarget hooks; the
        // compare view re-invalidates as soon as each side lands.
        Glide.with(this).load(firstUri).into(object : CustomTarget<Drawable>() {
            override fun onResourceReady(resource: Drawable, transition: Transition<in Drawable>?) {
                compare.setBefore(resource)
            }
            override fun onLoadCleared(placeholder: Drawable?) {}
        })
        Glide.with(this).load(secondUri).into(object : CustomTarget<Drawable>() {
            override fun onResourceReady(resource: Drawable, transition: Transition<in Drawable>?) {
                compare.setAfter(resource)
            }
            override fun onLoadCleared(placeholder: Drawable?) {}
        })
    }

    companion object {
        const val EXTRA_FIRST_URI = "extra_first_uri"
        const val EXTRA_SECOND_URI = "extra_second_uri"

        fun open(context: Context, firstUri: String, secondUri: String) {
            context.startActivity(Intent(context, PhotoCompareActivity::class.java).apply {
                putExtra(EXTRA_FIRST_URI, firstUri)
                putExtra(EXTRA_SECOND_URI, secondUri)
            })
        }
    }
}

/**
 * Custom view that renders two drawables stacked with a draggable
 * vertical divider. Letterbox-fits each drawable to the view's
 * bounds (preserving aspect ratio), then clips the "after"
 * drawable to the area RIGHT of the divider.
 */
class CompareView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    private var before: Drawable? = null
    private var after: Drawable? = null
    private var dividerFraction = 0.5f
    private val handlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.FILL
    }
    private val handleStrokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.STROKE
        strokeWidth = 3f * resources.displayMetrics.density
    }

    fun setBefore(d: Drawable) { before = d; invalidate() }
    fun setAfter(d: Drawable) { after = d; invalidate() }

    private fun fitRect(drawable: Drawable): Rect {
        // Letterbox-fit drawable inside this view, preserving aspect.
        val vw = width.toFloat()
        val vh = height.toFloat()
        val dw = drawable.intrinsicWidth.coerceAtLeast(1).toFloat()
        val dh = drawable.intrinsicHeight.coerceAtLeast(1).toFloat()
        val scale = minOf(vw / dw, vh / dh)
        val w = (dw * scale).toInt()
        val h = (dh * scale).toInt()
        val left = ((vw - w) / 2f).toInt()
        val top = ((vh - h) / 2f).toInt()
        return Rect(left, top, left + w, top + h)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        before?.let { b ->
            b.bounds = fitRect(b)
            b.draw(canvas)
        }
        val dividerX = (width * dividerFraction).toInt()
        after?.let { a ->
            canvas.save()
            canvas.clipRect(dividerX, 0, width, height)
            a.bounds = fitRect(a)
            a.draw(canvas)
            canvas.restore()
        }
        // Divider line.
        canvas.drawLine(dividerX.toFloat(), 0f, dividerX.toFloat(), height.toFloat(), handleStrokePaint)
        // Divider handle — small white circle at the vertical centre
        // so the user knows it's draggable.
        val r = 14f * resources.displayMetrics.density
        canvas.drawCircle(dividerX.toFloat(), height / 2f, r, handlePaint)
        canvas.drawCircle(dividerX.toFloat(), height / 2f, r, handleStrokePaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN,
            MotionEvent.ACTION_MOVE,
            MotionEvent.ACTION_UP -> {
                dividerFraction = (event.x / width.toFloat()).coerceIn(0f, 1f)
                invalidate()
                return true
            }
        }
        return false
    }

    @Suppress("unused")
    private fun ignoreBitmap(d: Drawable): BitmapDrawable? = d as? BitmapDrawable
}
