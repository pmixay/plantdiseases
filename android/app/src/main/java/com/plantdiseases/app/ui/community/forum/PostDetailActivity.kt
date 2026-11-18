package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Toast
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.PopupMenu
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.bumptech.glide.Glide
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumAnswer
import com.plantdiseases.app.data.model.ForumAnswerCreateRequest
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.model.StaffMe
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.ui.garden.PhotoViewerActivity
import com.plantdiseases.app.databinding.ActivityForumPostBinding
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * One post and its answers. Answers explain what to do: text, optional steps,
 * an optional diagnosis tag and up to three photos. Answers can be liked or
 * marked not helpful; the asker accepts the one that solved it.
 */
@AndroidEntryPoint
class PostDetailActivity :
    AppCompatActivity(),
    ThreadActions {

    @Inject
    lateinit var forum: ForumRepository

    private lateinit var binding: ActivityForumPostBinding
    private lateinit var adapter: PostThreadAdapter
    private lateinit var moderation: ModerationController

    private val postId: String by lazy { intent.getStringExtra(EXTRA_POST_ID).orEmpty() }
    private var post: ForumPost? = null
    private var spaces: List<ForumSpace> = emptyList()
    private var staff: StaffMe? = null
    private var replyTo: ForumAnswer? = null
    private var diagnosis: String? = null
    private val answerPhotos = mutableListOf<Pair<Uri, String>>()
    private var sending = false

    private val pickPhotos = registerForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(MAX_ANSWER_PHOTOS),
    ) { uris -> uris.take(MAX_ANSWER_PHOTOS - answerPhotos.size).forEach(::uploadPhoto) }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (postId.isBlank()) {
            finish()
            return
        }
        binding = ActivityForumPostBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        ForumInsets.apply(this, binding.appBar, binding.root)
        binding.toolbar.setOnMenuItemClickListener { item ->
            onPostMenu(item.itemId)
            true
        }
        adapter = PostThreadAdapter(forum::mediaUrl, this)
        moderation = ModerationController(this, forum) { load() }
        binding.rvThread.layoutManager = LinearLayoutManager(this)
        binding.rvThread.adapter = adapter
        bindComposer()
        lifecycleScope.launch { forum.spaces().onSuccess { spaces = it } }
        if (forum.isSignedIn) {
            lifecycleScope.launch {
                forum.staffMe().onSuccess {
                    staff = it
                    post?.let(::buildPostMenu)
                }
            }
        }
        load()
    }

    private var reloadOnReturn = false

    override fun onRestart() {
        super.onRestart()
        // Back from editing the post or posting an update: show the change.
        if (reloadOnReturn) {
            reloadOnReturn = false
            load()
        }
    }

    private fun load() {
        lifecycleScope.launch {
            forum.post(postId)
                .onSuccess(::render)
                .onFailure { error ->
                    binding.progress.visibility = View.GONE
                    binding.tvError.visibility = View.VISIBLE
                    binding.tvError.text = ForumUi.errorMessage(this@PostDetailActivity, error)
                    binding.composer.visibility = View.GONE
                }
        }
    }

    private fun render(loaded: ForumPost) {
        post = loaded
        binding.progress.visibility = View.GONE
        binding.tvError.visibility = View.GONE
        binding.toolbar.title = ForumUi.spaceName(this, loaded.space, spaces)
        adapter.submit(loaded, spaces)
        binding.tvLocked.visibility = if (loaded.locked) View.VISIBLE else View.GONE
        binding.composer.visibility = if (loaded.locked) View.GONE else View.VISIBLE
        buildPostMenu(loaded)
    }

    // ── post menu ────────────────────────────────────────────────────────

    private fun buildPostMenu(loaded: ForumPost) {
        val menu = binding.toolbar.menu
        menu.clear()
        if (loaded.author.isMe) {
            if (loaded.kind != "tip") menu.add(0, MENU_UPDATE, 0, R.string.forum_post_update)
            menu.add(0, MENU_DELETE, 1, R.string.forum_delete_post)
        } else {
            menu.add(0, MENU_REPORT, 0, R.string.forum_report)
            menu.add(0, MENU_BLOCK, 1, R.string.forum_block_author)
        }
        if (staff?.isStaff == true) menu.add(0, MENU_MODERATE, 2, R.string.forum_moderation_tools)
    }

    private fun onPostMenu(itemId: Int) {
        val current = post ?: return
        when (itemId) {
            MENU_DELETE -> confirm(R.string.forum_delete_post_confirm) {
                lifecycleScope.launch {
                    forum.deletePost(current.id).onSuccess { finish() }.onFailure(::showError)
                }
            }
            MENU_UPDATE -> {
                reloadOnReturn = true
                startActivity(ComposePostActivity.intentForUpdate(this, current.id))
            }
            MENU_REPORT -> report("post", current.id)
            MENU_BLOCK -> block("post", current.id)
            MENU_MODERATE -> staff?.let { moderation.postMenu(current, it, spaces) }
        }
    }

    // ── thread actions ───────────────────────────────────────────────────

    override fun onLikePost() {
        val current = post ?: return
        if (!requireSignIn() || current.author.isMe) return
        lifecycleScope.launch {
            forum.likePost(current.id, !current.likedByMe)
                .onSuccess { result ->
                    render(current.copy(likedByMe = result.liked, likeCount = result.likeCount))
                }
                .onFailure(::showError)
        }
    }

    override fun onOpenAuthor(authorToken: String) {
        startActivity(HelperProfileActivity.intent(this, authorToken))
    }

    override fun onEditPost() {
        val current = post ?: return
        reloadOnReturn = true
        startActivity(ComposePostActivity.intentForEdit(this, current.id))
    }

    override fun hasAnswerMenu(answer: ForumAnswer): Boolean =
        answer.author?.isMe == true || !answer.isAi || staff?.isStaff == true

    override fun onOpenPost(postId: String) {
        startActivity(intent(this, postId))
    }

    override fun onOpenPhoto(mediaIds: List<String>, index: Int, from: View) {
        PhotoViewerActivity.open(this, mediaIds.map(forum::mediaUrl), index, from)
    }

    override fun onReact(answer: ForumAnswer, reaction: String?) {
        if (!requireSignIn()) return
        lifecycleScope.launch {
            forum.react(answer.id, reaction).onSuccess { load() }.onFailure(::showError)
        }
    }

    override fun onReply(answer: ForumAnswer) {
        if (!requireSignIn()) return
        replyTo = answer
        val name = answer.author?.nickname?.takeIf { it.isNotBlank() }
            ?: getString(R.string.forum_ai_answer_title)
        binding.replyBanner.visibility = View.VISIBLE
        binding.tvReplyTo.text = getString(R.string.forum_replying_to, name)
        binding.etAnswer.requestFocus()
    }

    override fun onAccept(answer: ForumAnswer) {
        val current = post ?: return
        lifecycleScope.launch {
            forum.accept(current.id, if (answer.accepted) null else answer.id)
                .onSuccess { load() }
                .onFailure(::showError)
        }
    }

    override fun onAnswerMenu(answer: ForumAnswer, anchor: View) {
        val popup = PopupMenu(this, anchor)
        val mine = answer.author?.isMe == true
        if (mine) {
            popup.menu.add(0, MENU_DELETE, 0, R.string.forum_delete_answer)
        } else if (!answer.isAi) {
            popup.menu.add(0, MENU_REPORT, 0, R.string.forum_report)
            popup.menu.add(0, MENU_BLOCK, 1, R.string.forum_block_author)
        }
        if (staff?.isStaff == true) popup.menu.add(0, MENU_MODERATE, 2, R.string.forum_moderation_tools)
        if (popup.menu.size() == 0) return
        popup.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                MENU_DELETE -> confirm(R.string.forum_delete_answer_confirm) {
                    lifecycleScope.launch {
                        forum.deleteAnswer(answer.id).onSuccess { load() }.onFailure(::showError)
                    }
                }
                MENU_REPORT -> report("answer", answer.id)
                MENU_BLOCK -> block("answer", answer.id)
                MENU_MODERATE -> staff?.let { moderation.answerMenu(answer, it) }
            }
            true
        }
        popup.show()
    }

    // ── composer ─────────────────────────────────────────────────────────

    private fun bindComposer() {
        binding.btnCancelReply.setOnClickListener { clearReply() }
        binding.btnSteps.setOnClickListener {
            binding.etSteps.visibility = if (binding.etSteps.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        }
        binding.btnDiagnosis.setOnClickListener { pickDiagnosis() }
        binding.tvDiagnosisSelected.setOnClickListener {
            diagnosis = null
            renderDiagnosis()
        }
        binding.btnPhoto.setOnClickListener {
            if (!requireSignIn()) return@setOnClickListener
            if (answerPhotos.size >= MAX_ANSWER_PHOTOS) return@setOnClickListener
            pickPhotos.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
        }
        binding.btnSend.setOnClickListener { send() }
    }

    private fun pickDiagnosis() {
        val labels = ForumUi.DIAGNOSIS_KEYS.map { ForumUi.conditionLabel(this, it) }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.forum_add_diagnosis)
            .setItems(labels) { _, which ->
                diagnosis = ForumUi.DIAGNOSIS_KEYS[which]
                renderDiagnosis()
            }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun renderDiagnosis() {
        val value = diagnosis
        binding.tvDiagnosisSelected.visibility = if (value == null) View.GONE else View.VISIBLE
        binding.tvDiagnosisSelected.text = getString(
            R.string.forum_diagnosis_selected,
            ForumUi.conditionLabel(this, value),
        )
    }

    private fun uploadPhoto(uri: Uri) {
        lifecycleScope.launch {
            val bytes = ForumPhotos.prepare(this@PostDetailActivity, uri)
            if (bytes == null) {
                Toast.makeText(this@PostDetailActivity, R.string.forum_error_image, Toast.LENGTH_SHORT).show()
                return@launch
            }
            forum.uploadPhoto(bytes)
                .onSuccess { upload ->
                    answerPhotos += uri to upload.mediaId
                    renderPhotos()
                }
                .onFailure(::showError)
        }
    }

    private fun renderPhotos() {
        binding.llAnswerPhotos.removeAllViews()
        binding.llAnswerPhotos.visibility = if (answerPhotos.isEmpty()) View.GONE else View.VISIBLE
        binding.cbAnswerConsent.visibility = binding.llAnswerPhotos.visibility
        val size = resources.getDimensionPixelSize(R.dimen.forum_compose_thumb)
        val margin = resources.getDimensionPixelSize(R.dimen.space_sm)
        for ((uri, mediaId) in answerPhotos.toList()) {
            val view = ImageView(this)
            view.layoutParams = LinearLayout.LayoutParams(size, size).apply { marginEnd = margin }
            view.scaleType = ImageView.ScaleType.CENTER_CROP
            view.contentDescription = getString(R.string.forum_remove_photo)
            Glide.with(view).load(uri).centerCrop().into(view)
            view.setOnClickListener {
                answerPhotos.removeAll { it.second == mediaId }
                renderPhotos()
            }
            binding.llAnswerPhotos.addView(view)
        }
    }

    private fun send() {
        val current = post ?: return
        if (!requireSignIn() || sending) return
        val body = binding.etAnswer.text.toString().trim()
        val steps = binding.etSteps.text.toString().lines().map { it.trim() }.filter { it.isNotEmpty() }
        if (body.isEmpty() && steps.isEmpty() && answerPhotos.isEmpty() && diagnosis == null) {
            Toast.makeText(this, R.string.forum_error_body_required, Toast.LENGTH_SHORT).show()
            return
        }
        sending = true
        binding.btnSend.isEnabled = false
        val request = ForumAnswerCreateRequest(
            body = body,
            parentId = replyTo?.id,
            steps = steps.take(MAX_STEPS),
            diagnosis = diagnosis,
            mediaIds = answerPhotos.map { it.second },
            trainingConsent = answerPhotos.isNotEmpty() && binding.cbAnswerConsent.isChecked,
        )
        lifecycleScope.launch {
            forum.answer(current.id, request)
                .onSuccess {
                    binding.etAnswer.setText("")
                    binding.etSteps.setText("")
                    binding.etSteps.visibility = View.GONE
                    binding.cbAnswerConsent.isChecked = false
                    answerPhotos.clear()
                    renderPhotos()
                    diagnosis = null
                    renderDiagnosis()
                    clearReply()
                    load()
                }
                .onFailure(::showError)
            sending = false
            binding.btnSend.isEnabled = true
        }
    }

    private fun clearReply() {
        replyTo = null
        binding.replyBanner.visibility = View.GONE
    }

    // ── safety ───────────────────────────────────────────────────────────

    private fun report(targetType: String, targetId: String) {
        if (!requireSignIn()) return
        val labels = ForumUi.REPORT_REASONS.map { getString(it.second) }.toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.forum_report)
            .setItems(labels) { _, which ->
                lifecycleScope.launch {
                    forum.report(targetType, targetId, ForumUi.REPORT_REASONS[which].first)
                        .onSuccess {
                            Toast.makeText(this@PostDetailActivity, R.string.forum_reported, Toast.LENGTH_SHORT).show()
                        }
                        .onFailure(::showError)
                }
            }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun block(targetType: String, targetId: String) {
        if (!requireSignIn()) return
        confirm(R.string.forum_block_confirm) {
            lifecycleScope.launch {
                forum.block(targetType, targetId)
                    .onSuccess { if (targetType == "post") finish() else load() }
                    .onFailure(::showError)
            }
        }
    }

    private fun confirm(message: Int, onConfirm: () -> Unit) {
        MaterialAlertDialogBuilder(this)
            .setMessage(message)
            .setPositiveButton(R.string.forum_confirm) { _, _ -> onConfirm() }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private fun requireSignIn(): Boolean {
        if (forum.isSignedIn) return true
        Toast.makeText(this, R.string.community_sign_in_required, Toast.LENGTH_SHORT).show()
        startActivity(AccountActivity.intent(this))
        return false
    }

    private fun showError(error: Throwable) {
        Toast.makeText(this, ForumUi.errorMessage(this, error), Toast.LENGTH_LONG).show()
    }

    companion object {
        private const val EXTRA_POST_ID = "extra_forum_post_id"
        private const val MAX_ANSWER_PHOTOS = 3
        private const val MAX_STEPS = 8
        private const val MENU_DELETE = 1
        private const val MENU_REPORT = 2
        private const val MENU_BLOCK = 3
        private const val MENU_MODERATE = 4
        private const val MENU_UPDATE = 5

        fun intent(context: Context, postId: String): Intent =
            Intent(context, PostDetailActivity::class.java).putExtra(EXTRA_POST_ID, postId)
    }
}
