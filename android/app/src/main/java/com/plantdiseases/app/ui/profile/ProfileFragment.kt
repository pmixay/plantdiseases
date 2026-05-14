package com.plantdiseases.app.ui.profile

import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.content.ContextCompat
import androidx.core.os.LocaleListCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentProfileBinding
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.TelemetryIds
import com.plantdiseases.app.util.ThemeHelper
import kotlinx.coroutines.launch

class ProfileFragment : Fragment() {

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!
    private var isFirstLoad = true

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

        // Light Meter
        binding.cardLightMeter.setOnClickListener {
            startActivity(Intent(requireContext(), LightMeterActivity::class.java))
        }

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
        loadStats()
        checkServerHealth()
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

    private fun switchLanguage() {
        // Two-step:
        //   1) Persist the new language for our own attachBaseContext
        //      hook — that's what every activity reads on (re)create.
        //   2) Hand the change to AppCompatDelegate so platform APIs
        //      like LocaleManager (API 33+) and the AppCompat
        //      backport (API <33) recreate the activity themselves.
        //
        // We deliberately do NOT include locale in MainActivity's
        // configChanges. Doing so would make onConfigurationChanged
        // fire while the activity's base context still pointed at
        // the previous locale — the UI then half-updated and the
        // toggle "did nothing". Letting the activity recreate keeps
        // attachBaseContext as the single source of truth for which
        // locale the resources resolve against.
        val newLang = LocaleHelper.toggleLanguage(requireContext())
        AppCompatDelegate.setApplicationLocales(
            LocaleListCompat.forLanguageTags(newLang),
        )
        // Smooth the transition: quick fade-out on the current
        // window, then recreate(). Activity.recreate itself does not
        // animate; without this the screen blinks. The new activity
        // starts opaque (theme default) so the visual is "fade to
        // dark → cut to new language", which is what users read as
        // "smooth".
        val activity = requireActivity()
        val decor = activity.window.decorView
        decor.animate()
            .alpha(0f)
            .setDuration(160)
            .withEndAction {
                // Belt-and-braces: on some OEM builds
                // setApplicationLocales does not trigger the
                // recreate before the next frame, which shows up
                // as "tapping the toggle did nothing". An explicit
                // recreate() here is idempotent — if AppCompat
                // already scheduled one we just race with it on
                // the same activity.
                activity.recreate()
            }
            .start()
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
        ThemeHelper.saveTheme(requireContext(), mode)
        // AppCompatDelegate.setDefaultNightMode (called from
        // ThemeHelper.saveTheme) flips the night-mode flag but won't
        // re-apply styled attributes on the running activity, so
        // surfaces, text colors and the status bar stay stuck on the
        // previous theme until something triggers recreate(). We
        // recreate here with the same brief fade we use for language
        // switching so the transition reads as "smooth" instead of
        // "the button did nothing".
        val activity = requireActivity()
        activity.window.decorView.animate()
            .alpha(0f)
            .setDuration(160)
            .withEndAction { activity.recreate() }
            .start()
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
        checkServerHealth()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
