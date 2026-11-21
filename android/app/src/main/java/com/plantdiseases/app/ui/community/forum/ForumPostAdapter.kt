package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.databinding.ItemForumPostBinding

/**
 * Post cards for the feed, search results and profiles. Posts can only be
 * liked; the heart toggles through [onLike]. [terms] highlights search words.
 */
class ForumPostAdapter(
    private val mediaUrl: (String) -> String,
    private val avatarUrl: AvatarUrl,
    private val onOpen: (ForumPost) -> Unit,
    private val onLike: (ForumPost) -> Unit,
) : ListAdapter<ForumPost, ForumPostAdapter.Holder>(DIFF) {

    var spaces: List<ForumSpace> = emptyList()
        set(value) {
            field = value
            notifyItemRangeChanged(0, itemCount)
        }

    var terms: List<String> = emptyList()

    /** Optimistically flip a like while the request is in flight. */
    fun updateLike(postId: String, liked: Boolean, count: Int) {
        submitList(
            currentList.map { if (it.id == postId) it.copy(likedByMe = liked, likeCount = count) else it },
        )
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
        Holder(ItemForumPostBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(getItem(position))

    inner class Holder(private val binding: ItemForumPostBinding) : RecyclerView.ViewHolder(binding.root) {

        fun bind(post: ForumPost) {
            val context = binding.root.context
            val nickname = post.author.nickname.ifBlank { context.getString(R.string.community_anonymous_author) }
            ForumViews.bindAvatar(binding.tvAvatar, binding.ivAvatarPhoto, nickname, post.author, avatarUrl)
            binding.tvAuthor.text = nickname
            binding.tvMeta.text = ForumViews.meta(context, post, spaces)
            ForumViews.bindStatus(binding.tvBadge, ForumViews.feedStatus(post))

            val title = ForumUi.postTitle(context, post.title, post.kind, post.ai?.disease)
            val highlight = ContextCompat.getColor(context, R.color.search_highlight)
            binding.tvTitle.text = ForumUi.highlight(title, terms, highlight)
            binding.tvExcerpt.visibility = if (post.body.isBlank()) View.GONE else View.VISIBLE
            binding.tvExcerpt.text = ForumUi.highlight(post.body, terms, highlight)

            bindPhoto(post)
            bindSolution(context, post)
            bindActions(context, post)
            binding.root.setOnClickListener { onOpen(post) }
        }

        /** First photo; scans carry PlantVision's verdict on it (or under the title without a photo). */
        private fun bindPhoto(post: ForumPost) {
            val context = binding.root.context
            val photo = post.media.firstOrNull()
            val verdict = post.ai?.let { ai ->
                context.getString(
                    R.string.forum_ai_summary,
                    ForumUi.conditionLabel(context, ai.disease),
                    ((ai.confidence ?: 0.0) * PERCENT).toInt(),
                )
            }
            if (photo == null) {
                binding.photoFrame.visibility = View.GONE
                Glide.with(binding.ivPhoto).clear(binding.ivPhoto)
            } else {
                binding.photoFrame.visibility = View.VISIBLE
                Glide.with(binding.ivPhoto).load(mediaUrl(photo.mediaId)).centerCrop().into(binding.ivPhoto)
            }
            binding.tvAiPhoto.visibility = if (photo != null && verdict != null) View.VISIBLE else View.GONE
            binding.tvAiPhoto.text = verdict
            binding.tvAi.visibility = if (photo == null && verdict != null) View.VISIBLE else View.GONE
            binding.tvAi.text = verdict
            val photos = maxOf(post.mediaCount, post.media.size)
            binding.tvPhotoCount.visibility = if (photo != null && photos > 1) View.VISIBLE else View.GONE
            binding.tvPhotoCount.text = photos.toString()
        }

        private fun bindSolution(context: Context, post: ForumPost) {
            val excerpt = post.acceptedExcerpt?.takeIf { post.solved && it.isNotBlank() }
            binding.tvAccepted.visibility = if (excerpt == null) View.GONE else View.VISIBLE
            if (excerpt == null) return
            val label = context.getString(R.string.forum_mark_solution)
            val text = SpannableStringBuilder(label).append("  ").append(excerpt)
            text.setSpan(StyleSpan(Typeface.BOLD), 0, label.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            text.setSpan(
                ForegroundColorSpan(ContextCompat.getColor(context, R.color.healthy_green)),
                0,
                label.length,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
            binding.tvAccepted.text = text
        }

        private fun bindActions(context: Context, post: ForumPost) {
            binding.tvLike.text = post.likeCount.toString()
            binding.tvLike.setCompoundDrawablesRelativeWithIntrinsicBounds(
                if (post.likedByMe) R.drawable.ic_heart_filled else R.drawable.ic_heart,
                0,
                0,
                0,
            )
            val likeColor = ContextCompat.getColor(
                context,
                if (post.likedByMe) R.color.disease_red else R.color.on_surface,
            )
            binding.tvLike.compoundDrawableTintList = ColorStateList.valueOf(likeColor)
            binding.tvLike.setTextColor(likeColor)
            binding.tvLike.isEnabled = !post.author.isMe
            binding.tvLike.setOnClickListener { onLike(post) }
            binding.tvAnswers.text = post.answerCount.toString()
            // Clickable so the card's pressed state is not passed down to it
            // (a non-clickable child with a ripple lights up with the card).
            binding.tvAnswers.setOnClickListener { onOpen(post) }
            // Invite an answer where one is still missing: answering is what
            // the levels and achievements reward.
            val askingKind = post.kind == "question" || post.kind == "scan"
            val needsHelp = askingKind && !post.solved && !post.locked && post.answerCount == 0 && !post.author.isMe
            binding.btnHelp.visibility = if (needsHelp) View.VISIBLE else View.GONE
            binding.btnHelp.setOnClickListener { onOpen(post) }
        }
    }

    private companion object {
        val DIFF = object : DiffUtil.ItemCallback<ForumPost>() {
            override fun areItemsTheSame(oldItem: ForumPost, newItem: ForumPost) = oldItem.id == newItem.id
            override fun areContentsTheSame(oldItem: ForumPost, newItem: ForumPost) = oldItem == newItem
        }
        const val PERCENT = 100
    }
}
