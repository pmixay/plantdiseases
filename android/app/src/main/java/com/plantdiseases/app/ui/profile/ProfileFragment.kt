package com.plantdiseases.app.ui.profile

import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.TextUtils
import android.transition.TransitionManager
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.databinding.FragmentProfileBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.util.ThemeHelper
import kotlinx.coroutines.launch

class ProfileFragment : Fragment() {

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!
    private var isFirstLoad = true
    private var myOpinionsExpanded = true

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentProfileBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        binding.tvAppVersion.text = "v${BuildConfig.VERSION_NAME}"

        // "Clear all history" now lives on the Gallery tab's top-app-bar
        // basket, next to the scans it actually wipes.

        // Light Meter was removed from this screen — it now lives
        // on the plant detail with per-plant context. The
        // corresponding cardLightMeter view was deleted from the
        // layout XML in the same pass.

        // Profile empty state CTA
        binding.btnStartScan.setOnClickListener {
            findNavController().navigate(R.id.cameraFragment)
        }

        binding.langEn.setOnClickListener { setLanguage("en") }
        binding.langRu.setOnClickListener { setLanguage("ru") }
        highlightActiveLanguage()

        binding.btnRefreshHealth.setOnClickListener {
            checkServerHealth()
        }

        setupThemeChips()
        setupCommunityControls()
        setupMyOpinionsFold()
        loadStats()
        checkServerHealth()
    }

    private fun setupMyOpinionsFold() {
        applyMyOpinionsFold(animate = false)
        binding.headerMyOpinions.setOnClickListener {
            myOpinionsExpanded = !myOpinionsExpanded
            applyMyOpinionsFold(animate = true)
        }
    }

    /** Show/hide the "my second opinions" body and point the chevron up
     *  (expanded) or down (collapsed). */
    private fun applyMyOpinionsFold(animate: Boolean) {
        val b = _binding ?: return
        if (animate) {
            TransitionManager.beginDelayedTransition(b.cardMyOpinions)
        }
        b.llMyOpinionsContent.visibility = if (myOpinionsExpanded) View.VISIBLE else View.GONE
        b.ivMyOpinionsChevron.rotation = if (myOpinionsExpanded) 0f else 180f
    }

    private fun setupCommunityControls() {
        val ctx = requireContext()
        // Community uploads default to ON; the switch only ever
        // turns them off. The first time a brand-new install reads
        // CommunityPrefs.uploadsEnabled(...) the underlying pref is
        // missing and the default branch returns true, so the toggle
        // renders checked even before any scan.
        binding.switchCommunity.isChecked = CommunityPrefs.uploadsEnabled(ctx)
        binding.switchCommunity.setOnCheckedChangeListener { _, isChecked ->
            CommunityPrefs.setUploadsEnabled(ctx, isChecked)
        }
        binding.btnDeleteCommunity.setOnClickListener {
            MaterialAlertDialogBuilder(ctx)
                .setTitle(R.string.community_delete_title)
                .setMessage(R.string.community_delete_body)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.delete) { _, _ ->
                    deleteCommunityContributions()
                }
                .show()
        }
    }

    private fun deleteCommunityContributions() {
        val app = requireActivity().application as PlantDiseasesApp
        val pseudoId = TelemetryIds.userPseudoId(requireContext())
        lifecycleScope.launch {
            val result = app.scanRepository.deleteCommunityContributions(pseudoId)
            val msg = if (result.isSuccess)
                R.string.community_delete_done
            else
                R.string.community_delete_failed
            Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show()
        }
    }

    /** Load the second opinions this install has authored into the profile
     *  card: a headline count, a "what you flag most" mini bar chart, and a
     *  styled list of recent opinions. Failure / empty both collapse to the
     *  empty-state line. */
    private fun loadMyOpinions() {
        val app = requireActivity().application as PlantDiseasesApp
        val pseudoId = TelemetryIds.userPseudoId(requireContext())
        val isRu = LocaleHelper.isRussian(requireContext())
        lifecycleScope.launch {
            val result = app.scanRepository.getMyCommunityOpinions(pseudoId)
            val b = _binding ?: return@launch
            b.llMyOpinions.removeAllViews()
            b.llMyOpinionsChart.removeAllViews()
            val opinions = result.getOrNull()?.opinions.orEmpty()

            if (opinions.isEmpty()) {
                b.tvMyOpinionsEmpty.visibility = View.VISIBLE
                b.llMyOpinionsStats.visibility = View.GONE
                b.tvMyOpinionsBreakdownLabel.visibility = View.GONE
                b.llMyOpinionsChart.visibility = View.GONE
                b.tvMyOpinionsRecentLabel.visibility = View.GONE
                return@launch
            }

            b.tvMyOpinionsEmpty.visibility = View.GONE

            // Headline stat — total diagnoses weighed in on. The label is a
            // static string set in XML (matches the stats card convention of
            // a big number + plain caption), so only the count is set here.
            b.tvMyOpinionsCount.text = opinions.size.toString()
            b.llMyOpinionsStats.visibility = View.VISIBLE

            // "What you flag most" bar chart — opinion distribution by class,
            // top 4. Only worth showing once there's more than one opinion.
            val byDisease = opinions
                .groupingBy { it.suggestedDisease }
                .eachCount()
                .entries
                .sortedByDescending { it.value }
                .take(4)
            if (opinions.size >= 2 && byDisease.isNotEmpty()) {
                val maxCount = byDisease.first().value
                byDisease.forEach { (key, count) ->
                    b.llMyOpinionsChart.addView(
                        chartRow(diseaseLabel(key, isRu), count, maxCount),
                    )
                }
                b.tvMyOpinionsBreakdownLabel.visibility = View.VISIBLE
                b.llMyOpinionsChart.visibility = View.VISIBLE
            } else {
                b.tvMyOpinionsBreakdownLabel.visibility = View.GONE
                b.llMyOpinionsChart.visibility = View.GONE
            }

            // Recent list (most recent first, capped so the card stays tidy).
            val recent = opinions.sortedByDescending { it.createdAt }.take(8)
            recent.forEach { b.llMyOpinions.addView(opinionRow(it, isRu)) }
            b.tvMyOpinionsRecentLabel.visibility = View.VISIBLE
        }
    }

    /** One bar in the "what you flag most" chart: a label + count above a
     *  proportional fill bar. The fill/remainder split uses layout weights so
     *  no post-measure pass is needed. */
    private fun chartRow(label: String, count: Int, maxCount: Int): View {
        val ctx = requireContext()
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 10.dp() }
        }

        val head = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        head.addView(TextView(ctx).apply {
            text = label
            textSize = 13f
            setTextColor(color(R.color.on_surface))
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        head.addView(TextView(ctx).apply {
            text = count.toString()
            textSize = 13f
            setTextColor(color(R.color.on_surface_secondary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 8.dp() }
        })

        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 8.dp(),
            ).apply { topMargin = 5.dp() }
            background = pill(color(R.color.divider))
        }
        val fillWeight = count.toFloat().coerceAtLeast(0.0001f)
        val restWeight = (maxCount - count).toFloat().coerceAtLeast(0f)
        track.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, fillWeight)
            background = pill(color(R.color.primary))
        })
        if (restWeight > 0f) {
            track.addView(View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, restWeight)
            })
        }

        root.addView(head)
        root.addView(track)
        return root
    }

    /** A recent-opinion row: a primary dot, the disease label, and the note
     *  underneath. Tapping opens the community sheet for that scan. */
    private fun opinionRow(op: CommunityOpinion, isRu: Boolean): View {
        val ctx = requireContext()
        val label = diseaseLabel(op.suggestedDisease, isRu)
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            val v = 8.dp()
            setPadding(0, v, 0, v)
        }

        row.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(8.dp(), 8.dp()).apply {
                topMargin = 6.dp()
                marginEnd = 10.dp()
            }
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(color(R.color.primary))
            }
        })

        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        col.addView(TextView(ctx).apply {
            text = label
            textSize = 14f
            setTextColor(color(R.color.on_surface))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        })
        if (op.note.isNotBlank()) {
            col.addView(TextView(ctx).apply {
                text = op.note
                textSize = 13f
                setTextColor(color(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 2.dp() }
            })
        }
        row.addView(col)

        if (op.scanId.isNotBlank()) {
            row.isClickable = true
            row.setOnClickListener {
                CommunityOpinionSheet.show(parentFragmentManager, op.scanId, label)
            }
        }
        return row
    }

    private fun color(res: Int): Int = ContextCompat.getColor(requireContext(), res)

    private fun pill(fill: Int): GradientDrawable = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = 999f
        setColor(fill)
    }

    private fun Int.dp(): Int =
        (this * resources.displayMetrics.density).toInt()

    /** Class key -> localized display name via the guide article title. */
    private fun diseaseLabel(key: String, isRu: Boolean): String {
        val guideId = GuideDataProvider.guideIdForDisease(key)
        val item = guideId?.let { id ->
            GuideDataProvider.getGuideItems().firstOrNull { it.id == id }
        }
        return item?.let { if (isRu) it.titleRu else it.titleEn } ?: key
    }

    /** Highlight the active flag segment (filled green + white text); the
     *  other stays flat with secondary text. */
    private fun highlightActiveLanguage() {
        val isRu = LocaleHelper.isRussian(requireContext())
        styleLanguageSegment(binding.langEn, binding.tvLangEn, active = !isRu)
        styleLanguageSegment(binding.langRu, binding.tvLangRu, active = isRu)
    }

    private fun styleLanguageSegment(pill: android.view.View, label: android.widget.TextView, active: Boolean) {
        pill.setBackgroundResource(if (active) R.drawable.bg_segment_active else 0)
        label.setTextColor(
            androidx.core.content.ContextCompat.getColor(
                requireContext(),
                if (active) R.color.on_primary else R.color.on_surface_secondary,
            ),
        )
    }

    private fun setLanguage(lang: String) {
        if (LocaleHelper.getSavedLanguage(requireContext()) == lang) return
        // Locale is owned entirely by LocaleHelper: every activity's
        // attachBaseContext re-reads the saved language and wraps its context,
        // so persisting the new value (setLocale saves it) and recreating the
        // host is all that's needed. We must NOT also call
        // AppCompatDelegate.setApplicationLocales() — a second, framework-level
        // locale change raced our attachBaseContext wrapping and left the
        // window on a black frame (the "app turns black" bug). One mechanism,
        // one recreate.
        LocaleHelper.setLocale(requireContext(), lang)
        requireActivity().recreate()
    }

    private fun setupThemeChips() {
        // Set initial selection
        val currentTheme = ThemeHelper.getSavedTheme(requireContext())
        when (currentTheme) {
            ThemeHelper.THEME_LIGHT -> binding.chipThemeLight.isChecked = true
            ThemeHelper.THEME_DARK -> binding.chipThemeDark.isChecked = true
            else -> binding.chipThemeSystem.isChecked = true
        }

        binding.chipThemeLight.setOnClickListener { applyTheme(ThemeHelper.THEME_LIGHT) }
        binding.chipThemeDark.setOnClickListener { applyTheme(ThemeHelper.THEME_DARK) }
        binding.chipThemeSystem.setOnClickListener { applyTheme(ThemeHelper.THEME_SYSTEM) }
    }

    private fun applyTheme(mode: Int) {
        if (ThemeHelper.getSavedTheme(requireContext()) == mode) return
        // ``ThemeHelper.saveTheme`` persists the choice and then calls
        // ``AppCompatDelegate.setDefaultNightMode``. With ``uiMode``
        // removed from MainActivity's configChanges in the manifest,
        // AppCompat now triggers the recreate itself — we don't need
        // (and must not add) a manual recreate or a fade animation.
        // The earlier ``decorView.animate { recreate() }`` hack used
        // to freeze the app: the alpha-0 fade plus a second recreate
        // racing against AppCompat's own dispatch landed the window
        // on a black-screen / unresponsive state on some devices.
        ThemeHelper.saveTheme(requireContext(), mode)
    }

    private fun loadStats() {
        val app = requireActivity().application as PlantDiseasesApp
        val isRu = LocaleHelper.isRussian(requireContext())

        // Show shimmer on first load
        if (isFirstLoad) {
            binding.shimmerStats.visibility = View.VISIBLE
            binding.shimmerStats.startShimmer()
            binding.statsLayout.visibility = View.GONE
        }

        lifecycleScope.launch {
            val stats = app.scanRepository.getStats()

            // Hide shimmer
            if (isFirstLoad) {
                binding.shimmerStats.stopShimmer()
                binding.shimmerStats.visibility = View.GONE
                isFirstLoad = false
            }

            // `totalScans` from ScanStats counts plant scans only (healthy +
            // real disease). NAP rows are tracked separately and do not
            // contribute to health rate or "most common disease" — but
            // they DO count toward the empty-state check, otherwise users
            // who only have NAP scans see an "empty" profile.
            val totalRecorded = stats.totalScans + stats.notAPlantCount
            if (totalRecorded == 0) {
                binding.statsLayout.visibility = View.GONE
                binding.emptyState.visibility = View.VISIBLE
                return@launch
            }

            binding.statsLayout.visibility = View.VISIBLE
            binding.emptyState.visibility = View.GONE

            // The "Total Scans" card surfaces the full recorded count so
            // it matches the gallery row count; NAP filtering only affects
            // health-rate and most-common-disease semantics.
            animateCount(binding.tvTotalScans, totalRecorded)
            animateCount(binding.tvHealthyCount, stats.healthyCount)
            animateCount(binding.tvDiseasedCount, stats.diseasedCount)

            // Health rate is over plant scans only — NAP rows don't fail a
            // plant health check, they're a UX retake prompt. The ring and the
            // centre % animate up together.
            val ratePct = if (stats.totalScans > 0) {
                stats.healthyCount * 100 / stats.totalScans
            } else 0
            binding.tvHealthRate.text = if (stats.totalScans > 0) "$ratePct%" else "—"
            binding.progressHealthRing.max = 100
            binding.progressHealthRing.setProgressCompat(ratePct, true)

            val commonDisease = if (isRu) stats.mostCommonDiseaseRu else stats.mostCommonDisease
            binding.tvCommonDisease.text = commonDisease ?: getString(R.string.none_yet)
        }
    }

    /** Count-up animation for a stat number (0 → target over ~0.6s). */
    private fun animateCount(tv: android.widget.TextView, target: Int) {
        if (target <= 0) {
            tv.text = "0"
            return
        }
        android.animation.ValueAnimator.ofInt(0, target).apply {
            duration = 600L
            addUpdateListener { tv.text = (it.animatedValue as Int).toString() }
            start()
        }
    }

    private fun checkServerHealth() {
        val app = requireActivity().application as PlantDiseasesApp

        binding.tvServerStatus.text = getString(R.string.server_checking)
        setStatusIndicatorColor(R.color.on_surface_secondary)
        val dash = getString(R.string.diagnostics_value_dash)
        binding.tvDiagVersion.text = dash
        binding.tvDiagClasses.text = dash

        lifecycleScope.launch {
            val result = app.scanRepository.checkServerHealth()
            if (_binding == null) return@launch

            result.onSuccess { health ->
                binding.tvServerStatus.text = getString(R.string.server_online)
                setStatusIndicatorColor(R.color.healthy_green)
                binding.tvDiagVersion.text = health.version.ifBlank { dash }
                binding.tvDiagClasses.text = if (health.numClasses > 0)
                    health.numClasses.toString() else dash
            }.onFailure {
                binding.tvServerStatus.text = getString(R.string.server_offline)
                setStatusIndicatorColor(R.color.disease_red)
                binding.tvDiagVersion.text = dash
                binding.tvDiagClasses.text = dash
            }
        }
    }

    private fun setStatusIndicatorColor(colorRes: Int) {
        val drawable = binding.serverStatusIndicator.background as? GradientDrawable
        drawable?.setColor(ContextCompat.getColor(requireContext(), colorRes))
    }

    override fun onResume() {
        super.onResume()
        loadStats()
        loadMyOpinions()
        checkServerHealth()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
