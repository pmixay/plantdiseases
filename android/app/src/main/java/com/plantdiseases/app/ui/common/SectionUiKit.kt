package com.plantdiseases.app.ui.common

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R

/**
 * Shared, Context-scoped row/card builders used by every rebuilt collapsible section —
 * the plant screen's About / Fertiliser / Tips bodies and every Garden hub section. They
 * exist so all of those bodies share one visual language (the same card, icon circle and
 * type scale as the rest of the app) instead of each screen inventing its own TextView.
 */

internal fun Context.dpx(value: Int): Int = (value * resources.displayMetrics.density).toInt()

internal fun Context.selectableForeground(): Drawable? {
    val outValue = TypedValue()
    theme.resolveAttribute(android.R.attr.selectableItemBackground, outValue, true)
    return ContextCompat.getDrawable(this, outValue.resourceId)
}

/** The panel every section body sits in — matches @style/App.GlassCard in code. */
internal fun Context.sectionCard(): MaterialCardView = MaterialCardView(this).apply {
    radius = resources.getDimension(R.dimen.radius_md)
    cardElevation = 0f
    strokeWidth = dpx(1)
    strokeColor = ContextCompat.getColor(this@sectionCard, R.color.card_stroke)
    setCardBackgroundColor(ContextCompat.getColor(this@sectionCard, R.color.surface))
    layoutParams = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT,
        LinearLayout.LayoutParams.WRAP_CONTENT,
    ).apply { bottomMargin = dpx(10) }
}

/** A vertical column with the standard inner padding, to be added to a [sectionCard]. */
internal fun Context.cardColumn(): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    setPadding(dpx(14), dpx(14), dpx(14), dpx(14))
}

/** Small heading inside a card, e.g. "Feeding schedule". */
internal fun Context.cardHeading(text: String): TextView = TextView(this).apply {
    this.text = text
    setTextColor(ContextCompat.getColor(this@cardHeading, R.color.on_surface))
    typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_title_sm))
    setPadding(0, 0, 0, dpx(6))
}

/**
 * A value longer than this cannot share a line with its label: the two columns
 * are weighted equally, so a full sentence on the right squeezes the left one
 * to nothing and the row loses its subject — an unlabelled paragraph floating
 * against the right edge, which is what made the About card read as broken.
 */
private const val INLINE_VALUE_MAX_CHARS = 24

/**
 * Fact row with a tinted icon circle, used by the About fact card and the Fertiliser
 * schedule. Short values ("Demanding", an NPK ratio) sit on the label's line, right
 * aligned. Long ones — a native-range sentence, a soil mix — stack UNDER the label
 * and run the full width of the row instead, so the label always survives and the
 * paragraph reads left-to-right like the prose it is.
 */
internal fun Context.factRow(iconRes: Int, label: String, value: String): View =
    LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        // TOP, not CENTER_VERTICAL: a wrapped value would otherwise float the
        // icon to the middle of the block instead of beside its first line.
        gravity = Gravity.TOP
        setPadding(0, dpx(7), 0, dpx(7))
        addView(
            ImageView(this@factRow).apply {
                setImageResource(iconRes)
                setBackgroundResource(R.drawable.bg_icon_circle)
                setPadding(dpx(7), dpx(7), dpx(7), dpx(7))
                imageTintList = ColorStateList.valueOf(
                    ContextCompat.getColor(this@factRow, R.color.primary),
                )
            },
            LinearLayout.LayoutParams(dpx(32), dpx(32)),
        )
        val labelView = TextView(this@factRow).apply {
            text = label
            maxLines = 2
            ellipsize = TextUtils.TruncateAt.END
            setTextColor(ContextCompat.getColor(this@factRow, R.color.on_surface_secondary))
            setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_body_sm))
        }
        val valueView = TextView(this@factRow).apply {
            text = value
            setTextColor(ContextCompat.getColor(this@factRow, R.color.on_surface))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_body))
        }
        if (value.length > INLINE_VALUE_MAX_CHARS) {
            valueView.setLineSpacing(dpx(3).toFloat(), 1f)
            addView(
                LinearLayout(this@factRow).apply {
                    orientation = LinearLayout.VERTICAL
                    addView(
                        labelView,
                        LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                        ),
                    )
                    addView(
                        valueView,
                        LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = dpx(3) },
                    )
                },
                // 0dp + weight: the column takes whatever the icon leaves, so a
                // long sentence wraps inside the row instead of widening it.
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                    .apply { marginStart = dpx(12) },
            )
        } else {
            valueView.gravity = Gravity.END
            addView(
                labelView,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    1f,
                ).apply {
                    marginStart = dpx(12)
                    marginEnd = dpx(12)
                },
            )
            addView(
                // WRAP_CONTENT + equal weights, not 0dp: a 0dp base would split the
                // row by weight alone and break long RU labels mid-word next to a
                // short value sitting in dead space. Both sides ask for what they
                // need first; only the surplus is distributed.
                valueView,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    1f,
                ),
            )
        }
    }

/** A single advice line: coloured dot, then wrapping text. */
internal fun Context.bulletRow(text: String, dotColor: Int = R.color.primary): View =
    LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        setPadding(0, dpx(6), 0, dpx(6))
        addView(
            View(this@bulletRow).apply {
                setBackgroundResource(R.drawable.bg_status_dot)
                backgroundTintList = ColorStateList.valueOf(
                    ContextCompat.getColor(this@bulletRow, dotColor),
                )
            },
            LinearLayout.LayoutParams(dpx(7), dpx(7)).apply { topMargin = dpx(6) },
        )
        addView(
            TextView(this@bulletRow).apply {
                this.text = text
                setTextColor(ContextCompat.getColor(this@bulletRow, R.color.on_surface))
                setLineSpacing(dpx(3).toFloat(), 1f)
                setTextSize(TypedValue.COMPLEX_UNIT_PX, resources.getDimension(R.dimen.text_body))
            },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = dpx(10)
            },
        )
    }

/** Thin rule between rows inside a section body. */
internal fun Context.rowDivider(): View = View(this).apply {
    setBackgroundColor(ContextCompat.getColor(this@rowDivider, R.color.divider))
    layoutParams = LinearLayout.LayoutParams(
        LinearLayout.LayoutParams.MATCH_PARENT,
        (1f * resources.displayMetrics.density).toInt(),
    )
    alpha = 0.5f
}

/**
 * The standard Garden-hub row: icon (or photo) circle, title, optional secondary line,
 * optional trailing text, chevron when tappable. This is the visual unit of every
 * section body in the Garden hub.
 */
internal fun Context.sectionRow(
    title: String,
    subtitle: String?,
    iconRes: Int,
    trailing: String? = null,
    trailingColor: Int = R.color.on_surface_secondary,
    iconTint: Int = R.color.primary,
    thumb: ((ImageView) -> Unit)? = null,
    onClick: (() -> Unit)? = null,
): View = LinearLayout(this).apply {
    orientation = LinearLayout.HORIZONTAL
    gravity = Gravity.CENTER_VERTICAL
    setPadding(0, dpx(10), 0, dpx(10))
    if (onClick != null) {
        isClickable = true
        foreground = selectableForeground()
        setOnClickListener { onClick() }
    }
    val iconView = ImageView(this@sectionRow).apply {
        setImageResource(iconRes)
        scaleType = ImageView.ScaleType.CENTER_CROP
        clipToOutline = true
        setBackgroundResource(R.drawable.bg_icon_circle)
        if (thumb == null) {
            setPadding(dpx(7), dpx(7), dpx(7), dpx(7))
            imageTintList = ColorStateList.valueOf(ContextCompat.getColor(this@sectionRow, iconTint))
        }
    }
    addView(iconView, LinearLayout.LayoutParams(dpx(34), dpx(34)))
    thumb?.invoke(iconView)
    addView(
        LinearLayout(this@sectionRow).apply {
            orientation = LinearLayout.VERTICAL
            addView(
                TextView(this@sectionRow).apply {
                    text = title
                    textSize = 14f
                    maxLines = 1
                    ellipsize = TextUtils.TruncateAt.END
                    typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                    setTextColor(ContextCompat.getColor(this@sectionRow, R.color.on_surface))
                },
            )
            if (!subtitle.isNullOrBlank()) {
                addView(
                    TextView(this@sectionRow).apply {
                        text = subtitle
                        textSize = 12f
                        maxLines = 1
                        ellipsize = TextUtils.TruncateAt.END
                        setTextColor(
                            ContextCompat.getColor(this@sectionRow, R.color.on_surface_secondary),
                        )
                    },
                )
            }
        },
        LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            .apply { marginStart = dpx(12) },
    )
    if (!trailing.isNullOrBlank()) {
        addView(
            TextView(this@sectionRow).apply {
                text = trailing
                textSize = 13f
                setTextColor(ContextCompat.getColor(this@sectionRow, trailingColor))
            },
        )
    }
    if (onClick != null) {
        addView(
            ImageView(this@sectionRow).apply {
                setImageResource(R.drawable.ic_chevron_right)
                imageTintList = ColorStateList.valueOf(
                    ContextCompat.getColor(this@sectionRow, R.color.on_surface_secondary),
                )
            },
            LinearLayout.LayoutParams(dpx(20), dpx(20)).apply { marginStart = dpx(6) },
        )
    }
}

/**
 * The designed empty state for a section body. Never a bare sentence: an icon, a
 * headline and, when there is something the user can actually do about it, a button.
 */
internal fun Context.sectionEmpty(
    iconRes: Int,
    title: String,
    body: String,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
): View = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    gravity = Gravity.CENTER_HORIZONTAL
    setPadding(dpx(16), dpx(18), dpx(16), dpx(18))
    addView(
        ImageView(this@sectionEmpty).apply {
            setImageResource(iconRes)
            alpha = 0.35f
            imageTintList = ColorStateList.valueOf(
                ContextCompat.getColor(this@sectionEmpty, R.color.on_surface_secondary),
            )
        },
        LinearLayout.LayoutParams(dpx(40), dpx(40)),
    )
    addView(
        TextView(this@sectionEmpty).apply {
            text = title
            textSize = 15f
            gravity = Gravity.CENTER
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            setTextColor(ContextCompat.getColor(this@sectionEmpty, R.color.on_surface))
            setPadding(0, dpx(10), 0, 0)
        },
    )
    addView(
        TextView(this@sectionEmpty).apply {
            text = body
            textSize = 13f
            gravity = Gravity.CENTER
            setTextColor(ContextCompat.getColor(this@sectionEmpty, R.color.on_surface_secondary))
            setPadding(0, dpx(4), 0, 0)
        },
    )
    if (actionLabel != null && onAction != null) {
        addView(
            // Manually styled outline (rather than a named Material style attr) to
            // match how this codebase already builds one-off buttons — see
            // DetailUiKit.actionTile's stroked MaterialCardView.
            MaterialButton(this@sectionEmpty).apply {
                text = actionLabel
                setTextColor(ContextCompat.getColor(this@sectionEmpty, R.color.primary))
                strokeColor = ColorStateList.valueOf(
                    ContextCompat.getColor(this@sectionEmpty, R.color.card_stroke),
                )
                strokeWidth = dpx(1)
                backgroundTintList = ColorStateList.valueOf(
                    ContextCompat.getColor(this@sectionEmpty, R.color.surface),
                )
                setOnClickListener { onAction() }
            },
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dpx(12) },
        )
    }
}

/** The centred text link that closes a section body, e.g. "See all". */
internal fun Context.sectionFooter(label: String, onClick: () -> Unit): View =
    TextView(this).apply {
        text = label
        textSize = 13f
        gravity = Gravity.CENTER
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        setTextColor(ContextCompat.getColor(this@sectionFooter, R.color.primary))
        setPadding(0, dpx(12), 0, dpx(4))
        isClickable = true
        foreground = selectableForeground()
        setOnClickListener { onClick() }
    }
