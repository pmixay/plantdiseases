package com.plantdiseases.app.ui.guide

import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.data.model.GuideItem
import com.plantdiseases.app.databinding.FragmentGuideBinding
import com.plantdiseases.app.util.LocaleHelper

class GuideFragment : Fragment() {

    private var _binding: FragmentGuideBinding? = null
    private val binding get() = _binding!!
    private lateinit var adapter: GuideAdapter
    private var currentCategory: GuideCategory = GuideCategory.COMMON_DISEASES
    private var searchQuery: String = ""

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
            GuideDetailSheet.newInstance(item.id).show(childFragmentManager, "guide_detail")
        }

        binding.rvGuide.layoutManager = LinearLayoutManager(requireContext())
        binding.rvGuide.adapter = adapter

        setupTabs()
        setupSearch()
        loadItems()
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
                currentCategory = tab.tag as? GuideCategory ?: GuideCategory.COMMON_DISEASES
                loadItems()
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
    }

    private fun setupSearch() {
        binding.etSearch.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                searchQuery = s?.toString()?.trim() ?: ""
                loadItems()
            }
        })
    }

    private fun loadItems() {
        val items = if (searchQuery.isBlank()) {
            GuideDataProvider.getByCategory(currentCategory)
        } else {
            // Search across all categories
            val isRu = LocaleHelper.isRussian(requireContext())
            val query = searchQuery.lowercase()
            GuideDataProvider.getGuideItems().filter { item ->
                val title = if (isRu) item.titleRu else item.titleEn
                val desc = if (isRu) item.descriptionRu else item.descriptionEn
                val content = if (isRu) item.contentRu else item.contentEn
                title.lowercase().contains(query) ||
                desc.lowercase().contains(query) ||
                content.lowercase().contains(query)
            }
        }
        adapter.submitList(items)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
