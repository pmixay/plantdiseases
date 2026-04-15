package com.plantdiseases.app.ui.onboarding

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
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

class OnboardingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityOnboardingBinding
    private val indicators = mutableListOf<View>()

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityOnboardingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val isRu = LocaleHelper.isRussian(this)

        val pages = listOf(
            OnboardingPage(
                R.raw.onboarding_scan,
                if (isRu) "Сканируй" else "Scan",
                if (isRu) "Наведите камеру на лист растения и сделайте фото за пару секунд"
                else "Point your camera at a plant leaf and take a photo in seconds"
            ),
            OnboardingPage(
                R.raw.onboarding_diagnose,
                if (isRu) "Узнай болезнь" else "Get Diagnosis",
                if (isRu) "Наш ИИ проанализирует фото и определит болезнь с высокой точностью"
                else "Our AI analyzes the photo and identifies the disease with high accuracy"
            ),
            OnboardingPage(
                R.raw.onboarding_treat,
                if (isRu) "Получи лечение" else "Get Treatment",
                if (isRu) "Получите пошаговые рекомендации по лечению и профилактике"
                else "Get step-by-step treatment and prevention recommendations"
            )
        )

        binding.viewPager.adapter = OnboardingAdapter(pages)
        setupIndicators(pages.size)
        updateIndicators(0)

        binding.viewPager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                updateIndicators(position)
                binding.btnNext.text = if (position == pages.size - 1) {
                    getString(R.string.onboarding_start)
                } else {
                    getString(R.string.onboarding_next)
                }
            }
        })

        binding.btnNext.setOnClickListener {
            val current = binding.viewPager.currentItem
            if (current < pages.size - 1) {
                binding.viewPager.currentItem = current + 1
            } else {
                finishOnboarding()
            }
        }

        binding.btnSkip.setOnClickListener {
            finishOnboarding()
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
        indicators.forEachIndexed { index, view ->
            view.isSelected = index == selected
            val size = if (index == selected) 12.dp else 10.dp
            view.layoutParams = (view.layoutParams as LinearLayout.LayoutParams).apply {
                width = if (index == selected) 24.dp else 10.dp
                height = size
            }
            view.requestLayout()
        }
    }

    private fun finishOnboarding() {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ONBOARDING_DONE, true)
            .apply()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private val Int.dp: Int get() = (this * resources.displayMetrics.density).toInt()

    companion object {
        const val PREFS_NAME = "plantdiseases_prefs"
        const val KEY_ONBOARDING_DONE = "onboarding_done"

        fun isOnboardingDone(context: Context): Boolean {
            return context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .getBoolean(KEY_ONBOARDING_DONE, false)
        }
    }
}

data class OnboardingPage(
    val lottieRes: Int,
    val title: String,
    val description: String
)

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
        }
    }

    override fun getItemCount() = pages.size
}
