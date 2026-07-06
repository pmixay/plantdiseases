package com.plantdiseases.app.ui.onboarding

import android.animation.ValueAnimator
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.AccelerateDecelerateInterpolator
import android.widget.ImageView
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityOnboardingBinding
import com.plantdiseases.app.databinding.ItemOnboardingPageBinding
import com.plantdiseases.app.util.LocaleHelper

/**
 * First-run onboarding: a Lottie-animated [ViewPager2] (scan → diagnose →
 * care → data consent) with an animated page indicator whose circles carry
 * each page's icon (lens / diagnosis / care / privacy shield), the current
 * one filled and gently scaled up. The final page is a
 * BLOCKING consent disclosure (photos + feedback are used, anonymized, to
 * train our models — Play "prominent disclosure"): Skip is hidden there and
 * jumps to it from earlier pages instead of finishing, so consent can never
 * be bypassed. Agreeing sets [KEY_ONBOARDING_DONE] + [KEY_DATA_CONSENT] and
 * moves on to [MainActivity]; [isOnboardingDone] lets the launcher decide
 * whether to show this screen at all.
 */
class OnboardingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityOnboardingBinding
    private val indicators = mutableListOf<ImageView>()
    private var currentSelected = -1
    private var indicatorAnimator: ValueAnimator? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityOnboardingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val pages = listOf(
            OnboardingPage(
                R.raw.onboarding_scan,
                R.drawable.ic_scan,
                getString(R.string.onboarding_scan_title),
                getString(R.string.onboarding_scan_body),
            ),
            OnboardingPage(
                R.raw.onboarding_diagnose,
                R.drawable.ic_healthy,
                getString(R.string.onboarding_diagnose_title),
                getString(R.string.onboarding_diagnose_body),
            ),
            OnboardingPage(
                R.raw.onboarding_care,
                R.drawable.ic_care,
                getString(R.string.onboarding_care_title),
                getString(R.string.onboarding_care_body),
            ),
            // Blocking consent page — always last; Agree is the only way out.
            OnboardingPage(
                R.raw.onboarding_diagnose,
                R.drawable.ic_shield_check,
                getString(R.string.consent_title),
                getString(R.string.consent_body),
                linkText = getString(R.string.consent_privacy_link),
                linkUrl = getString(R.string.consent_privacy_url),
            ),
        )
        val consentIndex = pages.size - 1

        binding.viewPager.adapter = OnboardingAdapter(pages)
        setupIndicators(pages)
        updateIndicators(0)

        binding.viewPager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                updateIndicators(position)
                val onConsent = position == consentIndex
                binding.btnNext.text = if (onConsent) {
                    getString(R.string.consent_agree)
                } else {
                    getString(R.string.onboarding_next)
                }
                // The consent page cannot be skipped (prominent disclosure).
                binding.btnSkip.visibility = if (onConsent) View.GONE else View.VISIBLE
            }
        })

        binding.btnNext.setOnClickListener {
            val current = binding.viewPager.currentItem
            if (current < consentIndex) {
                binding.viewPager.currentItem = current + 1
            } else {
                finishOnboarding()
            }
        }

        // Skip fast-forwards the tour but still lands on the consent page —
        // the onboarding-done flag is only ever written behind Agree.
        binding.btnSkip.setOnClickListener {
            binding.viewPager.currentItem = consentIndex
        }
    }

    /** One icon-circle per page: the page's glyph inside a tinted dot, so the
     *  indicator itself previews the tour (lens → diagnosis → care → shield)
     *  instead of being an anonymous row of colored dots. */
    private fun setupIndicators(pages: List<OnboardingPage>) {
        binding.indicatorLayout.removeAllViews()
        indicators.clear()
        val pad = 6.dp
        pages.forEachIndexed { i, page ->
            val dot = ImageView(this).apply {
                layoutParams = LinearLayout.LayoutParams(28.dp, 28.dp).apply {
                    marginStart = if (i == 0) 0 else 10.dp
                    gravity = Gravity.CENTER_VERTICAL
                }
                background = ContextCompat.getDrawable(this@OnboardingActivity, R.drawable.bg_indicator_dot)
                setImageResource(page.iconRes)
                imageTintList = ContextCompat.getColorStateList(
                    this@OnboardingActivity,
                    R.color.indicator_icon_tint,
                )
                scaleType = ImageView.ScaleType.FIT_CENTER
                setPadding(pad, pad, pad, pad)
                // Decorative: the pager itself announces page changes.
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }
            indicators.add(dot)
            binding.indicatorLayout.addView(dot)
        }
    }

    private fun updateIndicators(selected: Int) {
        if (selected == currentSelected) return
        val previous = currentSelected
        currentSelected = selected

        indicators.forEachIndexed { index, view ->
            view.isSelected = index == selected
            if (index != selected && index != previous) {
                view.scaleX = 1f
                view.scaleY = 1f
            }
        }

        indicatorAnimator?.cancel()
        val expanding = indicators.getOrNull(selected) ?: return
        val shrinking = indicators.getOrNull(previous)

        indicatorAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 220
            interpolator = AccelerateDecelerateInterpolator()
            addUpdateListener { anim ->
                val t = anim.animatedValue as Float
                val grow = 1f + SELECTED_INDICATOR_SCALE * t
                expanding.scaleX = grow
                expanding.scaleY = grow
                if (shrinking != null) {
                    val shrink = 1f + SELECTED_INDICATOR_SCALE * (1f - t)
                    shrinking.scaleX = shrink
                    shrinking.scaleY = shrink
                }
            }
            start()
        }
    }

    override fun onDestroy() {
        indicatorAnimator?.cancel()
        indicatorAnimator = null
        super.onDestroy()
    }

    private fun finishOnboarding() {
        // Reaching here means the user pressed Agree on the consent page
        // (it's the only finishing affordance), so both flags are written
        // together — the upgrade-path dialog in MainActivity covers installs
        // that completed onboarding before the consent page existed.
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ONBOARDING_DONE, true)
            .putBoolean(KEY_DATA_CONSENT, true)
            .apply()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private val Int.dp: Int get() = (this * resources.displayMetrics.density).toInt()

    companion object {
        const val PREFS_NAME = "plantdiseases_prefs"
        const val KEY_ONBOARDING_DONE = "onboarding_done"
        const val KEY_DATA_CONSENT = "data_consent_given"

        /** Extra scale applied to the selected indicator circle (1.0 → 1.2). */
        private const val SELECTED_INDICATOR_SCALE = 0.2f

        fun isOnboardingDone(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getBoolean(KEY_ONBOARDING_DONE, false)

        fun isDataConsentGiven(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getBoolean(KEY_DATA_CONSENT, false)

        fun setDataConsentGiven(context: Context) {
            context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_DATA_CONSENT, true)
                .apply()
        }
    }
}

/** One onboarding slide: a Lottie animation plus its title and body copy.
 *  [iconRes] is the glyph shown inside this page's indicator circle.
 *  [linkText]/[linkUrl] render a tappable link (the consent page's privacy
 *  policy) when set. */
data class OnboardingPage(
    val lottieRes: Int,
    val iconRes: Int,
    val title: String,
    val description: String,
    val linkText: String? = null,
    val linkUrl: String? = null,
)

/** Binds [OnboardingPage]s into the onboarding pager, playing each Lottie animation. */
class OnboardingAdapter(
    private val pages: List<OnboardingPage>
) : RecyclerView.Adapter<OnboardingAdapter.ViewHolder>() {

    inner class ViewHolder(val binding: ItemOnboardingPageBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemOnboardingPageBinding.inflate(
            LayoutInflater.from(parent.context),
            parent,
            false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val page = pages[position]
        holder.binding.apply {
            lottieAnimation.setAnimation(page.lottieRes)
            lottieAnimation.playAnimation()
            tvTitle.text = page.title
            tvDescription.text = page.description
            if (page.linkText != null && page.linkUrl != null) {
                tvLink.text = page.linkText
                tvLink.visibility = View.VISIBLE
                tvLink.setOnClickListener {
                    it.context.startActivity(
                        Intent(Intent.ACTION_VIEW, android.net.Uri.parse(page.linkUrl)),
                    )
                }
            } else {
                tvLink.visibility = View.GONE
                tvLink.setOnClickListener(null)
            }
        }
    }

    override fun getItemCount() = pages.size
}
