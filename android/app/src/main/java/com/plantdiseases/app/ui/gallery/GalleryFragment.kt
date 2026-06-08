package com.plantdiseases.app.ui.gallery

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuInflater
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.view.animation.AlphaAnimation
import android.view.animation.AnimationSet
import android.view.animation.LayoutAnimationController
import android.view.animation.ScaleAnimation
import androidx.core.content.ContextCompat
import androidx.core.view.MenuProvider
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.GridLayoutManager
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.databinding.FragmentGalleryBinding
import com.plantdiseases.app.ui.result.ResultActivity
import com.plantdiseases.app.util.Haptics
import android.widget.Toast
import kotlinx.coroutines.launch

class GalleryFragment : Fragment() {

    private var _binding: FragmentGalleryBinding? = null
    private val binding get() = _binding!!

    private lateinit var adapter: GalleryAdapter
    private var allScans: List<ScanHistoryItem> = emptyList()
    private var currentFilter = Filter.ALL
    private var isFirstLoad = true

    // Kept so the toolbar basket can hide itself when there's nothing to
    // clear (mirrors the old profile button, which was GONE on an empty
    // history). Re-resolved on every menu (re)creation.
    private var clearHistoryItem: MenuItem? = null

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

        setupStaggerAnimation()
        setupToolbarMenu()

        binding.swipeRefresh.setOnRefreshListener { loadScans() }

        binding.btnFirstScan.setOnClickListener {
            findNavController().navigate(R.id.cameraFragment)
        }

        setupFilterChips()
        // Data loads in onResume() — which always fires right after this on
        // first show, and again on every return. Loading here as well re-ran
        // the grid's entry animation, so the photos appeared to load twice.
    }

    /** Add the "clear all history" basket to the shared toolbar, scoped to
     *  this fragment's RESUMED state so it only shows in the green top bar
     *  while the Gallery tab is open. This replaces the footer button that
     *  used to sit at the bottom of the Profile screen. */
    private fun setupToolbarMenu() {
        requireActivity().addMenuProvider(
            object : MenuProvider {
                override fun onCreateMenu(menu: Menu, menuInflater: MenuInflater) {
                    menuInflater.inflate(R.menu.gallery_menu, menu)
                    clearHistoryItem = menu.findItem(R.id.action_clear_history)
                    // Tint to match the flat surface toolbar (onSurface);
                    // menu icons otherwise fall back to a default control colour.
                    clearHistoryItem?.icon
                        ?.setTint(ContextCompat.getColor(requireContext(), R.color.on_surface))
                    // Nothing to clear on an empty gallery.
                    clearHistoryItem?.isVisible = allScans.isNotEmpty()
                }

                override fun onMenuItemSelected(item: MenuItem): Boolean {
                    if (item.itemId != R.id.action_clear_history) return false
                    Haptics.tick(requireContext())
                    confirmClearHistory()
                    return true
                }
            },
            viewLifecycleOwner,
            Lifecycle.State.RESUMED,
        )
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
        val app = requireActivity().application as PlantDiseasesApp
        lifecycleScope.launch {
            app.scanRepository.clearAll()
            Toast.makeText(requireContext(), R.string.history_cleared, Toast.LENGTH_SHORT).show()
            loadScans()
        }
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

    private fun applyFilter() {
        val filtered = when (currentFilter) {
            Filter.ALL -> allScans
            Filter.HEALTHY -> allScans.filter { it.isHealthy && !it.isNotAPlant }
            // Diseased = real plant disease only. NAP rows are neither
            // healthy nor diseased; they belong to neither bucket.
            Filter.DISEASED -> allScans.filter { !it.isHealthy && !it.isNotAPlant }
        }
        adapter.submitList(filtered)

        // The basket is only useful when there's history to wipe.
        clearHistoryItem?.isVisible = allScans.isNotEmpty()

        when {
            filtered.isEmpty() && allScans.isEmpty() ->
                updateEmptyState(isEmpty = true, isFilterEmpty = false)
            filtered.isEmpty() ->
                updateEmptyState(isEmpty = false, isFilterEmpty = true)
            else ->
                updateEmptyState(isEmpty = false, isFilterEmpty = false)
        }

        binding.rvGallery.scheduleLayoutAnimation()
    }

    private fun loadScans() {
        val app = requireActivity().application as PlantDiseasesApp

        if (isFirstLoad) {
            binding.shimmerLayout.visibility = View.VISIBLE
            binding.shimmerLayout.startShimmer()
            binding.rvGallery.visibility = View.GONE
        }

        lifecycleScope.launch {
            binding.swipeRefresh.isRefreshing = !isFirstLoad
            allScans = app.scanRepository.getAllScans()

            if (isFirstLoad) {
                binding.shimmerLayout.stopShimmer()
                binding.shimmerLayout.visibility = View.GONE
                isFirstLoad = false
            }

            binding.swipeRefresh.isRefreshing = false
            applyFilter()
            loadCommunityBadges(app)
        }
    }

    /**
     * Lazily fetch community activity (opinions + per-viewer-deduped views) for
     * this install's shared scans and re-render with badges. Best-effort: when
     * community is off, nothing was shared, or the call fails, the list simply
     * stays unbadged.
     */
    private fun loadCommunityBadges(app: PlantDiseasesApp) {
        if (!com.plantdiseases.app.util.CommunityPrefs.uploadsEnabled(requireContext())) return
        val ids = allScans.map { it.clientScanId }.filter { it.isNotBlank() }
        if (ids.isEmpty()) return
        lifecycleScope.launch {
            val counts = app.scanRepository.getCommunityScanCounts(ids)
            if (counts.isEmpty() || _binding == null) return@launch
            val ctx = requireContext()
            allScans = allScans.map { item ->
                val c = counts[item.clientScanId]
                if (c != null) {
                    // "New" = opinions the user hasn't opened this scan to see.
                    val viewed = com.plantdiseases.app.util.OpinionPrefs
                        .viewedOpinions(ctx, item.clientScanId)
                    item.copy(
                        communityOpinions = c.opinions,
                        communityViews = c.views,
                        newOpinions = (c.opinions - viewed).coerceAtLeast(0),
                    )
                } else {
                    item
                }
            }
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
