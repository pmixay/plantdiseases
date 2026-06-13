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
 * care → data consent) with an animated page indicator. The final page is a
 * BLOCKING consent disclosure (photos + feedback are used, anonymized, to
 * train our models — Play "prominent disclosure"): Skip is hidden there and
 * jumps to it from earlier pages instead of finishing, so consent can never
 * be bypassed. Agreeing sets [KEY_ONBOARDING_DONE] + [KEY_DATA_CONSENT] and
 * moves on to [MainActivity]; [isOnboardingDone] lets the launcher decide
 * whether to show this screen at all.
 */
class OnboardingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityOnboardingBinding
    private val indicators = mutableListOf<View>()
    private var currentSelected = -1
    private var widthAnimator: ValueAnimator? = null

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
                getString(R.string.onboarding_scan_title),
                getString(R.string.onboarding_scan_body),
            ),
            OnboardingPage(
                R.raw.onboarding_diagnose,
                getString(R.string.onboarding_diagnose_title),
                getString(R.string.onboarding_diagnose_body),
            ),
            OnboardingPage(
                R.raw.onboarding_care,
                getString(R.string.onboarding_care_title),
                getString(R.string.onboarding_care_body),
            ),
            // Blocking consent page — always last; Agree is the only way out.
            OnboardingPage(
                R.raw.onboarding_diagnose,
                getString(R.string.consent_title),
                getString(R.string.consent_body),
                linkText = getString(R.string.consent_privacy_link),
                linkUrl = getString(R.string.consent_privacy_url),
            ),
        )
        val consentIndex = pages.size - 1

        binding.viewPager.adapter = OnboardingAdapter(pages)
        setupIndicators(pages.size)
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

    private fun setupIndicators(count: Int) {
        binding.indicatorLayout.removeAllViews()
        indicators.clear()
        for (i in 0 until count) {
            val dot = View(this).apply {
                layoutParams = LinearLayout.LayoutParams(10.dp, 10.dp).apply {
                    marginStart = if (i == 0) 0 else 8.dp
                    gravity = Gravity.CENTER_VERTICAL
                }
                background = ContextCompat.getDrawable(this@OnboardingActivity, R.drawable.bg_indicator_dot)
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
                val lp = view.layoutParams as LinearLayout.LayoutParams
                lp.width = 10.dp
                lp.height = 10.dp
                view.layoutParams = lp
            }
        }

        widthAnimator?.cancel()
        val expanding = indicators.getOrNull(selected) ?: return
        val shrinking = indicators.getOrNull(previous)

        widthAnimator = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 220
            interpolator = AccelerateDecelerateInterpolator()
            addUpdateListener { anim ->
                val t = anim.animatedValue as Float
                applyWidth(expanding, lerp(10.dp, 24.dp, t), lerp(10.dp, 12.dp, t))
                if (shrinking != null) {
                    applyWidth(shrinking, lerp(24.dp, 10.dp, t), lerp(12.dp, 10.dp, t))
                }
            }
            start()
        }
    }

    private fun applyWidth(view: View, width: Int, height: Int) {
        val lp = view.layoutParams as LinearLayout.LayoutParams
        lp.width = width
        lp.height = height
        view.layoutParams = lp
    }

    private fun lerp(from: Int, to: Int, t: Float): Int =
        (from + (to - from) * t).toInt()

    override fun onDestroy() {
        widthAnimator?.cancel()
        widthAnimator = null
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

        fun isOnboardingDone(context: Context): Boolean {
            return context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .getBoolean(KEY_ONBOARDING_DONE, false)
        }

        fun isDataConsentGiven(context: Context): Boolean {
            return context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .getBoolean(KEY_DATA_CONSENT, false)
        }

        fun setDataConsentGiven(context: Context) {
            context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_DATA_CONSENT, true)
                .apply()
        }
    }
}

/** One onboarding slide: a Lottie animation plus its title and body copy.
 *  [linkText]/[linkUrl] render a tappable link (the consent page's privacy
 *  policy) when set. */
data class OnboardingPage(
    val lottieRes: Int,
    val title: String,
    val description: String,
    val linkText: String? = null,
    val linkUrl: String? = null,
)

/** Binds [OnboardingPage]s into the onboarding pager, playing each Lottie animation. */
class OnboardingAdapter(
    private val pages: List<OnboardingPage>
) : RecyclerView.Adapter<OnboardingAdapter.ViewHolder>() {

    inner class ViewHolder(val binding: ItemOnboardingPageBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemOnboardingPageBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
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
