package com.plantdiseases.app.ui.legal

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.method.LinkMovementMethod
import android.text.style.BulletSpan
import android.text.style.ClickableSpan
import android.text.style.URLSpan
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.text.HtmlCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.core.widget.TextViewCompat
import com.google.android.material.color.MaterialColors
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityPrivacyPolicyBinding
import com.plantdiseases.app.util.LocaleHelper

/**
 * The privacy policy, bundled in `assets/privacy_policy.html` instead of
 * being fetched from a hosted page.
 *
 * Two reasons it ships with the APK: the consent screen links to it during
 * onboarding, where there may be no network yet and a dead link is the worst
 * possible first impression; and a bundled copy can never describe a
 * different build than the one the reader is running.
 *
 * The asset stays the single source of the text: [PrivacyPolicyParser] reads it
 * at runtime and this screen builds native views from the blocks, so the
 * policy follows the app theme and the device's font and size settings. It
 * opens in the app language; the RU/EN chips switch the page only, not the
 * app's saved language. Links (the mailto: contact) are handed to the handling
 * app, there is no in-app browsing.
 */
class PrivacyPolicyActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPrivacyPolicyBinding
    private var blocks: List<PolicyBlock> = emptyList()
    private var russian = false

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityPrivacyPolicyBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val scrollBottom = binding.policyScroll.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.appBar.updatePadding(top = bars.top)
            binding.policyScroll.updatePadding(bottom = scrollBottom + bars.bottom)
            insets
        }
        binding.toolbar.setNavigationOnClickListener { finish() }

        blocks = runCatching {
            assets.open(POLICY_ASSET).bufferedReader(Charsets.UTF_8).use { PrivacyPolicyParser.parse(it.readText()) }
        }.getOrDefault(emptyList())

        russian = savedInstanceState?.getBoolean(STATE_RUSSIAN) ?: LocaleHelper.isRussian(this)
        binding.languageGroup.check(if (russian) R.id.chip_lang_ru else R.id.chip_lang_en)
        binding.languageGroup.setOnCheckedStateChangeListener { _, ids ->
            val next = ids.firstOrNull() == R.id.chip_lang_ru
            if (next != russian) {
                russian = next
                render()
            }
        }
        render()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(STATE_RUSSIAN, russian)
    }

    private fun render() {
        val container = binding.policyContent
        container.removeAllViews()
        blocks.forEach { block ->
            container.addView(createView(block))
        }
    }

    private fun createView(block: PolicyBlock): TextView {
        val view = TextView(this)
        val styled = HtmlCompat.fromHtml(block.text(russian), HtmlCompat.FROM_HTML_MODE_LEGACY).trimTrailingNewlines()
        val text = SpannableStringBuilder(styled)
        routeLinksToExternalApps(text)
        val (appearance, topMargin) = when (block.kind) {
            PolicyBlockKind.TITLE -> R.style.TextAppearance_App_Headline to R.dimen.space_lg
            PolicyBlockKind.UPDATED -> R.style.TextAppearance_App_Body to R.dimen.space_xs
            PolicyBlockKind.HEADING -> R.style.TextAppearance_App_Title to R.dimen.space_2xl
            PolicyBlockKind.PARAGRAPH -> R.style.TextAppearance_App_Body to R.dimen.space_sm
            PolicyBlockKind.BULLET -> R.style.TextAppearance_App_Body to R.dimen.space_sm
        }
        TextViewCompat.setTextAppearance(view, appearance)
        if (block.kind == PolicyBlockKind.HEADING || block.kind == PolicyBlockKind.UPDATED) {
            view.setTypeface(view.typeface, android.graphics.Typeface.BOLD)
        }
        if (block.kind == PolicyBlockKind.BULLET) {
            // One span over the whole item gives every wrapped line the hanging indent.
            val gap = resources.getDimensionPixelSize(R.dimen.space_md)
            val radius = resources.getDimensionPixelSize(R.dimen.space_xs) / 2 + 1
            text.setSpan(
                BulletSpan(gap, ContextCompat.getColor(this, R.color.on_surface), radius),
                0,
                text.length,
                Spanned.SPAN_INCLUSIVE_EXCLUSIVE,
            )
        }
        view.text = text
        view.setLinkTextColor(MaterialColors.getColor(view, androidx.appcompat.R.attr.colorPrimary))
        view.movementMethod = LinkMovementMethod.getInstance()
        view.layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
        ).apply { this.topMargin = resources.getDimensionPixelSize(topMargin) }
        view.textAlignment = View.TEXT_ALIGNMENT_VIEW_START
        return view
    }

    /**
     * Replace each [URLSpan] with one that starts an ACTION_VIEW intent and
     * swallows "no app can handle this", which would otherwise crash the tap
     * on a phone with no email app. Only mailto and web links are passed on.
     */
    private fun routeLinksToExternalApps(text: SpannableStringBuilder) {
        text.getSpans(0, text.length, URLSpan::class.java).forEach { span ->
            val start = text.getSpanStart(span)
            val end = text.getSpanEnd(span)
            val flags = text.getSpanFlags(span)
            text.removeSpan(span)
            val uri = Uri.parse(span.url)
            if (uri.scheme in ALLOWED_LINK_SCHEMES) {
                text.setSpan(ExternalLinkSpan(uri), start, end, flags)
            }
        }
    }

    private class ExternalLinkSpan(private val uri: Uri) : ClickableSpan() {
        override fun onClick(widget: View) {
            runCatching {
                widget.context.startActivity(Intent(Intent.ACTION_VIEW, uri))
            }
        }
    }

    private fun CharSequence.trimTrailingNewlines(): CharSequence {
        var end = length
        while (end > 0 && this[end - 1] == '\n') end--
        return subSequence(0, end)
    }

    companion object {
        private const val POLICY_ASSET = "privacy_policy.html"
        private const val STATE_RUSSIAN = "policy_russian"
        private val ALLOWED_LINK_SCHEMES = setOf("mailto", "https", "http")

        fun intent(context: Context): Intent = Intent(context, PrivacyPolicyActivity::class.java)
    }
}
