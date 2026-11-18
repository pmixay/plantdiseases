package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumPostCreateRequest
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityForumComposeBinding
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import javax.inject.Inject

/**
 * Ask the community: a question, a shared scan, a recovery update on one of
 * your posts, or a tip. Up to four photos. A scan attached from the result
 * screen uploads the exact analysed photo so the post can show the AI verdict.
 * While typing a title, solved questions that look similar are suggested.
 * Training consent is a separate switch and starts off.
 */
@AndroidEntryPoint
class ComposePostActivity : AppCompatActivity() {

    @Inject
    lateinit var forum: ForumRepository

    @Inject
    lateinit var scans: ScanRepository

    private lateinit var binding: ActivityForumComposeBinding
    private val photos = mutableListOf<Pair<Any, String>>()
    private var scanToken: String? = null
    private var scanMediaId: String? = null
    private var space: String = "general"
    private var spaces: List<ForumSpace> = emptyList()
    private var condition: String? = null
    private var similarJob: Job? = null
    private var publishing = false

    private val parentPostId: String? by lazy { intent.getStringExtra(EXTRA_PARENT_POST_ID) }
    private val scanRowId: Long by lazy { intent.getLongExtra(EXTRA_SCAN_ROW_ID, -1L) }

    private val pickPhotos = registerForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(MAX_PHOTOS),
    ) { uris -> uris.take(MAX_PHOTOS - photos.size).forEach(::uploadPhoto) }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityForumComposeBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        ForumInsets.apply(this, binding.appBar, binding.root)
        if (parentPostId != null) {
            binding.chipRecovery.visibility = View.VISIBLE
            binding.chipRecovery.isChecked = true
            binding.chipQuestion.visibility = View.GONE
            binding.chipTip.visibility = View.GONE
            binding.toolbar.setTitle(R.string.forum_compose_update_title)
        }
        binding.btnAddPhoto.setOnClickListener {
            if (photos.size >= MAX_PHOTOS) return@setOnClickListener
            pickPhotos.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
        }
        binding.tvSpace.setOnClickListener { pickSpace() }
        binding.tvCondition.setOnClickListener { pickCondition() }
        binding.etTitle.doAfterTextChanged { suggestSimilar(it?.toString().orEmpty()) }
        binding.btnPost.setOnClickListener { publish() }
        renderSpace()
        renderCondition()
        lifecycleScope.launch {
            forum.spaces().onSuccess {
                spaces = it
                renderSpace()
            }
        }
        if (scanRowId >= 0) attachScan(scanRowId)
    }

    // ── scan, photos ─────────────────────────────────────────────────────

    private fun attachScan(rowId: Long) {
        lifecycleScope.launch {
            val scan = scans.getScanById(rowId)
            val image = scan?.let { withContext(Dispatchers.IO) { scans.retainedAnalysisImage(it) } }
            val token = scan?.clientScanId?.takeIf { it.isNotBlank() }
            if (scan == null || image == null || token == null) {
                toast(getString(R.string.forum_scan_unavailable))
                return@launch
            }
            condition = scan.displayedClassKey?.takeIf { it.isNotBlank() }
            scan.speciesScientific?.let { binding.etSpecies.setText(it) }
            renderCondition()
            forum.uploadPhoto(image.bytes, scanToken = token)
                .onSuccess { upload ->
                    scanToken = token
                    scanMediaId = upload.mediaId
                    photos.add(0, File(scan.imagePath) to upload.mediaId)
                    binding.chipScan.visibility = View.VISIBLE
                    binding.tvScan.visibility = View.VISIBLE
                    binding.tvScan.text = getString(
                        R.string.forum_scan_attached,
                        ForumUi.conditionLabel(this@ComposePostActivity, condition),
                        (scan.confidence * PERCENT).toInt(),
                    )
                    renderPhotos()
                }
                .onFailure { toast(ForumUi.errorMessage(this@ComposePostActivity, it)) }
        }
    }

    private fun uploadPhoto(uri: Uri) {
        lifecycleScope.launch {
            val bytes = ForumPhotos.prepare(this@ComposePostActivity, uri)
            if (bytes == null) {
                toast(getString(R.string.forum_error_image))
                return@launch
            }
            forum.uploadPhoto(bytes)
                .onSuccess { upload ->
                    photos += uri to upload.mediaId
                    renderPhotos()
                }
                .onFailure { toast(ForumUi.errorMessage(this@ComposePostActivity, it)) }
        }
    }

    private fun renderPhotos() {
        val container = binding.llPhotos
        while (container.childCount > 1) container.removeViewAt(0)
        val size = resources.getDimensionPixelSize(R.dimen.forum_compose_thumb)
        val margin = resources.getDimensionPixelSize(R.dimen.space_sm)
        photos.forEachIndexed { index, (source, mediaId) ->
            val view = ImageView(this)
            view.layoutParams = LinearLayout.LayoutParams(size, size).apply { marginEnd = margin }
            view.scaleType = ImageView.ScaleType.CENTER_CROP
            view.contentDescription = getString(R.string.forum_remove_photo)
            Glide.with(view).load(source).centerCrop().into(view)
            view.setOnClickListener {
                if (mediaId == scanMediaId) return@setOnClickListener
                photos.removeAll { it.second == mediaId }
                renderPhotos()
            }
            container.addView(view, index)
        }
        binding.btnAddPhoto.visibility = if (photos.size >= MAX_PHOTOS) View.GONE else View.VISIBLE
    }

    // ── space, condition, similar ───────────────────────────────────────

    private fun pickSpace() {
        if (spaces.isEmpty()) return
        val labels = spaces.map { ForumUi.spaceName(this, it.slug, spaces) }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.forum_spaces)
            .setItems(labels) { _, which ->
                space = spaces[which].slug
                renderSpace()
            }
            .show()
    }

    private fun renderSpace() {
        binding.tvSpace.text = getString(R.string.forum_field_space, ForumUi.spaceName(this, space, spaces))
    }

    private fun pickCondition() {
        val keys = listOf<String?>(null) + ForumUi.DIAGNOSIS_KEYS
        val labels = keys.map { key ->
            if (key == null) getString(R.string.forum_condition_unknown) else ForumUi.conditionLabel(this, key)
        }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.forum_field_condition_title)
            .setItems(labels) { _, which ->
                condition = keys[which]
                renderCondition()
            }
            .show()
    }

    private fun renderCondition() {
        val label = condition?.let { ForumUi.conditionLabel(this, it) } ?: getString(R.string.forum_condition_unknown)
        binding.tvCondition.text = getString(R.string.forum_field_condition, label)
    }

    private fun suggestSimilar(title: String) {
        similarJob?.cancel()
        if (title.trim().length < MIN_SIMILAR_CHARS || parentPostId != null) {
            binding.cardSimilar.visibility = View.GONE
            return
        }
        similarJob = lifecycleScope.launch {
            delay(SIMILAR_DEBOUNCE_MS)
            forum.search(ForumRepository.SearchQuery(text = title, solvedOnly = true)).onSuccess { page ->
                val items = page.items.take(MAX_SIMILAR)
                binding.cardSimilar.visibility = if (items.isEmpty()) View.GONE else View.VISIBLE
                binding.llSimilar.removeAllViews()
                for (item in items) {
                    val row = TextView(this@ComposePostActivity)
                    row.text = ForumUi.postTitle(this@ComposePostActivity, item.title, item.kind, item.ai?.disease)
                    row.setTextColor(ContextCompat.getColor(this@ComposePostActivity, R.color.primary))
                    row.setPadding(0, ROW_PADDING, 0, ROW_PADDING)
                    row.setOnClickListener {
                        startActivity(PostDetailActivity.intent(this@ComposePostActivity, item.id))
                    }
                    binding.llSimilar.addView(row)
                }
            }
        }
    }

    // ── publish ──────────────────────────────────────────────────────────

    private fun selectedKind(): String = when (binding.chipKind.checkedChipId) {
        R.id.chip_scan -> "scan"
        R.id.chip_recovery -> "recovery"
        R.id.chip_tip -> "tip"
        else -> "question"
    }

    private fun publish() {
        if (publishing) return
        val kind = selectedKind()
        val title = binding.etTitle.text?.toString()?.trim().orEmpty()
        val body = binding.etBody.text?.toString()?.trim().orEmpty()
        if (title.isEmpty() && kind != "scan") {
            binding.tilTitle.error = getString(R.string.forum_error_title_required)
            return
        }
        binding.tilTitle.error = null
        if (kind == "scan" && scanToken == null) {
            toast(getString(R.string.forum_scan_unavailable))
            return
        }
        publishing = true
        binding.btnPost.isEnabled = false
        val request = ForumPostCreateRequest(
            kind = kind,
            title = title,
            body = body,
            space = space,
            species = binding.etSpecies.text?.toString()?.trim()?.takeIf { it.isNotEmpty() },
            condition = condition,
            urgent = binding.switchUrgent.isChecked,
            mediaIds = photos.map { it.second },
            scanToken = scanToken,
            parentPostId = parentPostId,
            trainingConsent = binding.switchConsent.isChecked,
            locale = if (LocaleHelper.isRussian(this)) "ru" else "en",
        )
        lifecycleScope.launch {
            forum.createPost(request)
                .onSuccess { post ->
                    startActivity(PostDetailActivity.intent(this@ComposePostActivity, post.id))
                    finish()
                }
                .onFailure { toast(ForumUi.errorMessage(this@ComposePostActivity, it)) }
            publishing = false
            binding.btnPost.isEnabled = true
        }
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    companion object {
        private const val EXTRA_PARENT_POST_ID = "extra_forum_parent_post_id"
        private const val EXTRA_SCAN_ROW_ID = "extra_forum_scan_row_id"
        private const val MAX_PHOTOS = 4
        private const val MAX_SIMILAR = 3
        private const val MIN_SIMILAR_CHARS = 6
        private const val SIMILAR_DEBOUNCE_MS = 600L
        private const val ROW_PADDING = 8
        private const val PERCENT = 100

        fun intent(context: Context): Intent = Intent(context, ComposePostActivity::class.java)

        /** Ask about a saved scan: the analysed photo and verdict come along. */
        fun intentForScan(context: Context, scanRowId: Long): Intent =
            intent(context).putExtra(EXTRA_SCAN_ROW_ID, scanRowId)

        /** Post a recovery update on one of your own posts. */
        fun intentForUpdate(context: Context, parentPostId: String): Intent =
            intent(context).putExtra(EXTRA_PARENT_POST_ID, parentPostId)
    }
}
