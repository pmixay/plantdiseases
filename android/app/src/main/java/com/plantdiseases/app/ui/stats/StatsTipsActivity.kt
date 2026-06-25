package com.plantdiseases.app.ui.stats

import android.content.Context
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityStatsTipsBinding
import com.plantdiseases.app.ui.widget.BarChartView
import com.plantdiseases.app.ui.widget.DonutChartView
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import java.util.Calendar
import java.util.Locale

/**
 * Stats & Tips — graph-driven personal stats (healthy/diseased donut, a
 * 7-day scans bar chart, top-issue bars) from local scan history + garden,
 * plus an expandable bilingual tips library. No chart dependency: the charts
 * are custom Canvas views (see [DonutChartView] / [BarChartView]).
 */
class StatsTipsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityStatsTipsBinding
    private val isRu: Boolean get() = Locale.getDefault().language == "ru"

    private val scanRepository: ScanRepository by inject()
    private val plantRepository: PlantRepository by inject()

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
            // Pad the app-bar container so the toolbar sits fully below the
            // status bar instead of being squeezed inside its fixed height.
            binding.appbar.updatePadding(top = bars.top)
            insets
        }
        binding.statsToolbar.setNavigationOnClickListener { finish() }

        loadStats()
        buildTips()
    }

    // ── Personal stats (local data only) ────────────────────────────
    private fun loadStats() {
        lifecycleScope.launch {
            val scans = runCatching { scanRepository.getAllScans() }
                .getOrDefault(emptyList())
            val plants = runCatching { plantRepository.getPlants() }
                .getOrDefault(emptyList())
            val real = scans.filter { !it.isNotAPlant }
            val healthy = real.count { it.isHealthy }
            val diseased = real.size - healthy

            binding.tvStatsEmpty.visibility = if (real.isEmpty()) View.VISIBLE else View.GONE

            // Donut: healthy vs diseased, total in the center.
            binding.donutHealth.setTextColors(
                color(R.color.on_surface),
                color(R.color.on_surface_secondary),
            )
            binding.donutHealth.setSegments(
                listOf(
                    DonutChartView.Segment(healthy.toFloat(), color(R.color.healthy_green)),
                    DonutChartView.Segment(diseased.toFloat(), color(R.color.disease_red)),
                ),
                centerText = real.size.toString(),
                centerSubText = getString(R.string.stats_scans_label),
            )
            binding.tvLegendHealthy.text =
                getString(R.string.stats_legend_healthy, healthy)
            binding.tvLegendDiseased.text =
                getString(R.string.stats_legend_diseased, diseased)

            // 7-day bar chart.
            binding.barWeek.setBars(
                weeklyBars(real),
                barColor = color(R.color.primary),
                labelColor = color(R.color.on_surface_secondary),
                valueColor = color(R.color.on_surface),
            )

            // Top issues as horizontal bars.
            renderTopIssues(real)

            binding.tvGardenCount.text =
                resources.getQuantityString(
                    R.plurals.stats_garden_plants_value,
                    plants.size,
                    plants.size,
                )
        }
    }

    /** Count of real scans per day for the last 7 days (oldest → today). */
    private fun weeklyBars(scans: List<ScanHistoryItem>): List<BarChartView.Bar> {
        val cal = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val labels = resources.getStringArray(R.array.weekday_initials)
        val days = ArrayList<Pair<Long, Long>>(7) // [start, end) per day
        val dayLabels = ArrayList<String>(7)
        val start = cal.clone() as Calendar
        start.add(Calendar.DAY_OF_YEAR, -6)
        for (i in 0 until 7) {
            val dayStart = start.timeInMillis
            start.add(Calendar.DAY_OF_YEAR, 1)
            days.add(dayStart to start.timeInMillis)
            val dow = ((start.clone() as Calendar).apply { add(Calendar.DAY_OF_YEAR, -1) })
                .get(Calendar.DAY_OF_WEEK) // 1=Sun..7=Sat
            dayLabels.add(labels[dow - 1])
        }
        return days.mapIndexed { i, (s, e) ->
            BarChartView.Bar(
                dayLabels[i],
                scans.count { it.timestamp in s until e },
            )
        }
    }

    private fun renderTopIssues(scans: List<ScanHistoryItem>) {
        val list = binding.listTopIssues
        list.removeAllViews()
        val top = scans.filter { !it.isHealthy }
            .groupingBy { if (isRu) it.diseaseNameRu else it.diseaseName }
            .eachCount().entries
            .sortedByDescending { it.value }
            .take(4)
        binding.tvTopIssuesTitle.visibility = if (top.isEmpty()) View.GONE else View.VISIBLE
        if (top.isEmpty()) return
        val max = top.first().value
        top.forEach { (name, n) -> list.addView(issueBar(name, n, max)) }
    }

    /** One horizontal "issue" bar: label over a proportional fill track. */
    private fun issueBar(name: String, value: Int, max: Int): View {
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(6), 0, dp(6))
        }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(
            TextView(this).apply {
                text = name
                textSize = 13f
                setTextColor(color(R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
        )
        header.addView(
            TextView(this).apply {
                text = value.toString()
                textSize = 13f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(R.color.on_surface))
            }
        )
        column.addView(header)

        // Track + proportional fill.
        val track = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(0x14000000)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(8),
            ).apply { topMargin = dp(4) }
        }
        val fillWeight = value.toFloat() / max.coerceAtLeast(1)
        track.addView(
            View(this).apply {
                setBackgroundColor(color(R.color.disease_red))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, fillWeight)
            }
        )
        track.addView(
            View(this).apply {
                layoutParams = LinearLayout.LayoutParams(
                    0,
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    (1f - fillWeight).coerceAtLeast(0f),
                )
            }
        )
        column.addView(track)
        return column
    }

    private fun color(res: Int): Int = ContextCompat.getColor(this, res)

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
            setCardBackgroundColor(color(R.color.surface))
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
            setTextColor(color(R.color.on_surface_secondary))
            setPadding(0, dp(8), 0, 0)
            visibility = View.GONE
        }
        val chevron = ImageView(this).apply {
            setImageResource(R.drawable.ic_chevron_down)
            imageTintList = android.content.res.ColorStateList.valueOf(
                color(R.color.on_surface_secondary),
            )
        }
        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(
                TextView(this@StatsTipsActivity).apply {
                    text = getString(titleRes)
                    textSize = 15f
                    setTypeface(typeface, Typeface.BOLD)
                    setTextColor(color(R.color.on_surface))
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                }
            )
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
