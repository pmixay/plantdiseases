package com.plantdiseases.app.ui.guide

import android.content.Context
import android.graphics.Typeface
import android.text.Spannable
import android.text.Spanned
import android.text.style.LeadingMarginSpan
import android.util.TypedValue
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.common.ContentLinks

/**
 * Shared renderer for a [com.plantdiseases.app.data.model.GuideItem]'s
 * article body, so the Guide tab's bottom sheet and the full-screen
 * GuideDetailActivity produce identical layouts from one code path. Splits
 * the hand-typed content into its intro + ``SYMPTOMS:`` / ``TREATMENT:`` /
 * … sections and renders each as an accent-headed card.
 */
object GuideContentRenderer {

    /** Render [content] into [container], replacing its children. */
    fun render(container: LinearLayout, content: String) {
        val ctx = container.context
        container.removeAllViews()
        parseSections(content).forEach { section ->
            container.addView(
                if (section.header == null) {
                    introView(ctx, section.body)
                } else {
                    sectionCard(ctx, section.header, section.body)
                },
            )
        }
    }

    /** A labelled hero photo for the top of an article (server-hosted,
     *  disk-cached for offline). Caller inserts it at index 0. */
    fun heroPhoto(ctx: Context, imageUrl: String, contentDesc: CharSequence): View {
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(ctx, 16) }
        }
        column.addView(
            TextView(ctx).apply {
                text = ctx.getString(R.string.example_photos_label)
                textSize = 12f
                setTextColor(ContextCompat.getColor(ctx, R.color.primary))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                letterSpacing = 0.06f
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { bottomMargin = dp(ctx, 8) }
            }
        )
        val image = ImageView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(ctx, 190),
            )
            scaleType = ImageView.ScaleType.CENTER_CROP
            contentDescription = contentDesc
        }
        Glide.with(ctx)
            .load(imageUrl)
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .transform(CenterCrop(), RoundedCorners(dp(ctx, 16)))
            .into(image)
        column.addView(image)
        return column
    }

    private fun introView(ctx: Context, body: String): View = TextView(ctx).apply {
        ContentLinks.apply(this, body)
        textSize = 15f
        setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
        setLineSpacing(dp(ctx, 3).toFloat(), 1f)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = dp(ctx, 14) }
    }

    private fun sectionCard(ctx: Context, header: String, body: String): View {
        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(ctx, 16), dp(ctx, 14), dp(ctx, 16), dp(ctx, 14))
        }
        col.addView(
            TextView(ctx).apply {
                text = header.uppercase()
                textSize = 12f
                setTextColor(ContextCompat.getColor(ctx, R.color.primary))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                letterSpacing = 0.06f
            }
        )
        col.addView(
            TextView(ctx).apply {
                ContentLinks.apply(this, body)
                textSize = 15f
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                setLineSpacing(dp(ctx, 3).toFloat(), 1f)
                applyHangingIndent(this, dp(ctx, 20))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(ctx, 6) }
            }
        )
        return MaterialCardView(ctx).apply {
            radius = dp(ctx, 14).toFloat()
            cardElevation = 0f
            setCardBackgroundColor(ContextCompat.getColor(ctx, R.color.surface_variant))
            strokeWidth = dp(ctx, 1)
            strokeColor = ContextCompat.getColor(ctx, R.color.card_stroke)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(ctx, 12) }
            addView(col)
        }
    }

    /** Each "• " / "1. " line in [tv]'s (already-set) text is its own
     *  paragraph — give every paragraph a 0/[indentPx] leading margin so a
     *  wrapped continuation line aligns under the item's own text instead
     *  of falling back to the left edge under the bullet/number. A single
     *  span across the whole block won't do this: LeadingMarginSpan only
     *  treats its OWN first line as unindented, so one span per paragraph
     *  is required for every item to get its own hanging indent. */
    private fun applyHangingIndent(tv: TextView, indentPx: Int) {
        val text = tv.text as? Spannable ?: return
        var start = 0
        while (start <= text.length) {
            val end = text.indexOf('\n', start).let { if (it == -1) text.length else it }
            if (end > start) {
                text.setSpan(
                    LeadingMarginSpan.Standard(0, indentPx),
                    start,
                    end,
                    Spanned.SPAN_INCLUSIVE_EXCLUSIVE,
                )
            }
            start = end + 1
        }
    }

    private data class Section(val header: String?, val body: String)

    private fun parseSections(content: String): List<Section> {
        val sections = mutableListOf<Section>()
        var header: String? = null
        val buf = StringBuilder()
        fun flush() {
            val text = buf.toString().trim()
            if (text.isNotEmpty() || header != null) sections.add(Section(header, text))
            buf.setLength(0)
        }
        content.lines().forEach { line ->
            val t = line.trim()
            // A header is a short, all-caps line ending in ':' (EN or RU),
            // e.g. "SYMPTOMS:" / "ЛЕЧЕНИЕ:". Bullets/numbered steps start with
            // a non-letter, so they never match.
            val lead = t.takeWhile { it.isLetter() }
            val isHeader = t.endsWith(":") &&
                t.length <= 48 &&
                lead.length >= 3 &&
                lead == lead.uppercase()
            if (isHeader) {
                flush()
                header = t.removeSuffix(":").trim()
            } else {
                buf.appendLine(line)
            }
        }
        flush()
        return sections
    }

    private fun dp(ctx: Context, v: Int): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        v.toFloat(),
        ctx.resources.displayMetrics,
    ).toInt()
}
