package com.plantdiseases.app.ui.profile

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.text.TextUtils
import android.transition.TransitionManager
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.doOnAttach
import androidx.core.view.updatePadding
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.FragmentProfileBinding
import com.plantdiseases.app.ui.result.CommunityOpinionSheet
import com.plantdiseases.app.ui.widget.BarChartView
import com.plantdiseases.app.util.AutosavePrefs
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.util.ThemeHelper
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import java.util.Calendar

class ProfileFragment : Fragment() {

    private val scanRepository: ScanRepository by inject()

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!
    private var isFirstLoad = true
    private var myOpinionsExpanded = true

    // Count-up animators, cancelled in onDestroyView so they don't keep
    // ticking against a detached TextView (transient view retention + wasted
    // frames after the view is gone).
    private val countAnimators = mutableListOf<android.animation.ValueAnimator>()

    // Legacy (API ≤ 28) storage-permission request backing the autosave
    // toggle. On Android 10+ no permission is needed, so this never fires.
    private val storagePermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (_binding == null) return@registerForActivityResult
        if (granted) {
            AutosavePrefs.setEnabled(requireContext(), true)
        } else {
            AutosavePrefs.setEnabled(requireContext(), false)
            binding.switchAutosave.isChecked = false
            Toast.makeText(
                requireContext(),
                R.string.autosave_permission_needed,
                Toast.LENGTH_LONG,
            ).show()
        }
    }

    private val opinionNotificationLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (_binding == null) return@registerForActivityResult
        OpinionPrefs.setNotificationsEnabled(requireContext(), granted)
        binding.switchOpinionNotifications.isChecked = granted
        if (granted) startOpinionDelivery()
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentProfileBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        applyStatusBarTopInset(binding.root)
        binding.tvAppVersion.text = "v${BuildConfig.VERSION_NAME}"

        // "Clear all history" now lives on the Gallery tab's top-app-bar
        // basket, next to the scans it actually wipes.

        // Light Meter was removed from this screen — it now lives
        // on the plant detail with per-plant context. The
        // corresponding cardLightMeter view was deleted from the
        // layout XML in the same pass.

        // Profile empty state CTA
        binding.btnStartScan.setOnClickListener {
            // Select the Scan bottom-nav tab (routes through MainActivity) rather
            // than navigating the camera into Profile's own back stack.
            requireActivity().findViewById<BottomNavigationView>(R.id.bottom_navigation)
                ?.selectedItemId = R.id.cameraFragment
        }

        // Tips library lives inline now (the standalone Stats & tips screen
        // was removed). Static content — build once.
        buildTips()

        binding.langEn.setOnClickListener { setLanguage("en") }
        binding.langRu.setOnClickListener { setLanguage("ru") }
        highlightActiveLanguage()

        setupThemeChips()
        setupAutosaveToggle()
        setupCommunityControls()
        setupMyOpinionsFold()
        // loadStats() runs in onResume(), which fires immediately after this on
        // first show and again on every return — calling it here too would issue
        // a duplicate getStats() DB read on every fragment creation.
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
        binding.switchOpinionNotifications.isChecked =
            OpinionPrefs.notificationsEnabled(ctx)
        binding.switchOpinionNotifications.setOnCheckedChangeListener { _, checked ->
            if (checked) {
                enableOpinionNotifications()
            } else {
                OpinionPrefs.setNotificationsEnabled(ctx, false)
                androidx.work.WorkManager.getInstance(ctx).cancelUniqueWork("opinion_check")
                androidx.work.WorkManager.getInstance(ctx).cancelUniqueWork("opinion_check_now")
            }
        }
        // Community sharing is opt-in in context (the result screen's similar-
        // scans card / the Nearby screen). Keep the destructive
        // delete-my-shared-data control here.
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

    private fun enableOpinionNotifications() {
        val ctx = requireContext()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            opinionNotificationLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        OpinionPrefs.setNotificationsEnabled(ctx, true)
        startOpinionDelivery()
    }

    private fun startOpinionDelivery() {
        val app = requireActivity().application as com.plantdiseases.app.PlantDiseasesApp
        app.scheduleOpinionChecks()
        app.checkOpinionsNow()
        com.plantdiseases.app.push.PushManager.registerIfEnabled(requireContext())
    }

    private fun deleteCommunityContributions() {
        val pseudoId = TelemetryIds.userPseudoId(requireContext())
        lifecycleScope.launch {
            val result = scanRepository.deleteCommunityContributions(pseudoId)
            val msg = if (result.isSuccess) {
                R.string.community_delete_done
            } else {
                R.string.community_delete_failed
            }
            Toast.makeText(requireContext(), msg, Toast.LENGTH_SHORT).show()
        }
    }

    /** Load the second opinions this install has authored into the profile
     *  card: a headline count, a "what you flag most" mini bar chart, and a
     *  styled list of recent opinions. Failure / empty both collapse to the
     *  empty-state line. */
    private fun loadMyOpinions() {
        val pseudoId = TelemetryIds.userPseudoId(requireContext())
        val isRu = LocaleHelper.isRussian(requireContext())
        lifecycleScope.launch {
            val result = scanRepository.getMyCommunityOpinions(pseudoId)
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
        head.addView(
            TextView(ctx).apply {
                text = label
                textSize = 13f
                setTextColor(color(R.color.on_surface))
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
        )
        head.addView(
            TextView(ctx).apply {
                text = count.toString()
                textSize = 13f
                setTextColor(color(R.color.on_surface_secondary))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = 8.dp() }
            }
        )

        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                8.dp(),
            ).apply { topMargin = 5.dp() }
            background = pill(color(R.color.divider))
        }
        val fillWeight = count.toFloat().coerceAtLeast(0.0001f)
        val restWeight = (maxCount - count).toFloat().coerceAtLeast(0f)
        track.addView(
            View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, fillWeight)
                background = pill(color(R.color.primary))
            }
        )
        if (restWeight > 0f) {
            track.addView(
                View(ctx).apply {
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, restWeight)
                }
            )
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

        row.addView(
            View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(8.dp(), 8.dp()).apply {
                    topMargin = 6.dp()
                    marginEnd = 10.dp()
                }
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(color(R.color.primary))
                }
            }
        )

        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        col.addView(
            TextView(ctx).apply {
                text = label
                textSize = 14f
                setTextColor(color(R.color.on_surface))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            }
        )
        if (op.note.isNotBlank()) {
            col.addView(
                TextView(ctx).apply {
                    text = op.note
                    textSize = 13f
                    setTextColor(color(R.color.on_surface_secondary))
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 2.dp() }
                }
            )
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

    // ── Inline stats detail (7-day bars, top issues) + tips library ──────
    /** Real-scan count per day for the last 7 days (oldest → today). */
    private fun weeklyBars(scans: List<ScanHistoryItem>): List<BarChartView.Bar> {
        val labels = resources.getStringArray(R.array.weekday_initials)
        val start = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            add(Calendar.DAY_OF_YEAR, -6)
        }
        return (0 until 7).map {
            val dayStart = start.timeInMillis
            val dow = start.get(Calendar.DAY_OF_WEEK) // 1=Sun..7=Sat
            start.add(Calendar.DAY_OF_YEAR, 1)
            val dayEnd = start.timeInMillis
            BarChartView.Bar(labels[dow - 1], scans.count { it.timestamp in dayStart until dayEnd })
        }
    }

    private fun renderTopIssues(scans: List<ScanHistoryItem>) {
        val isRu = LocaleHelper.isRussian(requireContext())
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
        val ctx = requireContext()
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 6.dp(), 0, 6.dp())
        }
        val header = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(
            TextView(ctx).apply {
                text = name
                textSize = 13f
                setTextColor(color(R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            },
        )
        header.addView(
            TextView(ctx).apply {
                text = value.toString()
                textSize = 13f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(R.color.on_surface))
            },
        )
        column.addView(header)

        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(0x14000000)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                8.dp(),
            ).apply { topMargin = 4.dp() }
        }
        val fillWeight = value.toFloat() / max.coerceAtLeast(1)
        track.addView(
            View(ctx).apply {
                setBackgroundColor(color(R.color.disease_red))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, fillWeight)
            },
        )
        track.addView(
            View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(
                    0,
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    (1f - fillWeight).coerceAtLeast(0f),
                )
            },
        )
        column.addView(track)
        return column
    }

    /** The trimmed tips library (4 cards) — static, built once. */
    private fun buildTips() {
        val sections = listOf(
            R.string.tip_scanning_title to R.string.tip_scanning_body,
            R.string.tip_results_title to R.string.tip_results_body,
            R.string.tip_watering_title to R.string.tip_watering_body,
            R.string.tip_community_title to R.string.tip_community_body,
        )
        binding.listTips.removeAllViews()
        sections.forEach { (title, body) -> binding.listTips.addView(tipCard(title, body)) }
    }

    private fun tipCard(titleRes: Int, bodyRes: Int): View {
        val ctx = requireContext()
        val card = MaterialCardView(ctx).apply {
            radius = 16.dp().toFloat()
            cardElevation = 1.dp().toFloat()
            setCardBackgroundColor(color(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 10.dp() }
        }
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
        }
        val bodyView = TextView(ctx).apply {
            text = getString(bodyRes)
            textSize = 14f
            setLineSpacing(3.dp().toFloat(), 1f)
            setTextColor(color(R.color.on_surface_secondary))
            setPadding(0, 8.dp(), 0, 0)
            visibility = View.GONE
        }
        val chevron = ImageView(ctx).apply {
            setImageResource(R.drawable.ic_chevron_down)
            imageTintList = android.content.res.ColorStateList.valueOf(
                color(R.color.on_surface_secondary),
            )
        }
        val headerRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(
                TextView(ctx).apply {
                    text = getString(titleRes)
                    textSize = 15f
                    setTypeface(typeface, Typeface.BOLD)
                    setTextColor(color(R.color.on_surface))
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                },
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

    /** Autosave-to-album toggle. On Android 9 and below, enabling it first
     *  requests WRITE_EXTERNAL_STORAGE (scoped storage on 10+ needs none);
     *  the preference is only committed once any required grant succeeds. */
    private fun setupAutosaveToggle() {
        val sw = binding.switchAutosave
        sw.isChecked = AutosavePrefs.enabled(requireContext())
        sw.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) {
                val needsLegacyPermission = Build.VERSION.SDK_INT <= Build.VERSION_CODES.P &&
                    ContextCompat.checkSelfPermission(
                        requireContext(),
                        Manifest.permission.WRITE_EXTERNAL_STORAGE,
                    ) != PackageManager.PERMISSION_GRANTED
                if (needsLegacyPermission) {
                    storagePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                } else {
                    AutosavePrefs.setEnabled(requireContext(), true)
                }
            } else {
                AutosavePrefs.setEnabled(requireContext(), false)
            }
        }
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
        val isRu = LocaleHelper.isRussian(requireContext())

        // Show shimmer on first load
        if (isFirstLoad) {
            binding.shimmerStats.visibility = View.VISIBLE
            binding.shimmerStats.startShimmer()
            binding.statsLayout.visibility = View.GONE
        }

        // viewLifecycleOwner scope: cancels on onDestroyView so the
        // continuation never resumes against a null binding (NPE on tab
        // switch / config change mid-query).
        viewLifecycleOwner.lifecycleScope.launch {
            val stats = scanRepository.getStats()
            // Full list (the bar chart + top-issues need per-scan data the
            // aggregate ScanStats doesn't carry).
            val real = runCatching { scanRepository.getAllScans() }
                .getOrDefault(emptyList())
                .filter { !it.isNotAPlant }

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
                // No scans yet: hide the summary + charts, but the tips library
                // (also in stats_detail_layout) stays visible for onboarding.
                binding.statsLayout.visibility = View.GONE
                binding.statsChartsCard.visibility = View.GONE
                binding.emptyState.visibility = View.VISIBLE
                return@launch
            }

            binding.statsLayout.visibility = View.VISIBLE
            binding.statsChartsCard.visibility = View.VISIBLE
            binding.emptyState.visibility = View.GONE

            // 7-day activity bar chart + top recurring issues.
            binding.barWeek.setBars(
                weeklyBars(real),
                barColor = color(R.color.primary),
                labelColor = color(R.color.on_surface_secondary),
                valueColor = color(R.color.on_surface),
            )
            renderTopIssues(real)

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
            } else {
                0
            }
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
            countAnimators += this
            start()
        }
    }

    override fun onResume() {
        super.onResume()
        loadStats()
        loadMyOpinions()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        countAnimators.forEach { it.cancel() }
        countAnimators.clear()
        _binding = null
    }

    /** The shared toolbar is hidden on this tab (see MainActivity), so the
     *  scroll content runs edge-to-edge under the status bar. Pad the scroll
     *  view's top by the status-bar inset so the hero card drops below the
     *  system clock (status bar over the dark background, card beneath it). */
    private fun applyStatusBarTopInset(scroll: View) {
        val baseTop = scroll.paddingTop
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { v, insets ->
            val top = insets.getInsets(WindowInsetsCompat.Type.systemBars()).top
            v.updatePadding(top = baseTop + top)
            insets
        }
        // onViewCreated runs before the view is attached, so requesting insets
        // now no-ops and a tab switched-to later never gets an inset pass — the
        // hero then draws under the status bar. Request once attached instead.
        scroll.doOnAttach { ViewCompat.requestApplyInsets(it) }
    }
}
