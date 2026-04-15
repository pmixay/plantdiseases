package com.plantdiseases.app.ui.profile

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentProfileBinding
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch

class ProfileFragment : Fragment() {

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!

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

        loadStats()
    }

    private fun loadStats() {
        val app = requireActivity().application as PlantDiseasesApp
        val isRu = LocaleHelper.isRussian(requireContext())

        lifecycleScope.launch {
            val stats = app.scanRepository.getStats()

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

    override fun onResume() {
        super.onResume()
        loadStats()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
