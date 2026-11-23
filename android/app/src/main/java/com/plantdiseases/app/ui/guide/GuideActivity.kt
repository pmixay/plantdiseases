package com.plantdiseases.app.ui.guide

import android.content.Context
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.plantdiseases.app.databinding.ActivityGuideBinding
import com.plantdiseases.app.util.LocaleHelper

/**
 * The plant-care guide, opened from Home's tools grid and its "Disease guide"
 * link: a back-arrow header over [GuideFragment]. A screen of its own like
 * every other pushed screen, so it uses the theme's shared push/pop motion.
 */
class GuideActivity : AppCompatActivity() {

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityGuideBinding.inflate(layoutInflater)
        setContentView(binding.root)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            binding.guideAppBar.updatePadding(top = bars.top, left = bars.left, right = bars.right)
            binding.guideContainer.updatePadding(bottom = bars.bottom, left = bars.left, right = bars.right)
            insets
        }
        binding.guideToolbar.setNavigationOnClickListener { finish() }
    }
}
