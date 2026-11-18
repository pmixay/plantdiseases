package com.plantdiseases.app.ui.community.forum

import android.content.res.ColorStateList
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
            binding.ivKind.setImageResource(ForumUi.kindIcon(post.kind))
            binding.tvKind.setText(ForumUi.kindLabel(post.kind))
            binding.tvMeta.text = context.getString(
                R.string.forum_meta_space_time,
                ForumUi.spaceName(context, post.space, spaces),
                ForumUi.relativeTime(post.createdAt),
            )
            bindBadge(post)
            val title = ForumUi.postTitle(context, post.title, post.kind, post.ai?.disease)
            val highlight = ContextCompat.getColor(context, R.color.search_highlight)
            binding.tvTitle.text = ForumUi.highlight(title, terms, highlight)
            binding.tvExcerpt.visibility = if (post.body.isBlank()) View.GONE else View.VISIBLE
            binding.tvExcerpt.text = ForumUi.highlight(post.body, terms, highlight)

            val photo = post.media.firstOrNull()
            if (photo == null) {
                binding.ivPhoto.visibility = View.GONE
                Glide.with(binding.ivPhoto).clear(binding.ivPhoto)
            } else {
                binding.ivPhoto.visibility = View.VISIBLE
                Glide.with(binding.ivPhoto).load(mediaUrl(photo.mediaId)).centerCrop().into(binding.ivPhoto)
            }

            val ai = post.ai
            binding.tvAi.visibility = if (ai == null) View.GONE else View.VISIBLE
            if (ai != null) {
                val percent = ((ai.confidence ?: 0.0) * 100).toInt()
                binding.tvAi.text = context.getString(
                    R.string.forum_ai_summary,
                    ForumUi.conditionLabel(context, ai.disease),
                    percent,
                )
            }
            val excerpt = post.acceptedExcerpt
            binding.tvAccepted.visibility = if (post.solved && !excerpt.isNullOrBlank()) View.VISIBLE else View.GONE
            binding.tvAccepted.text = context.getString(R.string.forum_accepted_excerpt, excerpt.orEmpty())

            binding.tvLike.text = post.likeCount.toString()
            binding.tvLike.setCompoundDrawablesRelativeWithIntrinsicBounds(
                if (post.likedByMe) R.drawable.ic_heart_filled else R.drawable.ic_heart,
                0,
                0,
                0,
            )
            val likeColor = ContextCompat.getColor(
                context,
                if (post.likedByMe) R.color.disease_red else R.color.on_surface_secondary,
            )
            binding.tvLike.compoundDrawableTintList = ColorStateList.valueOf(likeColor)
            binding.tvLike.setTextColor(likeColor)
            binding.tvLike.isEnabled = !post.author.isMe
            binding.tvLike.setOnClickListener { onLike(post) }
            binding.tvAnswers.text = post.answerCount.toString()
            binding.tvAuthor.text = post.author.nickname
            binding.root.setOnClickListener { onOpen(post) }
        }

        private fun bindBadge(post: ForumPost) {
            val context = binding.root.context
            val (text, color) = when {
                post.solved -> R.string.forum_badge_solved to R.color.healthy_green
                post.urgent -> R.string.forum_badge_urgent to R.color.disease_red
                post.answerCount == 0 && (post.kind == "question" || post.kind == "scan") ->
                    R.string.forum_badge_unanswered to R.color.warning_amber
                post.pinned -> R.string.forum_badge_pinned to R.color.primary
                else -> null to null
            }
            if (text == null || color == null) {
                binding.tvBadge.visibility = View.GONE
                return
            }
            binding.tvBadge.visibility = View.VISIBLE
            binding.tvBadge.setText(text)
            binding.tvBadge.setTextColor(ContextCompat.getColor(context, color))
        }
    }

    private companion object {
        val DIFF = object : DiffUtil.ItemCallback<ForumPost>() {
            override fun areItemsTheSame(oldItem: ForumPost, newItem: ForumPost) = oldItem.id == newItem.id
            override fun areContentsTheSame(oldItem: ForumPost, newItem: ForumPost) = oldItem == newItem
        }
    }
}
