package com.plantdiseases.app.ui.guide

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.databinding.ActivityGuideDetailBinding
import com.plantdiseases.app.util.LocaleHelper

/**
 * Full-screen documentation for one [com.plantdiseases.app.data.model.GuideItem]
 * — the "detailed description on enter" target for the Home disease/guide
 * carousels. Renders the same bilingual content as the Guide tab's bottom
 * sheet (via [GuideContentRenderer]) but as a dedicated, deep-linkable screen
 * with a back arrow and a "scan for this" CTA.
 */
class GuideDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityGuideDetailBinding

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityGuideDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.guideDetailToolbar.updatePadding(top = bars.top)
            insets
        }
        binding.guideDetailToolbar.setNavigationOnClickListener { finish() }

        val itemId = intent.getStringExtra(EXTRA_GUIDE_ID)
        val item = GuideDataProvider.getGuideItems().firstOrNull { it.id == itemId }
        if (item == null) {
            finish()
            return
        }
        val isRu = LocaleHelper.isRussian(this)

        val title = if (isRu) item.titleRu else item.titleEn
        binding.guideDetailToolbar.title = title
        binding.tvGuideTitle.text = title
        binding.ivGuideIcon.setImageResource(item.iconRes)

        val desc = if (isRu) item.descriptionRu else item.descriptionEn
        if (desc.isBlank()) {
            binding.tvGuideDesc.visibility = View.GONE
        } else {
            binding.tvGuideDesc.text = desc
        }

        GuideContentRenderer.render(
            binding.llGuideSections, if (isRu) item.contentRu else item.contentEn,
        )
        GuideDataProvider.exampleImageUrl(item.id)?.let { url ->
            binding.llGuideSections.addView(
                GuideContentRenderer.heroPhoto(this, url, title), 0,
            )
        }

        binding.btnGuideScan.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    putExtra(MainActivity.EXTRA_OPEN_SCANNER, true)
                },
            )
        }
    }

    companion object {
        const val EXTRA_GUIDE_ID = "extra_guide_id"

        fun start(context: Context, guideId: String) {
            context.startActivity(
                Intent(context, GuideDetailActivity::class.java)
                    .putExtra(EXTRA_GUIDE_ID, guideId),
            )
        }
    }
}
