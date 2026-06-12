package com.plantdiseases.app.ui.stats

import android.content.Context
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityStatsTipsBinding
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Stats & Tips — the combined personal-stats + documentation page opened
 * from the Home teaser card. Stats come from local data only (scan history
 * + garden); the tips library is a static bilingual string table rendered
 * as expandable cards.
 */
class StatsTipsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityStatsTipsBinding
    private val isRu: Boolean get() = Locale.getDefault().language == "ru"

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityStatsTipsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.statsToolbar.updatePadding(top = bars.top)
            insets
        }
        binding.statsToolbar.setNavigationOnClickListener { finish() }

        loadStats()
        buildTips()
    }

    // ── Personal stats (local data only) ────────────────────────────
    private fun loadStats() {
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            val scans = runCatching { app.scanRepository.getAllScans() }
                .getOrDefault(emptyList())
            val plants = runCatching { app.plantRepository.getPlants() }
                .getOrDefault(emptyList())
            val real = scans.filter { !it.isNotAPlant }
            val healthy = real.count { it.isHealthy }
            val diseased = real.size - healthy
            val healthyPct =
                if (real.isEmpty()) 0 else (healthy * 100.0 / real.size).toInt()

            val list = binding.listStats
            list.removeAllViews()
            statRow(list, getString(R.string.stats_total_scans), real.size.toString())
            statRow(
                list, getString(R.string.stats_healthy_share),
                getString(R.string.stats_healthy_share_value, healthyPct, healthy),
            )
            statRow(list, getString(R.string.stats_issues_found), diseased.toString())
            statRow(list, getString(R.string.stats_garden_plants), plants.size.toString())

            // Top issues: most frequent disease verdicts in the history.
            val top = real.filter { !it.isHealthy }
                .groupingBy { if (isRu) it.diseaseNameRu else it.diseaseName }
                .eachCount().entries.sortedByDescending { it.value }.take(3)
            if (top.isNotEmpty()) {
                sectionHeader(list, getString(R.string.stats_top_issues))
                top.forEach { (name, n) -> statRow(list, name, n.toString()) }
            }
            if (real.isEmpty()) {
                list.addView(TextView(this@StatsTipsActivity).apply {
                    text = getString(R.string.stats_empty)
                    textSize = 13f
                    setTextColor(getColor(R.color.on_surface_secondary))
                    setPadding(0, dp(8), 0, 0)
                })
            }
        }
    }

    private fun statRow(parent: LinearLayout, label: String, value: String) {
        parent.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(6), 0, dp(6))
            addView(TextView(this@StatsTipsActivity).apply {
                text = label
                textSize = 14f
                setTextColor(getColor(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            })
            addView(TextView(this@StatsTipsActivity).apply {
                text = value
                textSize = 14f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(getColor(R.color.on_surface))
            })
        })
    }

    private fun sectionHeader(parent: LinearLayout, title: String) {
        parent.addView(TextView(this).apply {
            text = title
            textSize = 13f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(getColor(R.color.on_surface))
            setPadding(0, dp(12), 0, dp(2))
        })
    }

    // ── Tips / documentation library ────────────────────────────────
    private fun buildTips() {
        val sections = listOf(
            R.string.tip_scanning_title to R.string.tip_scanning_body,
            R.string.tip_results_title to R.string.tip_results_body,
            R.string.tip_watering_title to R.string.tip_watering_body,
            R.string.tip_light_title to R.string.tip_light_body,
            R.string.tip_seasonal_title to R.string.tip_seasonal_body,
            R.string.tip_repotting_title to R.string.tip_repotting_body,
            R.string.tip_pests_title to R.string.tip_pests_body,
            R.string.tip_community_title to R.string.tip_community_body,
        )
        sections.forEach { (title, body) -> binding.listTips.addView(tipCard(title, body)) }
    }

    private fun tipCard(titleRes: Int, bodyRes: Int): View {
        val card = MaterialCardView(this).apply {
            radius = dp(16).toFloat()
            cardElevation = dp(1).toFloat()
            setCardBackgroundColor(getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(10) }
        }
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        val bodyView = TextView(this).apply {
            text = getString(bodyRes)
            textSize = 14f
            setLineSpacing(dp(3).toFloat(), 1f)
            setTextColor(getColor(R.color.on_surface_secondary))
            setPadding(0, dp(8), 0, 0)
            visibility = View.GONE
        }
        val chevron = ImageView(this).apply {
            setImageResource(R.drawable.ic_chevron_down)
            imageTintList = android.content.res.ColorStateList.valueOf(
                getColor(R.color.on_surface_secondary),
            )
        }
        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(TextView(this@StatsTipsActivity).apply {
                text = getString(titleRes)
                textSize = 15f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(getColor(R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            })
            addView(chevron)
        }
        column.addView(headerRow)
        column.addView(bodyView)
        card.addView(column)
        card.setOnClickListener {
            val open = bodyView.visibility == View.VISIBLE
            bodyView.visibility = if (open) View.GONE else View.VISIBLE
            chevron.setImageResource(
                if (open) R.drawable.ic_chevron_down else R.drawable.ic_chevron_up,
            )
        }
        return card
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
