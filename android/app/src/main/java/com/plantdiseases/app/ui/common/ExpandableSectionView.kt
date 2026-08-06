package com.plantdiseases.app.ui.common

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R

/**
 * One collapsible section on the plant screen: a tappable header row and a
 * body that is GONE until the user expands it. Every section on the screen is
 * an instance of this, so spacing, chevron behaviour and the expand animation
 * stay identical across Care / Diseases / Lunar / Tips.
 *
 * The body is intentionally a bare container — hosts add whatever view they
 * already had (the existing CarePlanCardView, the disease list, the new
 * LunarCalendarView), so no existing rendering logic changes.
 */
class ExpandableSectionView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : MaterialCardView(context, attrs, defStyle) {

    private val header: LinearLayout
    private val iconView: ImageView
    private val titleView: TextView
    private val subtitleView: TextView
    private val badgeView: TextView
    private val chevron: ImageView
    val body: FrameLayout

    var isExpanded: Boolean = false
        private set

    var onToggle: ((Boolean) -> Unit)? = null

    // M3 "emphasized decelerate/accelerate" — a slow, confident settle instead of the
    // previous curve, which reached ~70% of the height in the first 5% of the duration
    // and read as an instant jump rather than a reveal.
    private val expandInterp = PathInterpolator(0.2f, 0f, 0f, 1f)
    private val collapseInterp = PathInterpolator(0.3f, 0f, 0.8f, 0.15f)
    private val chevronInterp = PathInterpolator(0.2f, 0f, 0f, 1f)
    private var bodyAnimator: ValueAnimator? = null
    private val risePx = 10f * resources.displayMetrics.density

    init {
        radius = resources.getDimension(R.dimen.radius_lg)
        cardElevation = 0f
        useCompatPadding = false
        setContentPadding(0, 0, 0, 0)
        setCardBackgroundColor(context.getColor(R.color.surface_variant))

        val column = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        addView(column, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))

        header = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            isClickable = true
            isFocusable = true
            setBackgroundResource(selectableBackgroundRes())
            setOnClickListener { toggle() }
        }
        column.addView(header)

        iconView = ImageView(context).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            setPadding(dp(9), dp(9), dp(9), dp(9))
            imageTintList = ColorStateList.valueOf(context.getColor(R.color.primary))
        }
        header.addView(iconView, LinearLayout.LayoutParams(dp(42), dp(42)))

        val texts = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        header.addView(
            texts,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = dp(14)
                marginEnd = dp(8)
            },
        )
        titleView = TextView(context).apply {
            setTextColor(context.getColor(R.color.on_surface))
            setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_title_sm))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        }
        subtitleView = TextView(context).apply {
            setTextColor(context.getColor(R.color.on_surface_secondary))
            setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_body_sm))
            setPadding(0, dp(2), 0, 0)
        }
        texts.addView(titleView)
        texts.addView(subtitleView)

        badgeView = TextView(context).apply {
            visibility = View.GONE
            setBackgroundResource(R.drawable.bg_section_badge)
            setTextColor(context.getColor(R.color.primary))
            setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_body_sm))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            gravity = Gravity.CENTER
            setPadding(dp(10), dp(3), dp(10), dp(3))
        }
        header.addView(
            badgeView,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply { marginEnd = dp(8) },
        )

        chevron = ImageView(context).apply {
            setImageResource(R.drawable.ic_chevron_down)
            imageTintList = ColorStateList.valueOf(context.getColor(R.color.on_surface_secondary))
        }
        header.addView(chevron, LinearLayout.LayoutParams(dp(24), dp(24)))

        body = FrameLayout(context).apply {
            visibility = View.GONE
            setPadding(dp(12), 0, dp(12), dp(12))
        }
        column.addView(body)
        updateAccessibility()
    }

    fun bind(iconRes: Int, titleRes: Int, subtitleRes: Int) {
        iconView.setImageResource(iconRes)
        titleView.setText(titleRes)
        subtitleView.text = context.getString(subtitleRes)
        subtitleView.visibility = View.VISIBLE
    }

    /** Overload for callers that already have resolved strings (e.g. the Garden hub,
     *  which builds its subtitles at runtime from live counts). */
    fun bind(iconRes: Int, title: CharSequence, subtitle: CharSequence?) {
        iconView.setImageResource(iconRes)
        titleView.text = title
        subtitleView.text = subtitle ?: ""
        subtitleView.visibility = if (subtitle.isNullOrBlank()) View.GONE else View.VISIBLE
    }

    /** Shows a count pill in the header, or hides it when [value] is null or blank. */
    fun setBadge(value: String?) {
        badgeView.text = value.orEmpty()
        badgeView.visibility = if (value.isNullOrBlank()) View.GONE else View.VISIBLE
    }

    /** Adds [content] as the section body, replacing anything already there. */
    fun setContent(content: View) {
        body.removeAllViews()
        body.addView(
            content,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            ),
        )
    }

    override fun toggle() = setExpanded(!isExpanded)

    /**
     * Animates the body's height from 0 to its measured wrap-content height
     * (or back), rather than the previous blunt [android.transition.AutoTransition]
     * — that transition re-laid-out the whole NestedScrollView subtree, couldn't
     * cross-fade the body, and paired badly with the chevron's instant rotation
     * snap. The chevron now animates on its own timeline alongside the body.
     */
    fun setExpanded(expanded: Boolean, animate: Boolean = true) {
        if (expanded == isExpanded) return
        isExpanded = expanded
        bodyAnimator?.cancel()
        bodyAnimator = null
        updateAccessibility()
        onToggle?.invoke(expanded)

        val chevronTarget = if (expanded) 180f else 0f
        val canAnimate = animate && isAttachedToWindow && width > 0
        if (!canAnimate) {
            body.visibility = if (expanded) View.VISIBLE else View.GONE
            body.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
            body.alpha = 1f
            body.translationY = 0f
            chevron.rotation = chevronTarget
            return
        }

        chevron.animate().rotation(chevronTarget)
            .setDuration(if (expanded) EXPAND_MS else COLLAPSE_MS)
            .setInterpolator(chevronInterp)
            .start()

        val from: Int
        val to: Int
        if (expanded) {
            body.visibility = View.VISIBLE
            val widthSpec = View.MeasureSpec.makeMeasureSpec(
                (width - paddingLeft - paddingRight).coerceAtLeast(0),
                View.MeasureSpec.EXACTLY,
            )
            body.measure(widthSpec, View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
            from = 0
            to = body.measuredHeight
        } else {
            from = body.height
            to = 0
        }

        if (expanded && to == 0) {
            // Empty body — nothing to animate, jump straight to the terminal
            // state instead of running a zero-height animator.
            body.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
            body.alpha = 1f
            return
        }

        bodyAnimator = ValueAnimator.ofInt(from, to).apply {
            duration = if (expanded) EXPAND_MS else COLLAPSE_MS
            interpolator = if (expanded) expandInterp else collapseInterp
            addUpdateListener { animator ->
                val f = animator.animatedFraction
                body.layoutParams.height = animator.animatedValue as Int
                // The body fades in only after the box has already started opening, and
                // rises the last few dp into place. Height and opacity moving in lockstep
                // is what made the old expand read as a pop rather than a reveal.
                val fade = if (expanded) {
                    ((f - FADE_DELAY) / (1f - FADE_DELAY)).coerceIn(0f, 1f)
                } else {
                    1f - f
                }
                body.alpha = fade
                body.translationY = (1f - fade) * risePx
                body.requestLayout()
            }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    bodyAnimator = null
                    body.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
                    body.alpha = 1f
                    body.translationY = 0f
                    if (!expanded) body.visibility = View.GONE
                    body.requestLayout()
                }
            })
            start()
        }
    }

    override fun onDetachedFromWindow() {
        bodyAnimator?.cancel()
        bodyAnimator = null
        chevron.animate().cancel()
        super.onDetachedFromWindow()
    }

    private fun updateAccessibility() {
        header.contentDescription = context.getString(
            if (isExpanded) R.string.section_collapse else R.string.section_expand,
        )
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun selectableBackgroundRes(): Int {
        val outValue = TypedValue()
        context.theme.resolveAttribute(android.R.attr.selectableItemBackground, outValue, true)
        return outValue.resourceId
    }

    private companion object {
        const val EXPAND_MS = 420L
        const val COLLAPSE_MS = 300L
        /** Fraction of the expand spent opening the box before the body starts fading in. */
        const val FADE_DELAY = 0.28f
    }
}
