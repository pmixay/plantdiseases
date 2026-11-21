package com.plantdiseases.app.ui.garden.detail

import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.util.Haptics

/**
 * Shared programmatic-UI helpers for the plant-detail screen. The detail
 * screen builds its views in code (rather than inflating a static layout), so
 * every controller leans on the same primitive builders — [baseCard],
 * [statPill], [actionRow]/[actionTile], [emptyState], [selectable] and the
 * [dp] density helper — extracted here once instead of duplicated per
 * controller. All are `internal` extensions on [AppCompatActivity] so they use
 * the activity as their [android.content.Context] exactly as the original
 * Activity methods did.
 */

/** Density-independent pixels → raw pixels against the activity's metrics. */
internal fun Int.dp(activity: AppCompatActivity): Int = TypedValue.applyDimension(
    TypedValue.COMPLEX_UNIT_DIP,
    this.toFloat(),
    activity.resources.displayMetrics,
).toInt()

/** The themed ripple used as a card / row foreground. */
internal fun AppCompatActivity.selectable(): Drawable? {
    val outValue = TypedValue()
    theme.resolveAttribute(android.R.attr.selectableItemBackground, outValue, true)
    return getDrawable(outValue.resourceId)
}

internal fun AppCompatActivity.baseCard(): MaterialCardView = MaterialCardView(this).apply {
    // PictureThis-style soft card: no stroke, 2dp elevation, 20dp
    // corners. Single source of truth for every detail-screen
    // panel (care plan, watering, doctor, photos, journal). The
    // earlier hairline stroke fought the new white surface.
    radius = 20.dp(this@baseCard).toFloat()
    cardElevation = 2.dp(this@baseCard).toFloat()
    strokeWidth = 0
    setCardBackgroundColor(getColor(R.color.surface))
    isClickable = true
    foreground = selectable()
    layoutParams = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT,
        LinearLayout.LayoutParams.WRAP_CONTENT,
    ).apply { bottomMargin = 12.dp(this@baseCard) }
}

internal fun AppCompatActivity.statPill(
    label: String,
    value: String,
    endMargin: Boolean = true,
): View {
    val box = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        background = getDrawable(R.drawable.bg_stat_pill)
        setPadding(10.dp(this@statPill), 10.dp(this@statPill), 10.dp(this@statPill), 10.dp(this@statPill))
        layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            .apply { if (endMargin) marginEnd = 8.dp(this@statPill) }
    }
    box.addView(
        TextView(this).apply {
            text = value
            textSize = 18f
            setTextColor(getColor(R.color.on_surface))
            typeface = Typeface.DEFAULT_BOLD
        },
    )
    box.addView(
        TextView(this).apply {
            text = label
            textSize = 12f
            setTextColor(getColor(R.color.on_surface))
            typeface = Typeface.DEFAULT_BOLD
        },
    )
    return box
}

internal data class ActionSpec(val labelRes: Int, val iconRes: Int, val onClick: () -> Unit)

internal fun AppCompatActivity.actionRow(specs: List<ActionSpec>): LinearLayout {
    val row = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        )
    }
    specs.forEachIndexed { idx, spec ->
        row.addView(
            actionTile(spec.labelRes, spec.iconRes, spec.onClick).apply {
                layoutParams = LinearLayout.LayoutParams(0, 66.dp(this@actionRow), 1f).apply {
                    if (idx > 0) marginStart = 8.dp(this@actionRow)
                }
            },
        )
    }
    return row
}

internal fun AppCompatActivity.actionTile(
    labelRes: Int,
    iconRes: Int,
    action: () -> Unit,
): MaterialCardView =
    MaterialCardView(this).apply {
        radius = 18.dp(this@actionTile).toFloat()
        cardElevation = 0f
        strokeWidth = 1.dp(this@actionTile)
        strokeColor = getColor(R.color.card_stroke)
        setCardBackgroundColor(getColor(R.color.surface))
        isClickable = true
        foreground = selectable()
        contentDescription = getString(labelRes)
        setOnClickListener {
            Haptics.tick(this@actionTile)
            action()
        }
        addView(
            LinearLayout(this@actionTile).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                layoutParams = FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT,
                )
                setPadding(4.dp(this@actionTile), 7.dp(this@actionTile), 4.dp(this@actionTile), 6.dp(this@actionTile))
                addView(
                    ImageView(this@actionTile).apply {
                        setImageResource(iconRes)
                        setColorFilter(getColor(R.color.primary))
                        layoutParams = LinearLayout.LayoutParams(24.dp(this@actionTile), 24.dp(this@actionTile))
                    },
                )
                addView(
                    TextView(this@actionTile).apply {
                        text = getString(labelRes)
                        textSize = 11f
                        gravity = Gravity.CENTER
                        maxLines = 1
                        ellipsize = TextUtils.TruncateAt.END
                        setTextColor(getColor(R.color.on_surface))
                        typeface = Typeface.create(
                            "sans-serif-medium",
                            Typeface.NORMAL,
                        )
                        layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            LinearLayout.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = 5.dp(this@actionTile) }
                    },
                )
            },
        )
    }

internal fun AppCompatActivity.emptyState(imageRes: Int, title: String, body: String): LinearLayout =
    LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setPadding(28.dp(this@emptyState), 24.dp(this@emptyState), 28.dp(this@emptyState), 28.dp(this@emptyState))
        addView(
            ImageView(this@emptyState).apply {
                setImageResource(imageRes)
                layoutParams = LinearLayout.LayoutParams(180.dp(this@emptyState), 180.dp(this@emptyState))
            },
        )
        addView(
            TextView(this@emptyState).apply {
                text = title
                textSize = 22f
                gravity = Gravity.CENTER
                setTextColor(getColor(R.color.on_surface))
                typeface = Typeface.create("sans-serif-black", Typeface.NORMAL)
                letterSpacing = -0.02f
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 8.dp(this@emptyState) }
            },
        )
        addView(
            TextView(this@emptyState).apply {
                text = body
                textSize = 14f
                gravity = Gravity.CENTER
                setTextColor(getColor(R.color.on_surface_secondary))
                setLineSpacing(4.dp(this@emptyState).toFloat(), 1f)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 8.dp(this@emptyState) }
            },
        )
    }
