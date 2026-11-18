package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.google.android.material.imageview.ShapeableImageView
import com.google.android.material.shape.ShapeAppearanceModel
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumAnswer
import com.plantdiseases.app.data.model.ForumMedia
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.databinding.ItemForumAnswerBinding
import com.plantdiseases.app.databinding.ItemForumPostHeaderBinding
import com.plantdiseases.app.util.Avatars

/** What the thread screen does when someone taps something in the thread. */
interface ThreadActions {
    fun onLikePost()
    fun onOpenAuthor(authorToken: String)
    fun onPostUpdate()
    fun onOpenPost(postId: String)
    fun onOpenPhoto(mediaId: String)
    fun onReact(answer: ForumAnswer, reaction: String?)
    fun onReply(answer: ForumAnswer)
    fun onAccept(answer: ForumAnswer)
    fun onAnswerMenu(answer: ForumAnswer, anchor: View)
}

/**
 * A post followed by its answers, already ordered and indented by the server
 * (accepted first, then the AI verdict, then best-liked). The AI verdict is an
 * answer like any other: people like it or mark it not helpful.
 */
class PostThreadAdapter(
    private val mediaUrl: (String) -> String,
    private val actions: ThreadActions,
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private var post: ForumPost? = null
    private var spaces: List<ForumSpace> = emptyList()

    fun submit(post: ForumPost, spaces: List<ForumSpace>) {
        this.post = post
        this.spaces = spaces
        notifyDataSetChanged()
    }

    override fun getItemCount(): Int = post?.let { 1 + it.answers.size } ?: 0

    override fun getItemViewType(position: Int): Int = if (position == 0) TYPE_HEADER else TYPE_ANSWER

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == TYPE_HEADER) {
            HeaderHolder(ItemForumPostHeaderBinding.inflate(inflater, parent, false))
        } else {
            AnswerHolder(ItemForumAnswerBinding.inflate(inflater, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val current = post ?: return
        when (holder) {
            is HeaderHolder -> holder.bind(current)
            is AnswerHolder -> holder.bind(current, current.answers[position - 1])
        }
    }

    inner class HeaderHolder(private val binding: ItemForumPostHeaderBinding) : RecyclerView.ViewHolder(binding.root) {

        fun bind(post: ForumPost) {
            val context = binding.root.context
            val nickname = post.author.nickname.ifBlank { context.getString(R.string.community_anonymous_author) }
            binding.tvAvatar.text = Avatars.monogramForNick(nickname)
            binding.tvAvatar.backgroundTintList = ColorStateList.valueOf(Avatars.colorFor(nickname))
            binding.tvAuthor.text = nickname
            binding.tvAuthor.setOnClickListener { post.author.authorToken?.let(actions::onOpenAuthor) }
            binding.tvMeta.text = context.getString(
                R.string.forum_meta_kind_space_time,
                context.getString(ForumUi.kindLabel(post.kind)),
                ForumUi.spaceName(context, post.space, spaces),
                ForumUi.relativeTime(post.createdAt),
            )
            bindBadge(context, post)
            binding.tvTitle.text = ForumUi.postTitle(context, post.title, post.kind, post.ai?.disease)
            binding.tvBody.visibility = if (post.body.isBlank()) View.GONE else View.VISIBLE
            binding.tvBody.text = post.body
            val tags = listOfNotNull(
                post.species?.takeIf { it.isNotBlank() },
                post.condition?.let { ForumUi.conditionLabel(context, it) },
            )
            binding.tvTags.visibility = if (tags.isEmpty()) View.GONE else View.VISIBLE
            binding.tvTags.text = tags.joinToString(" · ")
            bindPhotos(context, post.media)
            bindUpdates(context, post)
            bindLike(context, post)
            binding.tvAnswerCount.text = post.answerCount.toString()
            binding.btnUpdate.visibility = if (post.author.isMe && post.kind != "tip") View.VISIBLE else View.GONE
            binding.btnUpdate.setOnClickListener { actions.onPostUpdate() }
            binding.tvAnswersTitle.text = context.resources.getQuantityString(
                R.plurals.forum_answers_title,
                post.answerCount,
                post.answerCount,
            )
        }

        private fun bindBadge(context: Context, post: ForumPost) {
            val (text, color) = when {
                post.solved -> R.string.forum_badge_solved to R.color.healthy_green
                post.locked -> R.string.forum_badge_locked to R.color.on_surface_secondary
                post.state == "hidden" -> R.string.forum_badge_hidden to R.color.warning_amber
                post.urgent -> R.string.forum_badge_urgent to R.color.disease_red
                else -> null to null
            }
            binding.tvBadge.visibility = if (text == null) View.GONE else View.VISIBLE
            if (text != null && color != null) {
                binding.tvBadge.setText(text)
                binding.tvBadge.setTextColor(ContextCompat.getColor(context, color))
            }
        }

        private fun bindPhotos(context: Context, media: List<ForumMedia>) {
            binding.llPhotos.removeAllViews()
            binding.photosScroll.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
            val height = context.resources.getDimensionPixelSize(R.dimen.forum_photo_height)
            for (item in media) {
                val ratio = if (item.width > 0 && item.height > 0) item.width.toFloat() / item.height else RATIO
                val view = photoView(context, (height * ratio).toInt().coerceIn(height / 2, height * 2), height)
                Glide.with(view).load(mediaUrl(item.mediaId)).centerCrop().into(view)
                view.setOnClickListener { actions.onOpenPhoto(item.mediaId) }
                binding.llPhotos.addView(view)
            }
        }

        private fun bindUpdates(context: Context, post: ForumPost) {
            binding.llUpdates.removeAllViews()
            val links = buildList {
                post.parentPostId?.let { add(context.getString(R.string.forum_original_post) to it) }
                post.recoveryUpdates.forEach { update ->
                    add(context.getString(R.string.forum_recovery_link, update.title, ForumUi.relativeTime(update.createdAt)) to update.id)
                }
            }
            for ((label, id) in links) {
                val view = TextView(context)
                view.text = label
                view.setTextColor(ContextCompat.getColor(context, R.color.primary))
                view.setPadding(0, PADDING_SMALL, 0, PADDING_SMALL)
                view.setOnClickListener { actions.onOpenPost(id) }
                binding.llUpdates.addView(view)
            }
        }

        private fun bindLike(context: Context, post: ForumPost) {
            binding.tvLike.text = post.likeCount.toString()
            binding.tvLike.setCompoundDrawablesRelativeWithIntrinsicBounds(
                if (post.likedByMe) R.drawable.ic_heart_filled else R.drawable.ic_heart,
                0,
                0,
                0,
            )
            val color = ContextCompat.getColor(
                context,
                if (post.likedByMe) R.color.disease_red else R.color.on_surface_secondary,
            )
            binding.tvLike.compoundDrawableTintList = ColorStateList.valueOf(color)
            binding.tvLike.setTextColor(color)
            binding.tvLike.isEnabled = !post.author.isMe
            binding.tvLike.setOnClickListener { actions.onLikePost() }
        }
    }

    inner class AnswerHolder(private val binding: ItemForumAnswerBinding) : RecyclerView.ViewHolder(binding.root) {

        fun bind(post: ForumPost, answer: ForumAnswer) {
            val context = binding.root.context
            val indent = context.resources.getDimensionPixelSize(R.dimen.forum_thread_indent) * answer.depth
            binding.answerRoot.setPaddingRelative(
                context.resources.getDimensionPixelSize(R.dimen.space_lg) + indent,
                binding.answerRoot.paddingTop,
                binding.answerRoot.paddingEnd,
                binding.answerRoot.paddingBottom,
            )
            val lineColor = when {
                answer.accepted -> R.color.healthy_green
                answer.isAi -> R.color.neutral_blue
                else -> R.color.divider
            }
            binding.threadLine.setBackgroundColor(ContextCompat.getColor(context, lineColor))
            bindAuthor(context, post, answer)
            if (answer.isRemoved) {
                bindRemoved(context)
                return
            }
            binding.tvBody.setTypeface(null, Typeface.NORMAL)
            binding.tvBody.setTextColor(ContextCompat.getColor(context, R.color.on_surface))
            bindAi(context, post, answer)
            binding.tvBody.visibility = if (answer.body.isBlank()) View.GONE else View.VISIBLE
            binding.tvBody.text = answer.body
            binding.tvSteps.visibility = if (answer.steps.isEmpty()) View.GONE else View.VISIBLE
            binding.tvSteps.text = answer.steps.mapIndexed { index, step -> "${index + 1}. $step" }.joinToString("\n")
            val diagnosis = answer.diagnosis?.takeIf { !answer.isAi }
            binding.tvDiagnosis.visibility = if (diagnosis == null) View.GONE else View.VISIBLE
            binding.tvDiagnosis.text = context.getString(
                R.string.forum_diagnosis_tag,
                ForumUi.conditionLabel(context, diagnosis),
            )
            bindMedia(context, answer.media)
            bindMarks(context, answer)
            bindActions(context, post, answer)
        }

        private fun bindAuthor(context: Context, post: ForumPost, answer: ForumAnswer) {
            binding.ivAi.visibility = if (answer.isAi) View.VISIBLE else View.GONE
            val author = answer.author
            binding.tvAuthor.text = when {
                answer.isAi -> context.getString(R.string.forum_ai_answer_title)
                author == null -> context.getString(R.string.forum_removed_author)
                else -> author.nickname.ifBlank { context.getString(R.string.community_anonymous_author) }
            }
            binding.tvAuthor.setOnClickListener { author?.authorToken?.let(actions::onOpenAuthor) }
            val badge = when {
                author?.isPostAuthor == true -> R.string.forum_badge_asker
                answer.verified -> R.string.forum_badge_expert_verified
                else -> null
            }
            binding.tvAuthorBadge.visibility = if (badge == null) View.GONE else View.VISIBLE
            badge?.let { binding.tvAuthorBadge.setText(it) }
            binding.tvTime.text = ForumUi.relativeTime(answer.createdAt)
            binding.btnMore.visibility = if (answer.isRemoved) View.GONE else View.VISIBLE
            binding.btnMore.setOnClickListener { actions.onAnswerMenu(answer, it) }
            binding.btnAccept.visibility = View.GONE
            if (post.locked) binding.tvReply.visibility = View.GONE
        }

        private fun bindRemoved(context: Context) {
            binding.aiBlock.visibility = View.GONE
            binding.tvBody.visibility = View.VISIBLE
            binding.tvBody.setText(R.string.forum_answer_removed)
            binding.tvBody.setTypeface(null, Typeface.ITALIC)
            binding.tvBody.setTextColor(ContextCompat.getColor(context, R.color.on_surface_secondary))
            binding.tvSteps.visibility = View.GONE
            binding.tvDiagnosis.visibility = View.GONE
            binding.llMedia.visibility = View.GONE
            binding.tvMarks.visibility = View.GONE
            binding.actions.visibility = View.GONE
        }

        private fun bindAi(context: Context, post: ForumPost, answer: ForumAnswer) {
            binding.aiBlock.visibility = if (answer.isAi) View.VISIBLE else View.GONE
            if (!answer.isAi) return
            val confidence = ((post.ai?.confidence ?: 0.0) * 100).toInt()
            binding.tvAiVerdict.text = context.getString(
                R.string.forum_ai_verdict,
                ForumUi.conditionLabel(context, answer.diagnosis ?: post.ai?.disease),
                confidence,
            )
            binding.pbAi.progress = confidence
        }

        private fun bindMedia(context: Context, media: List<ForumMedia>) {
            binding.llMedia.removeAllViews()
            binding.llMedia.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
            val size = context.resources.getDimensionPixelSize(R.dimen.forum_answer_photo_size)
            for (item in media) {
                val view = photoView(context, size, size)
                Glide.with(view).load(mediaUrl(item.mediaId)).centerCrop().into(view)
                view.setOnClickListener { actions.onOpenPhoto(item.mediaId) }
                binding.llMedia.addView(view)
            }
        }

        private fun bindMarks(context: Context, answer: ForumAnswer) {
            val marks = listOfNotNull(
                context.getString(R.string.forum_mark_accepted).takeIf { answer.accepted },
                context.getString(R.string.forum_mark_verified).takeIf { answer.verified },
            )
            binding.tvMarks.visibility = if (marks.isEmpty()) View.GONE else View.VISIBLE
            binding.tvMarks.text = marks.joinToString(" · ")
        }

        private fun bindActions(context: Context, post: ForumPost, answer: ForumAnswer) {
            binding.actions.visibility = View.VISIBLE
            val mine = answer.author?.isMe == true
            val liked = answer.myReaction == "like"
            val notHelpful = answer.myReaction == "not_helpful"
            binding.tvLike.setCompoundDrawablesRelativeWithIntrinsicBounds(
                if (liked) R.drawable.ic_heart_filled else R.drawable.ic_heart,
                0,
                0,
                0,
            )
            styleReaction(context, binding.tvLike, answer.likeCount, liked, R.color.disease_red)
            styleReaction(context, binding.tvNotHelpful, answer.notHelpfulCount, notHelpful, R.color.on_surface)
            binding.tvLike.isEnabled = !mine
            binding.tvNotHelpful.isEnabled = !mine
            binding.tvLike.setOnClickListener { actions.onReact(answer, if (liked) null else "like") }
            binding.tvNotHelpful.setOnClickListener {
                actions.onReact(answer, if (notHelpful) null else "not_helpful")
            }
            binding.tvReply.visibility = if (post.locked) View.GONE else View.VISIBLE
            binding.tvReply.setOnClickListener { actions.onReply(answer) }
            val canAccept = post.author.isMe && !mine && answer.depth == 0
            binding.btnAccept.visibility = if (canAccept) View.VISIBLE else View.GONE
            binding.btnAccept.setText(if (answer.accepted) R.string.forum_unaccept else R.string.forum_accept)
            binding.btnAccept.setOnClickListener { actions.onAccept(answer) }
        }

        private fun styleReaction(context: Context, view: TextView, count: Int, active: Boolean, activeColor: Int) {
            view.text = if (count > 0) count.toString() else ""
            val color = ContextCompat.getColor(context, if (active) activeColor else R.color.on_surface_secondary)
            view.compoundDrawableTintList = ColorStateList.valueOf(color)
            view.setTextColor(color)
        }
    }

    private fun photoView(context: Context, width: Int, height: Int): ImageView {
        val view = ShapeableImageView(context)
        val margin = context.resources.getDimensionPixelSize(R.dimen.space_sm)
        view.layoutParams = LinearLayout.LayoutParams(width, height).apply { marginEnd = margin }
        view.scaleType = ImageView.ScaleType.CENTER_CROP
        view.shapeAppearanceModel = ShapeAppearanceModel.builder(context, R.style.ShapeAppearance_App_ForumPhoto, 0).build()
        view.setBackgroundColor(ContextCompat.getColor(context, R.color.surface_container))
        view.contentDescription = context.getString(R.string.forum_photo_description)
        return view
    }

    private companion object {
        const val TYPE_HEADER = 0
        const val TYPE_ANSWER = 1
        const val RATIO = 4f / 3f
        const val PADDING_SMALL = 6
    }
}
