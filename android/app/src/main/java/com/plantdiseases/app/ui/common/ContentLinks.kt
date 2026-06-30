package com.plantdiseases.app.ui.common

import android.content.Context
import android.content.Intent
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.method.LinkMovementMethod
import android.text.style.ClickableSpan
import android.view.View
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.ui.camera.WaterCalculatorActivity
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.symptom.SymptomCheckerActivity

/**
 * In-app cross-links for hand-written documentation — the disease-guide
 * articles ([GuideDataProvider]) and the Profile tips. Author a link inside the
 * content as `[label](token)`; [apply] turns each marker into a tappable span
 * that opens the matching in-app destination. Plain text renders unchanged, so
 * adding a cross-link is just editing the content string — no per-call wiring.
 *
 * Supported tokens:
 *  - `tool:water` / `tool:light` / `tool:symptom` — the home tools
 *  - `guide:<id>` — a disease-guide article (ids live in GuideDataProvider)
 */
object ContentLinks {

    private val LINK = Regex("""\[([^\]]+)]\(([^)]+)\)""")

    /** Set [raw] on [tv], rendering any `[label](token)` markers as tappable
     *  in-app links. A string with no markers is set verbatim. */
    fun apply(tv: TextView, raw: String) {
        val out = SpannableStringBuilder()
        var cursor = 0
        var linked = false
        for (m in LINK.findAll(raw)) {
            out.append(raw, cursor, m.range.first)
            val token = m.groupValues[2]
            val start = out.length
            out.append(m.groupValues[1])
            out.setSpan(
                object : ClickableSpan() {
                    override fun onClick(widget: View) = open(widget.context, token)
                },
                start,
                out.length,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
            cursor = m.range.last + 1
            linked = true
        }
        out.append(raw, cursor, raw.length)
        tv.text = out
        if (linked) {
            tv.movementMethod = LinkMovementMethod.getInstance()
            tv.setLinkTextColor(ContextCompat.getColor(tv.context, R.color.primary))
        }
    }

    private fun open(ctx: Context, token: String) {
        runCatching {
            when {
                token == "tool:water" -> ctx.startActivity(Intent(ctx, WaterCalculatorActivity::class.java))
                token == "tool:light" -> ctx.startActivity(Intent(ctx, LightMeterActivity::class.java))
                token == "tool:symptom" -> ctx.startActivity(Intent(ctx, SymptomCheckerActivity::class.java))
                token.startsWith("guide:") -> GuideDetailActivity.start(ctx, token.removePrefix("guide:"))
            }
        }
    }
}
