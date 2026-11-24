package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.util.TypedValue
import android.view.Gravity
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

/** What the thread screen does when someone taps something in the thread. */
interface ThreadActions {
    fun onLikePost()
    fun onOpenAuthor(authorToken: String)
    fun onEditPost()
    fun onOpenPost(postId: String)
    fun onOpenPhoto(mediaIds: List<String>, index: Int, from: View)

    /** False when the answer's ⋯ menu would be empty (e.g. PlantVision's for members). */
    fun hasAnswerMenu(answer: ForumAnswer): Boolean
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
    private val avatarUrl: AvatarUrl,
    private val actions: ThreadActions,
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private var post: ForumPost? = null
    private var spaces: List<ForumSpace> = emptyList()

    /** The answer a notification opened the post at, tinted for a moment. */
    var highlightedAnswerId: String? = null
        set(value) {
            if (field == value) return
            field = value
            notifyDataSetChanged()
        }

    /** Adapter position of [answerId], or -1 when the thread lacks it. */
    fun positionOf(answerId: String): Int {
        val index = post?.answers?.indexOfFirst { it.id == answerId } ?: -1
        return if (index < 0) -1 else index + 1
    }

    /** A guest's like, not-helpful and reply actions are drawn locked (gray);
     *  their taps still reach [actions], which offer sign-in. */
    var guest: Boolean = false
        set(value) {
            if (field == value) return
            field = value
            notifyDataSetChanged()
        }

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
            ForumViews.bindAvatar(binding.tvAvatar, binding.ivAvatarPhoto, nickname, post.author, avatarUrl)
            binding.tvAuthor.text = nickname
            binding.tvAuthor.setOnClickListener { post.author.authorToken?.let(actions::onOpenAuthor) }
            binding.tvMeta.text = ForumViews.meta(context, post, spaces)
            ForumViews.bindStatus(binding.tvBadge, ForumViews.threadStatus(post))
            binding.tvTitle.text = ForumUi.postTitle(context, post.title, post.kind, post.ai?.disease)
            binding.tvBody.visibility = if (post.body.isBlank()) View.GONE else View.VISIBLE
            binding.tvBody.text = post.body
            bindTags(context, post)
            bindPhotos(context, post.media)
            bindUpdates(context, post)
            bindLike(context, post)
            binding.tvAnswerCount.text = post.answerCount.toString()
            binding.btnUpdate.visibility = if (post.author.isMe && post.kind != "tip") View.VISIBLE else View.GONE
            binding.btnUpdate.setOnClickListener { actions.onEditPost() }
            binding.tvAnswersTitle.text = context.resources.getQuantityString(
                R.plurals.forum_answers_title,
                post.answerCount,
                post.answerCount,
            )
        }

        /** Species and condition as two pills. */
        private fun bindTags(context: Context, post: ForumPost) {
            val species = post.species?.takeIf { it.isNotBlank() }
            val condition = post.condition?.takeIf { it.isNotBlank() }?.let { ForumUi.conditionLabel(context, it) }
            binding.tagsRow.visibility = if (species == null && condition == null) View.GONE else View.VISIBLE
            binding.tvSpecies.visibility = if (species == null) View.GONE else View.VISIBLE
            binding.tvSpecies.text = species
            ForumViews.setSmallIcon(binding.tvSpecies, R.drawable.ic_leaf, TAG_ICON_DP)
            binding.tvSpecies.compoundDrawableTintList =
                ColorStateList.valueOf(ContextCompat.getColor(context, R.color.primary))
            binding.tvCondition.visibility = if (condition == null) View.GONE else View.VISIBLE
            binding.tvCondition.text = condition
            ForumViews.setSmallIcon(binding.tvCondition, R.drawable.ic_first_aid, TAG_ICON_DP)
            binding.tvCondition.compoundDrawableTintList =
                ColorStateList.valueOf(ContextCompat.getColor(context, R.color.on_surface_secondary))
        }

        private fun bindPhotos(context: Context, media: List<ForumMedia>) {
            binding.llPhotos.removeAllViews()
            binding.photosScroll.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
            val height = context.resources.getDimensionPixelSize(R.dimen.forum_photo_height)
            val ids = media.map { it.mediaId }
            media.forEachIndexed { index, item ->
                val ratio = if (item.width > 0 && item.height > 0) item.width.toFloat() / item.height else RATIO
                val view = if (media.size == 1) {
                    // One photo fills the width (the scroller fills its viewport).
                    photoView(context, 0, context.resources.getDimensionPixelSize(R.dimen.forum_single_photo_height))
                        .also { photo ->
                            (photo.layoutParams as LinearLayout.LayoutParams).apply {
                                weight = 1f
                                marginEnd = 0
                            }
                        }
                } else {
                    photoView(context, (height * ratio).toInt().coerceIn(height / 2, height * 2), height)
                }
                Glide.with(view).load(mediaUrl(item.mediaId)).centerCrop().into(view)
                view.setOnClickListener { actions.onOpenPhoto(ids, index, it) }
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
                when {
                    guest -> R.color.guest_locked
                    post.likedByMe -> R.color.disease_red
                    else -> R.color.on_surface
                },
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
            val res = context.resources
            binding.answerRoot.foreground = if (answer.id == highlightedAnswerId) {
                android.graphics.drawable.ColorDrawable(
                    androidx.core.graphics.ColorUtils.setAlphaComponent(
                        ContextCompat.getColor(context, R.color.primary),
                        HIGHLIGHT_ALPHA,
                    ),
                )
            } else {
                null
            }
            binding.answerRoot.setPaddingRelative(
                res.getDimensionPixelSize(R.dimen.space_lg) + res.getDimensionPixelSize(R.dimen.forum_reply_indent) * answer.depth,
                binding.answerRoot.paddingTop,
                binding.answerRoot.paddingEnd,
                binding.answerRoot.paddingBottom,
            )
            binding.threadLine.visibility = if (answer.depth > 0) View.VISIBLE else View.GONE
            bindCard(context, answer)
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
            bindSteps(context, answer.steps)
            bindDiagnosis(context, answer)
            bindMedia(context, answer.media)
            bindActions(context, post, answer)
        }

        /** PlantVision's answer is a green-tinted card; the accepted solution is outlined and labelled. */
        private fun bindCard(context: Context, answer: ForumAnswer) {
            val background = when {
                answer.isRemoved -> null
                answer.isAi -> R.drawable.bg_forum_ai_card
                answer.accepted -> R.drawable.bg_forum_solution_card
                else -> null
            }
            val card = binding.answerCard
            if (background == null) {
                card.background = null
                card.setPadding(0, 0, 0, 0)
            } else {
                card.setBackgroundResource(background)
                val padding = context.resources.getDimensionPixelSize(R.dimen.space_md)
                card.setPadding(padding, padding, padding, context.resources.getDimensionPixelSize(R.dimen.space_xs))
            }
            // "✓ Solution, Expert-checked", "✓ Solution" or "Expert-checked" above the answer.
            val marks = listOfNotNull(
                context.getString(R.string.forum_mark_solution).takeIf { answer.accepted },
                context.getString(R.string.forum_badge_expert_verified).takeIf { answer.verified },
            ).takeUnless { answer.isRemoved }.orEmpty()
            binding.tvMarks.visibility = if (marks.isEmpty()) View.GONE else View.VISIBLE
            if (marks.isNotEmpty()) {
                binding.tvMarks.text = marks.joinToString(", ")
                val icon = if (answer.accepted) R.drawable.ic_check else R.drawable.ic_shield_check
                ForumViews.setSmallIcon(binding.tvMarks, icon, MARK_ICON_DP)
                binding.tvMarks.compoundDrawableTintList =
                    ColorStateList.valueOf(ContextCompat.getColor(context, R.color.healthy_green))
            }
        }

        private fun bindAuthor(context: Context, post: ForumPost, answer: ForumAnswer) {
            binding.ivAi.visibility = if (answer.isAi) View.VISIBLE else View.GONE
            binding.tvAvatar.visibility = if (answer.isAi) View.GONE else View.VISIBLE
            if (answer.isAi) {
                Glide.with(binding.ivAvatarPhoto).clear(binding.ivAvatarPhoto)
                binding.ivAvatarPhoto.visibility = View.GONE
            }
            val author = answer.author
            val name = when {
                answer.isAi -> context.getString(R.string.forum_ai_answer_title)
                author == null -> context.getString(R.string.forum_removed_author)
                else -> author.nickname.ifBlank { context.getString(R.string.community_anonymous_author) }
            }
            binding.tvAuthor.text = name
            binding.tvAuthor.setTextColor(
                ContextCompat.getColor(context, if (answer.isAi) R.color.forum_ai_fg else R.color.on_surface),
            )
            if (!answer.isAi) ForumViews.bindAvatar(binding.tvAvatar, binding.ivAvatarPhoto, name, author, avatarUrl)
            binding.tvAuthor.setOnClickListener { author?.authorToken?.let(actions::onOpenAuthor) }
            bindAuthorBadge(context, answer)
            binding.tvTime.text = ForumUi.relativeTime(answer.createdAt)
            binding.btnMore.visibility = if (answer.isRemoved || !actions.hasAnswerMenu(answer)) View.GONE else View.VISIBLE
            binding.btnMore.setOnClickListener { actions.onAnswerMenu(answer, it) }
            binding.btnAccept.visibility = View.GONE
            if (post.locked) binding.tvReply.visibility = View.GONE
        }

        /** "Asker" next to the question author's own answers and replies. */
        private fun bindAuthorBadge(context: Context, answer: ForumAnswer) {
            val badge = binding.tvAuthorBadge
            val asker = answer.author?.isPostAuthor == true
            badge.visibility = if (asker) View.VISIBLE else View.GONE
            if (!asker) return
            badge.setText(R.string.forum_badge_asker)
            badge.setTextColor(ContextCompat.getColor(context, R.color.on_surface))
            badge.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(context, R.color.surface_container))
            ForumViews.setSmallIcon(badge, null, TAG_ICON_DP)
        }

        private fun bindRemoved(context: Context) {
            binding.aiBlock.visibility = View.GONE
            binding.tvBody.visibility = View.VISIBLE
            binding.tvBody.setText(R.string.forum_answer_removed)
            binding.tvBody.setTypeface(null, Typeface.ITALIC)
            binding.tvBody.setTextColor(ContextCompat.getColor(context, R.color.on_surface))
            binding.llSteps.visibility = View.GONE
            binding.tvDiagnosis.visibility = View.GONE
            binding.llMedia.visibility = View.GONE
            binding.actions.visibility = View.GONE
        }

        private fun bindAi(context: Context, post: ForumPost, answer: ForumAnswer) {
            binding.aiBlock.visibility = if (answer.isAi) View.VISIBLE else View.GONE
            if (!answer.isAi) return
            val confidence = ((post.ai?.confidence ?: 0.0) * PERCENT).toInt()
            binding.tvAiVerdict.text = ForumUi.conditionLabel(context, answer.diagnosis ?: post.ai?.disease)
            binding.pbAi.progress = confidence
            binding.tvAiConfidence.text = context.getString(R.string.forum_ai_confidence, confidence)
        }

        /** Steps as a numbered list with round number markers. */
        private fun bindSteps(context: Context, steps: List<String>) {
            val container = binding.llSteps
            container.removeAllViews()
            container.visibility = if (steps.isEmpty()) View.GONE else View.VISIBLE
            val res = context.resources
            val size = res.getDimensionPixelSize(R.dimen.forum_step_number)
            val primary = ContextCompat.getColor(context, R.color.primary)
            val onSurface = ContextCompat.getColor(context, R.color.on_surface)
            steps.forEachIndexed { index, step ->
                val row = LinearLayout(context)
                row.orientation = LinearLayout.HORIZONTAL
                row.layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = res.getDimensionPixelSize(R.dimen.space_sm) }
                val number = TextView(context)
                number.layoutParams = LinearLayout.LayoutParams(size, size)
                number.setBackgroundResource(R.drawable.bg_forum_step_number)
                number.gravity = Gravity.CENTER
                number.text = (index + 1).toString()
                number.setTextColor(primary)
                number.setTypeface(number.typeface, Typeface.BOLD)
                number.setTextSize(TypedValue.COMPLEX_UNIT_PX, res.getDimension(R.dimen.forum_text_meta))
                val label = TextView(context)
                label.layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginStart = res.getDimensionPixelSize(R.dimen.forum_step_gap)
                }
                label.text = step
                label.setTextColor(onSurface)
                label.setTextSize(TypedValue.COMPLEX_UNIT_PX, res.getDimension(R.dimen.text_body))
                label.setPadding(0, res.getDimensionPixelSize(R.dimen.space_xxs), 0, 0)
                row.addView(number)
                row.addView(label)
                container.addView(row)
            }
        }

        private fun bindDiagnosis(context: Context, answer: ForumAnswer) {
            val diagnosis = answer.diagnosis?.takeIf { !answer.isAi }
            binding.tvDiagnosis.visibility = if (diagnosis == null) View.GONE else View.VISIBLE
            if (diagnosis == null) return
            binding.tvDiagnosis.text = context.getString(
                R.string.forum_diagnosis_tag,
                ForumUi.conditionLabel(context, diagnosis),
            )
            ForumViews.setSmallIcon(binding.tvDiagnosis, R.drawable.ic_first_aid, TAG_ICON_DP)
            binding.tvDiagnosis.compoundDrawableTintList =
                ColorStateList.valueOf(ContextCompat.getColor(context, R.color.primary))
        }

        private fun bindMedia(context: Context, media: List<ForumMedia>) {
            binding.llMedia.removeAllViews()
            binding.llMedia.visibility = if (media.isEmpty()) View.GONE else View.VISIBLE
            val size = context.resources.getDimensionPixelSize(R.dimen.forum_answer_photo_size)
            val ids = media.map { it.mediaId }
            media.forEachIndexed { index, item ->
                val view = photoView(context, size, size)
                Glide.with(view).load(mediaUrl(item.mediaId)).centerCrop().into(view)
                view.setOnClickListener { actions.onOpenPhoto(ids, index, it) }
                binding.llMedia.addView(view)
            }
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
            binding.tvReply.compoundDrawableTintList = ColorStateList.valueOf(
                ContextCompat.getColor(context, if (guest) R.color.guest_locked else R.color.on_surface_secondary),
            )
            binding.tvReply.setTextColor(
                ContextCompat.getColor(context, if (guest) R.color.guest_locked else R.color.on_surface),
            )
            binding.tvReply.setOnClickListener { actions.onReply(answer) }
            val canAccept = post.author.isMe && !mine && answer.depth == 0
            binding.btnAccept.visibility = if (canAccept) View.VISIBLE else View.GONE
            binding.btnAccept.setText(if (answer.accepted) R.string.forum_unaccept else R.string.forum_accept)
            binding.btnAccept.setOnClickListener { actions.onAccept(answer) }
        }

        private fun styleReaction(context: Context, view: TextView, count: Int, active: Boolean, activeColor: Int) {
            view.text = if (count > 0) count.toString() else ""
            val color = ContextCompat.getColor(
                context,
                when {
                    guest -> R.color.guest_locked
                    active -> activeColor
                    else -> R.color.on_surface
                },
            )
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
        const val PERCENT = 100
        const val TAG_ICON_DP = 14
        const val MARK_ICON_DP = 16
        const val HIGHLIGHT_ALPHA = 0x29
    }
}
