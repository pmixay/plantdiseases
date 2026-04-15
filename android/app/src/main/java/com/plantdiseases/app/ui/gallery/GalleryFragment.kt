package com.plantdiseases.app.ui.gallery

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentGalleryBinding
import com.plantdiseases.app.ui.result.ResultActivity
import kotlinx.coroutines.launch

class GalleryFragment : Fragment() {

    private var _binding: FragmentGalleryBinding? = null
    private val binding get() = _binding!!

    private lateinit var adapter: GalleryAdapter

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

        binding.swipeRefresh.setOnRefreshListener {
            loadScans()
        }

        loadScans()
    }

    private fun loadScans() {
        val app = requireActivity().application as PlantDiseasesApp
        lifecycleScope.launch {
            binding.swipeRefresh.isRefreshing = true
            val scans = app.scanRepository.getAllScans()
            adapter.submitList(scans)
            binding.swipeRefresh.isRefreshing = false

            binding.tvEmptyState.visibility = if (scans.isEmpty()) View.VISIBLE else View.GONE
            binding.rvGallery.visibility = if (scans.isEmpty()) View.GONE else View.VISIBLE
        }
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
