package com.plantdiseases.app.ui.community

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.text.format.DateUtils
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.CommunityTopDisease
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper

/**
 * The consensus vote bar and the forum-style comment row — shared between
 * [com.plantdiseases.app.ui.result.CommunityOpinionSheet] (viewer sheet) and
 * [MyScanRepliesActivity] (the owner's dedicated reply screen). Both render
 * the exact same visual for the same data, including the class labels:
 * [labelFor] resolves them here, from [DiseaseLabels], so the same diagnosis
 * can never read differently on the two screens.
 */
object OpinionViews {

    /** Display authority for a class key on both opinion surfaces. Deliberately
     *  NOT a caller-supplied lambda: the sheet's chip catalogue covers only 8 of
     *  the 15 keys the server accepts, so a `pests` opinion used to render as
     *  raw snake_case there while the owner's screen said "Sap-sucking Pests". */
    private fun labelFor(ctx: Context, key: String): String =
        DiseaseLabels.displayName(key, LocaleHelper.isRussian(ctx)) ?: key

    /** One horizontal percentage bar for the consensus graph. The leading
     *  disease is tinted with the brand primary; the rest use a muted
     *  primary so the winner reads at a glance. */
    fun buildVoteBar(
        ctx: Context,
        entry: CommunityTopDisease,
        total: Int,
        leading: Boolean,
    ): View {
        val density = ctx.resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()
        val pct = Math.round(entry.count * 100f / total).coerceIn(0, 100)
        val fillColor = ContextCompat.getColor(ctx, if (leading) R.color.primary else R.color.primary_light)
        val trackColor = ContextCompat.getColor(ctx, R.color.surface_variant)

        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(6), 0, dp(6))
        }

        val labelLine = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        labelLine.addView(
            TextView(ctx).apply {
                text = labelFor(ctx, entry.disease)
                textSize = 13f
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            },
        )
        labelLine.addView(
            TextView(ctx).apply {
                text = ctx.getString(R.string.community_vote_percent, pct)
                textSize = 13f
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface_secondary))
            },
        )
        row.addView(labelLine)

        val radius = dp(4).toFloat()
        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            background = GradientDrawable().apply {
                cornerRadius = radius
                setColor(trackColor)
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(8),
            ).apply { topMargin = dp(4) }
            weightSum = 100f
        }
        track.addView(
            View(ctx).apply {
                background = GradientDrawable().apply {
                    cornerRadius = radius
                    setColor(fillColor)
                }
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, pct.toFloat())
            },
        )
        if (pct < 100) {
            track.addView(
                View(ctx).apply {
                    layoutParams = LinearLayout.LayoutParams(
                        0,
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        (100 - pct).toFloat(),
                    )
                },
            )
        }
        row.addView(track)
        return row
    }

    /** One forum-style comment row: monogram avatar, nickname + relative
     *  time header, the suggested class (a comment is always at least a
     *  vote), and the free-text note underneath when the author wrote one. */
    fun buildOpinionRow(
        ctx: Context,
        op: CommunityOpinion,
        onReport: ((CommunityOpinion) -> Unit)? = null,
        onBlock: ((CommunityOpinion) -> Unit)? = null,
        onAuthor: ((CommunityOpinion) -> Unit)? = null,
    ): View {
        val density = ctx.resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()
        val nick = op.nickname?.takeIf { it.isNotBlank() }
        val who = nick ?: ctx.getString(R.string.community_anonymous_author)
        // Only a real account-author handle opens a profile; a legacy reply
        // carries no addressable author, so its row stays inert.
        val authorClickable = onAuthor != null &&
            op.authorToken?.let { it.startsWith("ca") && it.length == 32 } == true

        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, dp(8))
        }

        row.addView(
            TextView(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(dp(32), dp(32)).apply { marginEnd = dp(10) }
                gravity = Gravity.CENTER
                background = ContextCompat.getDrawable(ctx, R.drawable.bg_circle_surface_variant)
                backgroundTintList = ColorStateList.valueOf(Avatars.colorFor(who))
                setTextColor(Color.WHITE)
                textSize = 11f
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                text = Avatars.monogramForNick(who)
                if (authorClickable) {
                    contentDescription = ctx.getString(R.string.community_open_profile, who)
                    isClickable = true
                    isFocusable = true
                    setOnClickListener { onAuthor.invoke(op) }
                } else {
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                }
            },
        )

        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }

        val head = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        head.addView(
            TextView(ctx).apply {
                text = who
                textSize = 13f
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
                setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                if (authorClickable) {
                    isClickable = true
                    isFocusable = true
                    setOnClickListener { onAuthor.invoke(op) }
                }
            },
        )
        if (op.createdAt > 0L) {
            head.addView(
                TextView(ctx).apply {
                    text = DateUtils.getRelativeTimeSpanString(
                        op.createdAt * 1000L,
                        System.currentTimeMillis(),
                        DateUtils.MINUTE_IN_MILLIS,
                        DateUtils.FORMAT_ABBREV_RELATIVE,
                    )
                    textSize = 12f
                    setTextColor(ContextCompat.getColor(ctx, R.color.on_surface_secondary))
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { marginStart = dp(8) }
                },
            )
        }
        col.addView(head)

        col.addView(
            TextView(ctx).apply {
                text = labelFor(ctx, op.suggestedDisease)
                textSize = 14f
                setTextColor(ContextCompat.getColor(ctx, R.color.primary))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(2) }
            },
        )

        if (op.note.isNotBlank()) {
            col.addView(
                TextView(ctx).apply {
                    text = op.note
                    textSize = 14f
                    setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = dp(2) }
                },
            )
        }

        // Moderation is attached to every visible reply, not hidden behind an
        // account-only menu. The server-issued content token prevents leaking
        // database ids or anonymous author identities to the client.
        val hasModerationAction = onReport != null || onBlock != null
        if (!op.isMine && !op.contentId.isNullOrBlank() && hasModerationAction) {
            val actions = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(4) }
            }
            onReport?.let { callback ->
                actions.addView(
                    TextView(ctx).apply {
                        text = ctx.getString(R.string.community_report_action)
                        textSize = 12f
                        setTextColor(ContextCompat.getColor(ctx, R.color.on_surface_secondary))
                        setPadding(0, dp(6), dp(16), dp(6))
                        isClickable = true
                        isFocusable = true
                        setOnClickListener { callback(op) }
                    },
                )
            }
            onBlock?.let { callback ->
                actions.addView(
                    TextView(ctx).apply {
                        text = ctx.getString(R.string.community_block_action)
                        textSize = 12f
                        setTextColor(ContextCompat.getColor(ctx, R.color.on_surface_secondary))
                        setPadding(0, dp(6), dp(8), dp(6))
                        isClickable = true
                        isFocusable = true
                        setOnClickListener { callback(op) }
                    },
                )
            }
            col.addView(actions)
        }

        row.addView(col)
        return row
    }
}
