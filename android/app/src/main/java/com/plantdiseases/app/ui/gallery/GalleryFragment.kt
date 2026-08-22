package com.plantdiseases.app.ui.gallery

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.AlphaAnimation
import android.view.animation.AnimationSet
import android.view.animation.LayoutAnimationController
import android.view.animation.ScaleAnimation
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.FragmentGalleryBinding
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.applyStatusBarTopInset
import android.widget.Toast
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class GalleryFragment : Fragment() {

    @Inject
    lateinit var scanRepository: ScanRepository

    private var _binding: FragmentGalleryBinding? = null
    private val binding get() = _binding!!

    private lateinit var adapter: GalleryAdapter
    private var allScans: List<ScanHistoryItem> = emptyList()
    private var currentFilter = Filter.ALL
    private var isFirstLoad = true

    enum class Filter { ALL, HEALTHY, DISEASED }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentGalleryBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.galleryRoot.applyStatusBarTopInset()

        adapter = GalleryAdapter(
            onClick = { item ->
                startActivity(
                    Intent(requireContext(), ResultActivity::class.java)
                        .putExtra(ResultActivity.EXTRA_SCAN_ID, item.id),
                )
            },
            onDelete = { item ->
                deleteItem(item.id)
            }
        )

        binding.rvGallery.layoutManager =
            GridLayoutManager(requireContext(), resources.getInteger(R.integer.gallery_span_count))
        binding.rvGallery.adapter = adapter

        setupStaggerAnimation()
        binding.btnClearHistory.setOnClickListener {
            Haptics.tick(requireContext())
            confirmClearHistory()
        }

        binding.btnFirstScan.setOnClickListener {
            // Select the Scan bottom-nav tab (routes through MainActivity) rather
            // than navigating the camera into Gallery's own back stack.
            requireActivity().findViewById<BottomNavigationView>(R.id.bottom_navigation)
                ?.selectedItemId = R.id.cameraFragment
        }

        setupFilterChips()
        // Preload the grid here, not just in onResume, so this tab is already
        // populated while it sits off-screen as the pager's neighbour — it
        // slides in showing photos (or the shimmer), not a blank panel. The
        // old "loads twice" hazard is gone: the entry stagger runs once here
        // (invisibly, off-screen) and onResume is now a guarded silent refresh.
        loadScans()
    }

    override fun onViewStateRestored(savedInstanceState: Bundle?) {
        super.onViewStateRestored(savedInstanceState)
        // The ChipGroup restores its checked chip across a config-change
        // recreation, but currentFilter is a plain field that resets to ALL.
        // Re-derive it from the restored chip (this runs after view-state
        // restoration and before onResume -> loadScans -> applyFilter) so the
        // highlighted chip and the rendered list stay in sync. On a fresh
        // launch chip_all is checked by default, so this evaluates to ALL.
        currentFilter = when {
            binding.chipHealthy.isChecked -> Filter.HEALTHY
            binding.chipDiseased.isChecked -> Filter.DISEASED
            else -> Filter.ALL
        }
    }

    private fun confirmClearHistory() {
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(R.string.clear_history)
            .setMessage(R.string.clear_history_confirm)
            .setPositiveButton(R.string.clear_all) { _, _ -> clearHistory() }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun clearHistory() {
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.clearAll()
            Toast.makeText(requireContext(), R.string.history_cleared, Toast.LENGTH_SHORT).show()
            loadScans()
        }
    }

    private fun setupStaggerAnimation() {
        val scaleAnim = ScaleAnimation(
            0.9f,
            1.0f,
            0.9f,
            1.0f,
            ScaleAnimation.RELATIVE_TO_SELF,
            0.5f,
            ScaleAnimation.RELATIVE_TO_SELF,
            0.5f
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
            Haptics.tick(requireContext())
            currentFilter = Filter.HEALTHY
            applyFilter()
        }
        binding.chipDiseased.setOnClickListener {
            Haptics.tick(requireContext())
            currentFilter = Filter.DISEASED
            applyFilter()
        }
    }

    private fun applyFilter(animate: Boolean = true) {
        val filtered = when (currentFilter) {
            Filter.ALL -> allScans
            Filter.HEALTHY -> allScans.filter { it.isHealthy && !it.isNotAPlant }
            // Diseased = real plant disease only. NAP rows are neither
            // healthy nor diseased; they belong to neither bucket.
            Filter.DISEASED -> allScans.filter { !it.isHealthy && !it.isNotAPlant }
        }
        adapter.submitList(filtered)

        // The basket is only useful when there's history to wipe.
        binding.btnClearHistory.visibility = if (allScans.isNotEmpty()) View.VISIBLE else View.GONE

        when {
            filtered.isEmpty() && allScans.isEmpty() ->
                updateEmptyState(isEmpty = true, isFilterEmpty = false)
            filtered.isEmpty() ->
                updateEmptyState(isEmpty = false, isFilterEmpty = true)
            else ->
                updateEmptyState(isEmpty = false, isFilterEmpty = false)
        }

        if (!isFirstLoad) registerOnboardingAnchors(filtered.isEmpty(), allScans.isEmpty())

        // Only run the staggered entry animation for user-driven changes and
        // the first load — a silent resume refresh passes animate=false so
        // the already-shown grid doesn't replay it.
        if (animate) binding.rvGallery.scheduleLayoutAnimation()
    }

    private fun loadScans(animate: Boolean = true) {
        if (isFirstLoad) {
            binding.shimmerLayout.visibility = View.VISIBLE
            binding.shimmerLayout.startShimmer()
            binding.rvGallery.visibility = View.GONE
        }

        viewLifecycleOwner.lifecycleScope.launch {
            // NAP / blur rejects never belong in the gallery. Their rows exist
            // briefly (they back the reject's result screen + "this is a
            // plant" feedback) and are swept after 48 h; this filter keeps
            // them out of the grid in the meantime.
            allScans = scanRepository.getAllScans().filter { !it.isNotAPlant }

            // The stagger belongs to the true first paint; a refresh (returning
            // to the tab) passes animate=false so the grid doesn't replay it.
            val runStagger = animate || isFirstLoad

            if (isFirstLoad) {
                binding.shimmerLayout.stopShimmer()
                binding.shimmerLayout.visibility = View.GONE
                isFirstLoad = false
            }

            applyFilter(animate = runStagger)
        }
    }

    private fun updateEmptyState(isEmpty: Boolean, isFilterEmpty: Boolean) {
        binding.emptyStateLayout.visibility = if (isEmpty) View.VISIBLE else View.GONE
        binding.filterEmptyLayout.visibility = if (isFilterEmpty) View.VISIBLE else View.GONE
        binding.rvGallery.visibility = if (isEmpty || isFilterEmpty) View.GONE else View.VISIBLE
    }

    private fun registerOnboardingAnchors(filteredEmpty: Boolean, allEmpty: Boolean) {
        val resultAnchor = when {
            allEmpty -> binding.emptyStateLayout
            filteredEmpty -> binding.filterEmptyLayout
            else -> binding.rvGallery
        }
        (activity as? MainActivity)?.apply {
            registerOnboardingAnchor(OnboardingAnchorKey.GALLERY_FILTERS, binding.chipGroupFilter)
            registerOnboardingAnchor(OnboardingAnchorKey.GALLERY_RESULTS, resultAnchor)
        }
    }

    private fun deleteItem(id: Long) {
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.deleteScan(id)
            loadScans()
        }
    }

    override fun onResume() {
        super.onResume()
        // onViewCreated already did the first load (this fires synchronously
        // right after it, while isFirstLoad is still true → skip, no double
        // load). Every genuine return refreshes automatically and silently:
        // no pull-to-refresh gesture, spinner, or replayed entry animation.
        if (!isFirstLoad) loadScans(animate = false)
    }

    override fun onDestroyView() {
        (activity as? MainActivity)?.apply {
            unregisterOnboardingAnchor(OnboardingAnchorKey.GALLERY_FILTERS)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GALLERY_RESULTS)
        }
        super.onDestroyView()
        _binding = null
    }
}
