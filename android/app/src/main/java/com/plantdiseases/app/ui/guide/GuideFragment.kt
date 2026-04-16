package com.plantdiseases.app.ui.guide

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.chip.Chip
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

    private val handler = Handler(Looper.getMainLooper())
    private var searchRunnable: Runnable? = null

    private val PREFS_NAME = "plantdiseases_prefs"
    private val KEY_RECENT_SEARCHES = "recent_searches"
    private val MAX_RECENT_SEARCHES = 5

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentGuideBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        adapter = GuideAdapter(
            onClick = { item ->
                // Save search query as recent if non-empty
                if (searchQuery.length >= 2) {
                    saveRecentSearch(searchQuery)
                }
                GuideDetailSheet.newInstance(item.id).show(childFragmentManager, "guide_detail")
            },
            searchQuery = ""
        )

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
        // Show recent searches on focus
        binding.etSearch.setOnFocusChangeListener { _, hasFocus ->
            if (hasFocus && binding.etSearch.text.isNullOrEmpty()) {
                showRecentSearches()
            } else {
                binding.recentSearchesGroup.visibility = View.GONE
            }
        }

        binding.etSearch.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                val query = s?.toString()?.trim() ?: ""

                // Hide recent searches when typing
                if (query.isNotEmpty()) {
                    binding.recentSearchesGroup.visibility = View.GONE
                }

                // Cancel previous debounce
                searchRunnable?.let { handler.removeCallbacks(it) }

                // Min 2 chars
                if (query.length < 2 && query.isNotEmpty()) {
                    return
                }

                // Debounce 300ms
                searchRunnable = Runnable {
                    searchQuery = query
                    adapter.searchQuery = query
                    loadItems()
                }.also {
                    handler.postDelayed(it, 300)
                }
            }
        })
    }

    private fun loadItems() {
        val isRu = LocaleHelper.isRussian(requireContext())
        val items = if (searchQuery.isBlank()) {
            GuideDataProvider.getByCategory(currentCategory)
        } else {
            // Search across all categories
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

        // Show search empty state
        if (items.isEmpty() && searchQuery.isNotBlank()) {
            binding.searchEmptyLayout.visibility = View.VISIBLE
            binding.tvSearchEmpty.text = getString(R.string.guide_search_empty, searchQuery)
            binding.rvGuide.visibility = View.GONE
        } else {
            binding.searchEmptyLayout.visibility = View.GONE
            binding.rvGuide.visibility = View.VISIBLE
        }
    }

    // Recent searches
    private fun showRecentSearches() {
        val recent = getRecentSearches()
        if (recent.isEmpty()) return

        binding.recentSearchesGroup.removeAllViews()
        recent.forEach { query ->
            val chip = Chip(requireContext()).apply {
                text = query
                isCloseIconVisible = false
                setOnClickListener {
                    binding.etSearch.setText(query)
                    binding.etSearch.setSelection(query.length)
                    binding.recentSearchesGroup.visibility = View.GONE
                }
            }
            binding.recentSearchesGroup.addView(chip)
        }
        binding.recentSearchesGroup.visibility = View.VISIBLE
    }

    private fun saveRecentSearch(query: String) {
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(KEY_RECENT_SEARCHES, "") ?: ""
        val list = existing.split("|").filter { it.isNotBlank() }.toMutableList()
        list.remove(query)
        list.add(0, query)
        val trimmed = list.take(MAX_RECENT_SEARCHES)
        prefs.edit().putString(KEY_RECENT_SEARCHES, trimmed.joinToString("|")).apply()
    }

    private fun getRecentSearches(): List<String> {
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(KEY_RECENT_SEARCHES, "") ?: ""
        return existing.split("|").filter { it.isNotBlank() }.take(MAX_RECENT_SEARCHES)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        searchRunnable?.let { handler.removeCallbacks(it) }
        _binding = null
    }
}
