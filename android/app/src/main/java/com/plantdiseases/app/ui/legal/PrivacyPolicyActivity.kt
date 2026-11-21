package com.plantdiseases.app.ui.legal

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
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
 * The page carries both languages as `data-ru`/`data-en` attributes and swaps
 * them in JS, so JavaScript has to stay on — but nothing else does. The
 * WebView loads exactly one local asset, has no JS bridge, no DOM storage and
 * no file access beyond `file:///android_asset`, and hands every outbound
 * link (the mailto: contact link) to the handling app rather than following
 * it in-app.
 */
class PrivacyPolicyActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPrivacyPolicyBinding

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityPrivacyPolicyBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.appBar.updatePadding(top = bars.top)
            view.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.toolbar.setNavigationOnClickListener { finish() }

        binding.privacyWeb.apply {
            settings.javaScriptEnabled = true // language toggle only
            settings.domStorageEnabled = false
            settings.allowFileAccess = false // android_asset is still reachable
            settings.allowContentAccess = false
            settings.setSupportZoom(false)
            webViewClient = ExternalLinksInBrowser()
            // Match the page's own ground so there is no white flash on a
            // dark-theme device before the first paint.
            setBackgroundColor(getColor(com.plantdiseases.app.R.color.background))
        }

        val lang = if (LocaleHelper.isRussian(this)) "ru" else "en"
        // Pass the app's RESOLVED mode, not the device preference. A user can
        // force dark mode while Android itself is light (and vice versa), so a
        // plain prefers-color-scheme query is not a sufficient source of truth.
        val nightMode = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK
        val theme = if (nightMode == Configuration.UI_MODE_NIGHT_YES) "dark" else "light"
        binding.privacyWeb.loadUrl(
            "file:///android_asset/privacy_policy.html?lang=$lang&theme=$theme",
        )
    }

    /** The policy's only outbound link is the mailto: contact link. Hand it
     *  to the email app — an in-app WebView is no place to browse the web. */
    private inner class ExternalLinksInBrowser : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url ?: return true
            if (url.scheme == "file") return false
            runCatching { startActivity(Intent(Intent.ACTION_VIEW, url)) }
            return true
        }
    }

    companion object {
        fun intent(context: Context): Intent = Intent(context, PrivacyPolicyActivity::class.java)
    }
}
