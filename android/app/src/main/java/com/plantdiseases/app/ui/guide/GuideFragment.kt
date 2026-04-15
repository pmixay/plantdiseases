package com.plantdiseases.app.ui.guide

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.databinding.FragmentGuideBinding

class GuideFragment : Fragment() {

    private var _binding: FragmentGuideBinding? = null
    private val binding get() = _binding!!
    private lateinit var adapter: GuideAdapter

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentGuideBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        adapter = GuideAdapter { item ->
            // Open detail bottom sheet
            GuideDetailSheet.newInstance(item.id).show(childFragmentManager, "guide_detail")
        }

        binding.rvGuide.layoutManager = LinearLayoutManager(requireContext())
        binding.rvGuide.adapter = adapter

        setupTabs()
        loadCategory(GuideCategory.COMMON_DISEASES)
    }

    private fun setupTabs() {
        val tabs = listOf(
            GuideCategory.COMMON_DISEASES to R.string.cat_diseases,
            GuideCategory.PESTS to R.string.cat_pests,
            GuideCategory.WATERING to R.string.cat_watering,
            GuideCategory.LIGHTING to R.string.cat_lighting,
            GuideCategory.CARE_TIPS to R.string.cat_care
        )

        tabs.forEach { (category, titleRes) ->
            binding.tabLayout.addTab(
                binding.tabLayout.newTab().setText(titleRes).setTag(category)
            )
        }

        binding.tabLayout.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                val category = tab.tag as? GuideCategory ?: GuideCategory.COMMON_DISEASES
                loadCategory(category)
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
    }

    private fun loadCategory(category: GuideCategory) {
        val items = GuideDataProvider.getByCategory(category)
        adapter.submitList(items)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
