package com.plantdiseases.app.ui.profile

import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentProfileBinding
import com.plantdiseases.app.ui.onboarding.OnboardingActivity
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ServerConfig
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

        // How to Use — re-open onboarding
        binding.cardHowToUse.setOnClickListener {
            startActivity(Intent(requireContext(), OnboardingActivity::class.java))
        }

        // Profile empty state CTA
        binding.btnStartScan.setOnClickListener {
            findNavController().navigate(R.id.cameraFragment)
        }

        setupThemeChips()
        setupServerUrlEditor()
        loadStats()
        checkServerHealth()
    }

    private fun setupServerUrlEditor() {
        val current = ServerConfig.getBaseUrl(requireContext())
        binding.etServerUrl.setText(current)

        binding.btnSaveServerUrl.setOnClickListener {
            val raw = binding.etServerUrl.text?.toString().orEmpty()
            val normalised = ServerConfig.setBaseUrl(requireContext(), raw)
            if (normalised == null) {
                binding.tilServerUrl.error = getString(R.string.server_url_invalid)
                return@setOnClickListener
            }
            binding.tilServerUrl.error = null
            binding.etServerUrl.setText(normalised)
            Toast.makeText(requireContext(), R.string.server_url_saved, Toast.LENGTH_SHORT).show()
            checkServerHealth()
        }

        binding.btnResetServerUrl.setOnClickListener {
            val defaultUrl = ServerConfig.resetToDefault(requireContext())
            binding.tilServerUrl.error = null
            binding.etServerUrl.setText(defaultUrl)
            Toast.makeText(requireContext(), R.string.server_url_reset, Toast.LENGTH_SHORT).show()
            checkServerHealth()
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

        binding.chipThemeLight.setOnClickListener {
            ThemeHelper.saveTheme(requireContext(), ThemeHelper.THEME_LIGHT)
        }
        binding.chipThemeDark.setOnClickListener {
            ThemeHelper.saveTheme(requireContext(), ThemeHelper.THEME_DARK)
        }
        binding.chipThemeSystem.setOnClickListener {
            ThemeHelper.saveTheme(requireContext(), ThemeHelper.THEME_SYSTEM)
        }
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

            if (stats.totalScans == 0) {
                binding.statsLayout.visibility = View.GONE
                binding.emptyState.visibility = View.VISIBLE
                binding.btnClearHistory.visibility = View.GONE
                return@launch
            }

            binding.statsLayout.visibility = View.VISIBLE
            binding.emptyState.visibility = View.GONE
            binding.btnClearHistory.visibility = View.VISIBLE

            binding.tvTotalScans.text = stats.totalScans.toString()
            binding.tvHealthyCount.text = stats.healthyCount.toString()
            binding.tvDiseasedCount.text = stats.diseasedCount.toString()

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

        lifecycleScope.launch {
            val result = app.scanRepository.checkServerHealth()
            if (_binding == null) return@launch

            result.onSuccess {
                binding.tvServerStatus.text = getString(R.string.server_online)
                setStatusIndicatorColor(R.color.healthy_green)
            }.onFailure {
                binding.tvServerStatus.text = getString(R.string.server_offline)
                setStatusIndicatorColor(R.color.disease_red)
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
