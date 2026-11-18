package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityForumSearchBinding
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Search the community in Russian or English, filter by solved, photos and
 * condition, or "search with your last scan" to find posts about the same
 * plant and problem (solved ones first).
 */
@AndroidEntryPoint
class ForumSearchActivity : AppCompatActivity() {

    @Inject
    lateinit var forum: ForumRepository

    @Inject
    lateinit var scans: ScanRepository

    private lateinit var binding: ActivityForumSearchBinding
    private lateinit var adapter: ForumPostAdapter
    private var condition: String? = null
    private var species: String? = null
    private var lastScan: Pair<String, String?>? = null
    private var searchJob: Job? = null

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityForumSearchBinding.inflate(layoutInflater)
        setContentView(binding.root)
        ForumInsets.apply(this, binding.searchBar, binding.root)
        adapter = ForumPostAdapter(
            mediaUrl = forum::mediaUrl,
            onOpen = { post -> startActivity(PostDetailActivity.intent(this, post.id)) },
            onLike = ::toggleLike,
        )
        binding.rvResults.layoutManager = LinearLayoutManager(this)
        binding.rvResults.adapter = adapter
        binding.btnBack.setOnClickListener { finish() }
        binding.btnClear.setOnClickListener {
            binding.etQuery.setText("")
            condition = null
            species = null
            renderCondition()
            showResults(emptyList(), prompt = true)
        }
        binding.etQuery.doAfterTextChanged { scheduleSearch() }
        binding.etQuery.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEARCH) {
                search()
                true
            } else {
                false
            }
        }
        binding.chipSolved.setOnCheckedChangeListener { _, _ -> search() }
        binding.chipPhotos.setOnCheckedChangeListener { _, _ -> search() }
        binding.chipCondition.setOnClickListener { pickCondition() }
        binding.cardScanSearch.setOnClickListener { searchWithLastScan() }
        lifecycleScope.launch { forum.spaces().onSuccess { adapter.spaces = it } }
        loadLastScan()
        binding.etQuery.requestFocus()
    }

    private fun loadLastScan() {
        lifecycleScope.launch {
            val latest = scans.getAllScans().maxByOrNull { it.timestamp } ?: return@launch
            val entity = scans.getScanById(latest.id) ?: return@launch
            val key = entity.displayedClassKey?.takeIf { it.isNotBlank() } ?: return@launch
            if (key == "not_a_plant") return@launch
            lastScan = key to entity.speciesScientific
            binding.cardScanSearch.visibility = View.VISIBLE
            val label = ForumUi.conditionLabel(this@ForumSearchActivity, key)
            binding.tvScanSearch.text = entity.speciesScientific
                ?.let { getString(R.string.forum_scan_search_detail, it, label) }
                ?: label
        }
    }

    private fun searchWithLastScan() {
        val (key, scanSpecies) = lastScan ?: return
        condition = key
        species = scanSpecies
        renderCondition()
        search()
    }

    private fun pickCondition() {
        val keys = listOf<String?>(null) + ForumUi.DIAGNOSIS_KEYS
        val labels = keys.map { key ->
            if (key == null) getString(R.string.forum_filter_any_condition) else ForumUi.conditionLabel(this, key)
        }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.forum_filter_condition)
            .setItems(labels) { _, which ->
                condition = keys[which]
                if (condition == null) species = null
                renderCondition()
                search()
            }
            .show()
    }

    private fun renderCondition() {
        binding.chipCondition.text = condition?.let { ForumUi.conditionLabel(this, it) }
            ?: getString(R.string.forum_filter_condition)
    }

    private fun scheduleSearch() {
        searchJob?.cancel()
        searchJob = lifecycleScope.launch {
            delay(DEBOUNCE_MS)
            search()
        }
    }

    private fun search() {
        val text = binding.etQuery.text.toString().trim()
        if (text.length < MIN_QUERY && condition == null && species == null) {
            showResults(emptyList(), prompt = true)
            return
        }
        searchJob?.cancel()
        binding.progress.visibility = View.VISIBLE
        val query = ForumRepository.SearchQuery(
            text = text.takeIf { it.length >= MIN_QUERY },
            solvedOnly = binding.chipSolved.isChecked,
            withPhotos = binding.chipPhotos.isChecked,
            condition = condition,
            species = species,
        )
        searchJob = lifecycleScope.launch {
            forum.search(query)
                .onSuccess { page ->
                    adapter.terms = page.terms
                    showResults(page.items, prompt = false)
                }
                .onFailure { error ->
                    showResults(emptyList(), prompt = false)
                    binding.tvEmpty.text = ForumUi.errorMessage(this@ForumSearchActivity, error)
                }
            binding.progress.visibility = View.GONE
        }
    }

    private fun showResults(items: List<ForumPost>, prompt: Boolean) {
        binding.progress.visibility = View.GONE
        adapter.submitList(items)
        binding.tvEmpty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
        binding.tvEmpty.setText(if (prompt) R.string.forum_search_prompt else R.string.forum_search_empty)
    }

    private fun toggleLike(post: ForumPost) {
        if (!forum.isSignedIn) {
            Toast.makeText(this, R.string.community_sign_in_required, Toast.LENGTH_SHORT).show()
            startActivity(AccountActivity.intent(this))
            return
        }
        if (post.author.isMe) return
        val next = !post.likedByMe
        adapter.updateLike(post.id, next, (post.likeCount + if (next) 1 else -1).coerceAtLeast(0))
        lifecycleScope.launch {
            forum.likePost(post.id, next)
                .onSuccess { adapter.updateLike(post.id, it.liked, it.likeCount) }
                .onFailure { adapter.updateLike(post.id, post.likedByMe, post.likeCount) }
        }
    }

    companion object {
        private const val DEBOUNCE_MS = 450L
        private const val MIN_QUERY = 2

        fun intent(context: Context): Intent = Intent(context, ForumSearchActivity::class.java)
    }
}
