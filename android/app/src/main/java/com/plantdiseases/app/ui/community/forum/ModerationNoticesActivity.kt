package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputFilter
import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.ModNotice
import com.plantdiseases.app.data.model.ModNotices
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.databinding.ActivityModerationNoticesBinding
import com.plantdiseases.app.databinding.ItemModQueueBinding
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Moderation decisions about the member's own content or account, each with
 * its reason, and an Appeal button that works once per decision. A different
 * moderator reviews the appeal.
 */
@AndroidEntryPoint
class ModerationNoticesActivity : AppCompatActivity() {

    @Inject
    lateinit var forum: ForumRepository

    private lateinit var binding: ActivityModerationNoticesBinding

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityModerationNoticesBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        ForumInsets.apply(this, binding.appBar, binding.root)
        binding.rvNotices.layoutManager = LinearLayoutManager(this)
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            forum.notices()
                .onSuccess(::render)
                .onFailure { error ->
                    binding.tvEmpty.visibility = View.VISIBLE
                    binding.tvEmpty.text = ForumUi.errorMessage(this@ModerationNoticesActivity, error)
                }
        }
    }

    private fun render(notices: ModNotices) {
        val sanction = notices.activeSanction
        binding.tvSanction.visibility = if (sanction == null) View.GONE else View.VISIBLE
        if (sanction != null) {
            val until = sanction.until?.let {
                DateUtils.formatDateTime(this, it * MILLIS, DateUtils.FORMAT_SHOW_DATE or DateUtils.FORMAT_SHOW_TIME)
            }
            binding.tvSanction.text = when {
                sanction.kind == "ban" && until == null -> getString(R.string.mod_notice_banned)
                sanction.kind == "ban" -> getString(R.string.mod_notice_banned_until, until)
                else -> getString(R.string.mod_notice_muted_until, until.orEmpty())
            }
        }
        binding.rvNotices.adapter = NoticeAdapter(notices.items)
        binding.tvEmpty.visibility = if (notices.items.isEmpty()) View.VISIBLE else View.GONE
        binding.tvEmpty.setText(R.string.mod_notices_empty)
    }

    private fun appeal(notice: ModNotice) {
        val input = EditText(this)
        input.hint = getString(R.string.mod_appeal_hint)
        input.filters = arrayOf(InputFilter.LengthFilter(APPEAL_MAX))
        val padding = resources.getDimensionPixelSize(R.dimen.space_lg)
        input.setPadding(padding, padding, padding, padding)
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.mod_appeal)
            .setMessage(R.string.mod_appeal_message)
            .setView(input)
            .setPositiveButton(R.string.mod_appeal_send) { _, _ ->
                lifecycleScope.launch {
                    forum.appeal(notice.actionId, input.text.toString().trim())
                        .onSuccess {
                            Toast.makeText(this@ModerationNoticesActivity, R.string.mod_appeal_sent, Toast.LENGTH_SHORT).show()
                            load()
                        }
                        .onFailure {
                            Toast.makeText(
                                this@ModerationNoticesActivity,
                                ForumUi.errorMessage(this@ModerationNoticesActivity, it),
                                Toast.LENGTH_LONG,
                            ).show()
                        }
                }
            }
            .setNegativeButton(R.string.forum_cancel, null)
            .show()
    }

    private inner class NoticeAdapter(private val items: List<ModNotice>) : RecyclerView.Adapter<NoticeAdapter.Holder>() {

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
            Holder(ItemModQueueBinding.inflate(LayoutInflater.from(parent.context), parent, false))

        override fun getItemCount(): Int = items.size

        override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(items[position])

        inner class Holder(private val binding: ItemModQueueBinding) : RecyclerView.ViewHolder(binding.root) {
            fun bind(notice: ModNotice) {
                val context = this@ModerationNoticesActivity
                val reason = ForumUi.MOD_REASONS.firstOrNull { it.first == notice.reasonCode }?.second
                binding.severity.setBackgroundColor(ContextCompat.getColor(context, R.color.warning_amber))
                binding.tvHeading.text = getString(
                    R.string.mod_notice_heading,
                    actionText(notice),
                    reason?.let { getString(it) } ?: getString(R.string.mod_reason_reports),
                )
                binding.tvExcerpt.text = notice.postTitle.orEmpty()
                binding.tvExcerpt.visibility = if (notice.postTitle.isNullOrBlank()) View.GONE else View.VISIBLE
                binding.tvMeta.text = listOfNotNull(
                    ForumUi.relativeTime(notice.createdAt).toString(),
                    getString(R.string.mod_notice_automatic).takeIf { notice.automatic },
                    notice.appealState?.let { appealStateText(it) },
                ).joinToString(" · ")
                binding.btnPrimary.visibility = if (notice.canAppeal) View.VISIBLE else View.GONE
                binding.btnPrimary.setText(R.string.mod_appeal)
                binding.btnPrimary.setOnClickListener { appeal(notice) }
                binding.btnSecondary.visibility = View.GONE
                binding.btnOpen.visibility = if (notice.postId != null && notice.action != "remove") View.VISIBLE else View.GONE
                binding.btnOpen.setOnClickListener {
                    notice.postId?.let { startActivity(PostDetailActivity.intent(context, it)) }
                }
            }
        }
    }

    private fun actionText(notice: ModNotice): String = getString(
        when (notice.action) {
            "remove" -> if (notice.targetType == "answer") R.string.mod_notice_answer_removed else R.string.mod_notice_post_removed
            "hide_auto" -> R.string.mod_notice_hidden
            "warn" -> R.string.mod_notice_warned
            "mute" -> R.string.mod_notice_muted
            "ban" -> R.string.mod_notice_ban
            "lock" -> R.string.mod_notice_locked
            "move" -> R.string.mod_notice_moved
            else -> R.string.mod_action_other
        },
    )

    private fun appealStateText(state: String): String = getString(
        when (state) {
            "overturned" -> R.string.mod_appeal_state_overturned
            "upheld" -> R.string.mod_appeal_state_upheld
            else -> R.string.mod_appeal_state_pending
        },
    )

    companion object {
        private const val MILLIS = 1000L
        private const val APPEAL_MAX = 500

        fun intent(context: Context): Intent = Intent(context, ModerationNoticesActivity::class.java)
    }
}
