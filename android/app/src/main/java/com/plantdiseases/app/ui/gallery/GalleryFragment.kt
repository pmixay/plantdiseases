package com.plantdiseases.app.ui.gallery

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.AnimationUtils
import android.view.animation.LayoutAnimationController
import android.view.animation.ScaleAnimation
import android.view.animation.AlphaAnimation
import android.view.animation.AnimationSet
import androidx.core.app.ActivityOptionsCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.GridLayoutManager
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.databinding.FragmentGalleryBinding
import com.plantdiseases.app.ui.result.ResultActivity
import kotlinx.coroutines.launch

class GalleryFragment : Fragment() {

    private var _binding: FragmentGalleryBinding? = null
    private val binding get() = _binding!!

    private lateinit var adapter: GalleryAdapter
    private var allScans: List<ScanHistoryItem> = emptyList()
    private var currentFilter = Filter.ALL
    private var isFirstLoad = true

    enum class Filter { ALL, HEALTHY, DISEASED }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentGalleryBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        adapter = GalleryAdapter(
            onClick = { item ->
                val intent = Intent(requireContext(), ResultActivity::class.java).apply {
                    putExtra(ResultActivity.EXTRA_SCAN_ID, item.id)
                }
                startActivity(intent)
            },
            onDelete = { item ->
                deleteItem(item.id)
            }
        )

        binding.rvGallery.layoutManager = GridLayoutManager(requireContext(), 2)
        binding.rvGallery.adapter = adapter

        // Stagger animation for list items (2.5)
        setupStaggerAnimation()

        binding.swipeRefresh.setOnRefreshListener {
            loadScans()
        }

        // First scan CTA button (2.7)
        binding.btnFirstScan.setOnClickListener {
            // Navigate to camera/scan tab
            val navController = findNavController()
            navController.navigate(R.id.cameraFragment)
        }

        setupFilterChips()
        loadScans()
    }

    private fun setupStaggerAnimation() {
        val scaleAnim = ScaleAnimation(
            0.9f, 1.0f, 0.9f, 1.0f,
            ScaleAnimation.RELATIVE_TO_SELF, 0.5f,
            ScaleAnimation.RELATIVE_TO_SELF, 0.5f
        )
        val alphaAnim = AlphaAnimation(0f, 1f)
        val animSet = AnimationSet(true).apply {
            addAnimation(scaleAnim)
            addAnimation(alphaAnim)
            duration = 300
        }
        val controller = LayoutAnimationController(animSet, 0.15f)
        binding.rvGallery.layoutAnimation = controller
    }

    private fun setupFilterChips() {
        binding.chipAll.setOnClickListener {
            currentFilter = Filter.ALL
            applyFilter()
        }
        binding.chipHealthy.setOnClickListener {
            vibrateLight()
            currentFilter = Filter.HEALTHY
            applyFilter()
        }
        binding.chipDiseased.setOnClickListener {
            vibrateLight()
            currentFilter = Filter.DISEASED
            applyFilter()
        }
    }

    private fun vibrateLight() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = requireContext().getSystemService(android.content.Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
                vm.defaultVibrator.vibrate(VibrationEffect.createOneShot(10, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                val v = requireContext().getSystemService(android.content.Context.VIBRATOR_SERVICE) as Vibrator
                v.vibrate(VibrationEffect.createOneShot(10, VibrationEffect.DEFAULT_AMPLITUDE))
            }
        } catch (_: Exception) {}
    }

    private fun applyFilter() {
        val filtered = when (currentFilter) {
            Filter.ALL -> allScans
            Filter.HEALTHY -> allScans.filter { it.isHealthy }
            Filter.DISEASED -> allScans.filter { !it.isHealthy }
        }
        adapter.submitList(filtered)

        if (filtered.isEmpty() && allScans.isEmpty()) {
            // No scans at all — show main empty state
            updateEmptyState(isEmpty = true, isFilterEmpty = false)
        } else if (filtered.isEmpty()) {
            // Has scans but filter yields nothing
            updateEmptyState(isEmpty = false, isFilterEmpty = true)
        } else {
            updateEmptyState(isEmpty = false, isFilterEmpty = false)
        }

        // Re-trigger stagger animation
        binding.rvGallery.scheduleLayoutAnimation()
    }

    private fun loadScans() {
        val app = requireActivity().application as PlantDiseasesApp

        // Show shimmer on first load
        if (isFirstLoad) {
            binding.shimmerLayout.visibility = View.VISIBLE
            binding.shimmerLayout.startShimmer()
            binding.rvGallery.visibility = View.GONE
        }

        lifecycleScope.launch {
            binding.swipeRefresh.isRefreshing = !isFirstLoad
            allScans = app.scanRepository.getAllScans()

            // Hide shimmer
            if (isFirstLoad) {
                binding.shimmerLayout.stopShimmer()
                binding.shimmerLayout.visibility = View.GONE
                isFirstLoad = false
            }

            binding.swipeRefresh.isRefreshing = false
            applyFilter()
        }
    }

    private fun updateEmptyState(isEmpty: Boolean, isFilterEmpty: Boolean) {
        binding.emptyStateLayout.visibility = if (isEmpty) View.VISIBLE else View.GONE
        binding.filterEmptyLayout.visibility = if (isFilterEmpty) View.VISIBLE else View.GONE
        binding.rvGallery.visibility = if (isEmpty || isFilterEmpty) View.GONE else View.VISIBLE
    }

    private fun deleteItem(id: Long) {
        val app = requireActivity().application as PlantDiseasesApp
        lifecycleScope.launch {
            app.scanRepository.deleteScan(id)
            loadScans()
        }
    }

    override fun onResume() {
        super.onResume()
        loadScans()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
