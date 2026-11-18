package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ModQueueItem
import com.plantdiseases.app.data.model.StaffMe
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.databinding.ActivityModerationBinding
import com.plantdiseases.app.databinding.ItemModQueueBinding
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Staff work in one place: reported content, content hidden automatically
 * after reports, appeals, frozen achievement rewards, and (for experts) posts
 * where helpers disagree with a confident AI verdict. Which queues show
 * depends on the member's roles; the server checks them again.
 */
@AndroidEntryPoint
class ModerationActivity : AppCompatActivity() {

    @Inject
    lateinit var forum: ForumRepository

    private lateinit var binding: ActivityModerationBinding
    private lateinit var moderation: ModerationController
    private var staff: StaffMe? = null
    private var kind = KIND_REPORTS

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityModerationBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        ForumInsets.apply(this, binding.appBar, binding.root)
        binding.rvQueue.layoutManager = LinearLayoutManager(this)
        moderation = ModerationController(this, forum) { load() }
        binding.swipeRefresh.setOnRefreshListener { load() }
        binding.chipQueues.setOnCheckedStateChangeListener { _, checked ->
            kind = when (checked.firstOrNull()) {
                R.id.chip_hidden -> KIND_HIDDEN
                R.id.chip_appeals -> KIND_APPEALS
                R.id.chip_rewards -> KIND_REWARDS
                R.id.chip_labels -> KIND_LABELS
                else -> KIND_REPORTS
            }
            load()
        }
        lifecycleScope.launch {
            forum.staffMe()
                .onSuccess { me ->
                    staff = me
                    configureChips(me)
                }
                .onFailure { showEmpty(ForumUi.errorMessage(this@ModerationActivity, it)) }
        }
    }

    private fun configureChips(me: StaffMe) {
        if (!me.isStaff) {
            showEmpty(getString(R.string.forum_error_forbidden))
            binding.chipQueues.visibility = View.GONE
            return
        }
        val moderator = me.isModerator
        binding.chipReports.visibility = if (moderator) View.VISIBLE else View.GONE
        binding.chipHidden.visibility = if (moderator) View.VISIBLE else View.GONE
        binding.chipAppeals.visibility = if (moderator) View.VISIBLE else View.GONE
        binding.chipRewards.visibility = if (moderator) View.VISIBLE else View.GONE
        binding.chipLabels.visibility = if (me.isExpert) View.VISIBLE else View.GONE
        if (moderator) binding.chipReports.isChecked = true else binding.chipLabels.isChecked = true
    }

    private fun load() {
        binding.swipeRefresh.isRefreshing = true
        lifecycleScope.launch {
            forum.moderationQueue(kind)
                .onSuccess { queue ->
                    binding.rvQueue.adapter = QueueAdapter(queue.items)
                    showEmpty(if (queue.items.isEmpty()) getString(R.string.mod_queue_empty) else null)
                }
                .onFailure { showEmpty(ForumUi.errorMessage(this@ModerationActivity, it)) }
            binding.swipeRefresh.isRefreshing = false
        }
    }

    private fun showEmpty(message: String?) {
        binding.tvEmpty.visibility = if (message == null) View.GONE else View.VISIBLE
        binding.tvEmpty.text = message
    }

    private inner class QueueAdapter(private val items: List<ModQueueItem>) : RecyclerView.Adapter<QueueAdapter.Holder>() {

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
            Holder(ItemModQueueBinding.inflate(LayoutInflater.from(parent.context), parent, false))

        override fun getItemCount(): Int = items.size

        override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(items[position])

        inner class Holder(private val binding: ItemModQueueBinding) : RecyclerView.ViewHolder(binding.root) {

            fun bind(item: ModQueueItem) {
                binding.btnSecondary.visibility = View.VISIBLE
                binding.btnOpen.visibility = if (item.postId != null) View.VISIBLE else View.GONE
                binding.btnOpen.setOnClickListener {
                    item.postId?.let { startActivity(PostDetailActivity.intent(this@ModerationActivity, it)) }
                }
                when (kind) {
                    KIND_REPORTS -> bindReport(item)
                    KIND_HIDDEN -> bindHidden(item)
                    KIND_APPEALS -> bindAppeal(item)
                    KIND_REWARDS -> bindReward(item)
                    else -> bindLabel(item)
                }
            }

            private fun bindReport(item: ModQueueItem) {
                val type = item.targetType ?: "post"
                val target = item.targetId ?: return
                severity(if (item.reportCount >= AUTO_HIDE) R.color.disease_red else R.color.warning_amber)
                binding.tvHeading.text = getString(
                    R.string.mod_report_heading,
                    targetLabel(type),
                    item.reportCount,
                    item.reasons.joinToString(", ") { reasonLabel(it) },
                )
                binding.tvExcerpt.text = item.excerpt.orEmpty()
                binding.tvMeta.text = metaLine(item)
                binding.btnPrimary.setText(R.string.mod_remove)
                binding.btnPrimary.setOnClickListener { moderation.remove(type, target) }
                binding.btnSecondary.setText(R.string.mod_keep)
                binding.btnSecondary.setOnClickListener { moderation.keep(type, target) }
            }

            private fun bindHidden(item: ModQueueItem) {
                val type = item.targetType ?: "post"
                val target = item.targetId ?: return
                severity(R.color.warning_amber)
                binding.tvHeading.text = getString(R.string.mod_hidden_heading, targetLabel(type))
                binding.tvExcerpt.text = item.excerpt.orEmpty()
                binding.tvMeta.text = metaLine(item)
                binding.btnPrimary.setText(R.string.mod_remove)
                binding.btnPrimary.setOnClickListener { moderation.remove(type, target) }
                binding.btnSecondary.setText(R.string.mod_restore)
                binding.btnSecondary.setOnClickListener { moderation.keep(type, target) }
            }

            private fun bindAppeal(item: ModQueueItem) {
                val appeal = item.appealId ?: return
                severity(R.color.neutral_blue)
                binding.tvHeading.text = getString(
                    R.string.mod_appeal_heading,
                    actionLabel(item.action),
                    item.reasonCode?.let { reasonLabel(it) }.orEmpty(),
                )
                binding.tvExcerpt.text = item.text?.takeIf { it.isNotBlank() } ?: getString(R.string.mod_appeal_no_text)
                binding.tvMeta.text = item.createdAt?.let { ForumUi.relativeTime(it) } ?: ""
                binding.btnPrimary.setText(R.string.mod_appeal_decide)
                binding.btnPrimary.setOnClickListener { moderation.resolveAppeal(appeal) }
                binding.btnSecondary.visibility = View.GONE
            }

            private fun bindReward(item: ModQueueItem) {
                val token = item.authorToken ?: return
                severity(R.color.warning_amber)
                binding.tvHeading.text = getString(R.string.mod_rewards_heading, item.nickname.orEmpty())
                binding.tvExcerpt.setText(
                    if (item.reason == "admin") R.string.mod_rewards_reason_admin else R.string.mod_rewards_reason_ring,
                )
                binding.tvMeta.text = item.createdAt?.let { ForumUi.relativeTime(it) } ?: ""
                binding.btnPrimary.setText(R.string.mod_rewards_review)
                binding.btnPrimary.setOnClickListener { moderation.clearRewardFreeze(token) }
                binding.btnSecondary.setText(R.string.mod_view_profile)
                binding.btnSecondary.setOnClickListener {
                    startActivity(HelperProfileActivity.intent(this@ModerationActivity, token))
                }
            }

            private fun bindLabel(item: ModQueueItem) {
                severity(R.color.neutral_blue)
                val context = this@ModerationActivity
                binding.tvHeading.text = getString(
                    R.string.mod_label_heading,
                    ForumUi.conditionLabel(context, item.aiDisease),
                    ((item.aiConfidence ?: 0.0) * PERCENT).toInt(),
                )
                binding.tvExcerpt.text = item.communityLabel
                    ?.let { getString(R.string.mod_label_community, ForumUi.conditionLabel(context, it)) }
                    ?: getString(R.string.mod_label_not_helpful, item.aiNotHelpful)
                binding.tvMeta.text = item.title.orEmpty()
                binding.btnPrimary.setText(R.string.mod_label_review)
                binding.btnPrimary.setOnClickListener {
                    item.postId?.let { startActivity(PostDetailActivity.intent(context, it)) }
                }
                binding.btnSecondary.visibility = View.GONE
                binding.btnOpen.visibility = View.GONE
            }

            private fun severity(color: Int) {
                binding.severity.setBackgroundColor(ContextCompat.getColor(this@ModerationActivity, color))
            }

            private fun metaLine(item: ModQueueItem): String = listOfNotNull(
                item.author?.nickname?.takeIf { it.isNotBlank() },
                item.space,
                item.state?.takeIf { it != "visible" }?.let { getString(R.string.mod_state, it) },
            ).joinToString(" · ")
        }
    }

    private fun targetLabel(type: String): String =
        getString(if (type == "answer") R.string.mod_target_answer else R.string.mod_target_post)

    private fun reasonLabel(code: String): String {
        val res = ForumUi.REPORT_REASONS.firstOrNull { it.first == code }?.second
            ?: ForumUi.MOD_REASONS.firstOrNull { it.first == code }?.second
        return res?.let { getString(it) } ?: code
    }

    private fun actionLabel(action: String?): String = getString(
        when (action) {
            "remove" -> R.string.mod_action_remove
            "hide_auto" -> R.string.mod_action_hide_auto
            "warn" -> R.string.mod_action_warn
            "mute" -> R.string.mod_action_mute
            "ban" -> R.string.mod_action_ban
            "lock" -> R.string.mod_action_lock
            "move" -> R.string.mod_action_move
            else -> R.string.mod_action_other
        },
    )

    companion object {
        private const val KIND_REPORTS = "reports"
        private const val KIND_HIDDEN = "hidden"
        private const val KIND_APPEALS = "appeals"
        private const val KIND_REWARDS = "rewards"
        private const val KIND_LABELS = "label_review"
        private const val AUTO_HIDE = 3
        private const val PERCENT = 100

        fun intent(context: Context): Intent = Intent(context, ModerationActivity::class.java)
    }
}
