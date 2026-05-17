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
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.bumptech.glide.Glide
import com.bumptech.glide.request.target.CustomTarget
import com.bumptech.glide.request.transition.Transition
import com.plantdiseases.app.R
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

        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            )
        }
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, bars.top, 0, bars.bottom)
            insets
        }

        val compare = CompareView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            )
        }
        root.addView(compare)

        // Top bar with close + label so the user knows which is which.
        val topBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.argb(96, 0, 0, 0))
            setPadding(16.dp(), 12.dp(), 16.dp(), 12.dp())
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP,
            )
        }
        topBar.addView(ImageView(this).apply {
            setImageResource(R.drawable.ic_back)
            setColorFilter(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(28.dp(), 28.dp())
            isClickable = true
            setOnClickListener { finish() }
        })
        topBar.addView(TextView(this).apply {
            text = getString(R.string.photo_compare_title)
            textSize = 14f
            setTextColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 16.dp() }
            gravity = Gravity.CENTER
        })
        root.addView(topBar)

        // Bottom label strip explaining which side is which (the divider
        // is otherwise unambiguous from the strokes around each half).
        val legend = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.argb(96, 0, 0, 0))
            setPadding(16.dp(), 12.dp(), 16.dp(), 12.dp())
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            )
        }
        legend.addView(TextView(this).apply {
            text = getString(R.string.photo_compare_left)
            textSize = 13f
            setTextColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            gravity = Gravity.START
        })
        legend.addView(TextView(this).apply {
            text = getString(R.string.photo_compare_right)
            textSize = 13f
            setTextColor(Color.WHITE)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            gravity = Gravity.END
        })
        root.addView(legend)

        setContentView(root)

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

    private fun Int.dp(): Int = (this * resources.displayMetrics.density).toInt()

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
