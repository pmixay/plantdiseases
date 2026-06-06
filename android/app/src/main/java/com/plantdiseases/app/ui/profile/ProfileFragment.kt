package com.plantdiseases.app.ui.profile

import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.transition.TransitionManager
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
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

        binding.btnClearHistory.setOnClickListener {
            MaterialAlertDialogBuilder(requireContext())
                .setTitle(R.string.clear_history)
                .setMessage(R.string.clear_history_confirm)
                .setPositiveButton(R.string.clear_all) { _, _ -> clearHistory() }
                .setNegativeButton(R.string.cancel, null)
                .show()
        }

        // Inline "how to use" mini-guide — three step columns, each
        // tappable as a soft shortcut into the relevant tab.
        setupHowToSteps()

        // Light Meter was removed from this screen — it now lives
        // on the plant detail with per-plant context. The
        // corresponding cardLightMeter view was deleted from the
        // layout XML in the same pass.

        // Profile empty state CTA
        binding.btnStartScan.setOnClickListener {
            findNavController().navigate(R.id.cameraFragment)
        }

        binding.btnLanguage.setOnClickListener {
            switchLanguage()
        }

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
     *  card. Failure / empty both render the empty-state line. */
    private fun loadMyOpinions() {
        val app = requireActivity().application as PlantDiseasesApp
        val pseudoId = TelemetryIds.userPseudoId(requireContext())
        val isRu = LocaleHelper.isRussian(requireContext())
        lifecycleScope.launch {
            val result = app.scanRepository.getMyCommunityOpinions(pseudoId)
            val b = _binding ?: return@launch
            b.llMyOpinions.removeAllViews()
            val opinions = result.getOrNull()?.opinions.orEmpty()
            if (opinions.isEmpty()) {
                b.tvMyOpinionsEmpty.visibility = View.VISIBLE
            } else {
                b.tvMyOpinionsEmpty.visibility = View.GONE
                opinions.forEach { b.llMyOpinions.addView(opinionRow(it, isRu)) }
            }
        }
    }

    private fun opinionRow(op: CommunityOpinion, isRu: Boolean): View {
        val ctx = requireContext()
        val label = diseaseLabel(op.suggestedDisease, isRu)
        val rowText = if (op.note.isBlank()) "•  $label" else "•  $label — ${op.note}"
        return TextView(ctx).apply {
            text = rowText
            textSize = 14f
            setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
            val v = (6 * resources.displayMetrics.density).toInt()
            setPadding(0, v, 0, v)
            if (op.scanId.isNotBlank()) {
                isClickable = true
                setOnClickListener {
                    CommunityOpinionSheet.show(parentFragmentManager, op.scanId, label)
                }
            }
        }
    }

    /** Class key -> localized display name via the guide article title. */
    private fun diseaseLabel(key: String, isRu: Boolean): String {
        val guideId = GuideDataProvider.guideIdForDisease(key)
        val item = guideId?.let { id ->
            GuideDataProvider.getGuideItems().firstOrNull { it.id == id }
        }
        return item?.let { if (isRu) it.titleRu else it.titleEn } ?: key
    }

    private fun switchLanguage() {
        // Locale is owned entirely by LocaleHelper: every activity's
        // attachBaseContext re-reads the saved language and wraps its
        // context (see MainActivity / ResultActivity / …), so persisting
        // the new value and recreating the host is all that's needed —
        // the rebuilt activity resolves every resource in the new locale.
        //
        // We must NOT also call AppCompatDelegate.setApplicationLocales()
        // here. That spins up a *second*, framework-level locale change
        // (LocaleManager on API 33+, the AppCompat backport below it) that
        // raced our own attachBaseContext locale wrapping and left the
        // window stuck on a black frame — the "app turns black" bug. One
        // mechanism, one recreate. Theme switching stays smooth for the
        // same reason: it only flips night-mode, never the locale.
        LocaleHelper.toggleLanguage(requireContext())
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

    private fun setupHowToSteps() {
        // The three step columns are read-only — they were tappable
        // shortcuts in v1, but users reported that intercepted
        // bottom-nav swipes and made it hard to switch tabs after
        // scrolling the profile screen. We now leave them as plain
        // informational cards; the bottom nav is the canonical way
        // to switch tabs.
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
                binding.btnClearHistory.visibility = View.GONE
                return@launch
            }

            binding.statsLayout.visibility = View.VISIBLE
            binding.emptyState.visibility = View.GONE
            binding.btnClearHistory.visibility = View.VISIBLE

            // The "Total Scans" card surfaces the full recorded count so
            // it matches the gallery row count; NAP filtering only affects
            // health-rate and most-common-disease semantics.
            binding.tvTotalScans.text = totalRecorded.toString()
            binding.tvHealthyCount.text = stats.healthyCount.toString()
            binding.tvDiseasedCount.text = stats.diseasedCount.toString()

            // Health rate is over plant scans only — NAP rows don't fail
            // a plant health check, they're a UX retake prompt.
            val healthRate = if (stats.totalScans > 0) {
                "${(stats.healthyCount * 100 / stats.totalScans)}%"
            } else "—"
            binding.tvHealthRate.text = healthRate

            val commonDisease = if (isRu) stats.mostCommonDiseaseRu else stats.mostCommonDisease
            binding.tvCommonDisease.text = commonDisease ?: getString(R.string.none_yet)
        }
    }

    private fun clearHistory() {
        val app = requireActivity().application as PlantDiseasesApp
        lifecycleScope.launch {
            app.scanRepository.clearAll()
            Toast.makeText(requireContext(), R.string.history_cleared, Toast.LENGTH_SHORT).show()
            loadStats()
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
