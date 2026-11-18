package com.plantdiseases.app.ui.community.forum

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.text.format.DateUtils
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.billing.ProUi
import com.plantdiseases.app.data.model.AchievementProgress
import com.plantdiseases.app.data.model.DiscountState
import com.plantdiseases.app.data.model.HelperProfile
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.databinding.ActivityHelperProfileBinding
import com.plantdiseases.app.databinding.ItemAchievementBinding
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * A member's level, helper stats and achievements. On your own profile it
 * also shows the PRO discount your achievements have earned (2 % each, up to
 * 40 %) — only while PRO is offered at all ([ProUi.ENABLED]).
 */
@AndroidEntryPoint
class HelperProfileActivity : AppCompatActivity() {

    @Inject
    lateinit var forum: ForumRepository

    private lateinit var binding: ActivityHelperProfileBinding
    private val authorToken: String? by lazy { intent.getStringExtra(EXTRA_AUTHOR_TOKEN) }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityHelperProfileBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        ForumInsets.apply(this, binding.appBar, binding.root)
        binding.rvAchievements.layoutManager = GridLayoutManager(this, COLUMNS)
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            val token = authorToken
            val result = if (token == null) forum.myAchievements() else forum.helperProfile(token)
            binding.progress.visibility = View.GONE
            result
                .onSuccess(::render)
                .onFailure { error ->
                    binding.tvError.visibility = View.VISIBLE
                    binding.tvError.text = ForumUi.errorMessage(this@HelperProfileActivity, error)
                }
        }
    }

    private fun render(profile: HelperProfile) {
        binding.scroll.visibility = View.VISIBLE
        val nickname = profile.nickname.ifBlank { getString(R.string.community_anonymous_author) }
        binding.tvAvatar.text = Avatars.monogramForNick(nickname)
        binding.tvAvatar.backgroundTintList = ColorStateList.valueOf(Avatars.colorFor(nickname))
        binding.tvNickname.text = nickname
        binding.tvLevel.text = getString(
            R.string.forum_level_line,
            getString(ForumUi.levelName(profile.level)),
            profile.level,
            profile.leaves,
        )
        val next = profile.nextLevelLeaves
        if (next == null) {
            binding.pbLevel.progress = PERCENT
            binding.tvNextLevel.setText(R.string.forum_level_max)
        } else {
            binding.pbLevel.progress = (profile.leaves * PERCENT / next.coerceAtLeast(1)).coerceIn(0, PERCENT)
            binding.tvNextLevel.text = getString(
                R.string.forum_level_next,
                next - profile.leaves,
                getString(ForumUi.levelName(profile.level + 1)),
            )
        }
        binding.tvStatAccepted.text = getString(R.string.forum_stat_accepted, profile.stats.acceptedAnswers)
        binding.tvStatSaved.text = getString(R.string.forum_stat_saved, profile.stats.plantsSaved)
        binding.tvStatTaught.text = getString(R.string.forum_stat_taught, profile.stats.labelsTaught)
        renderDiscount(profile.discount)
        binding.tvAchievementsTitle.text = getString(R.string.forum_achievements_title, profile.earned, profile.total)
        binding.rvAchievements.adapter = AchievementAdapter(profile.achievements, ::explain)
    }

    private fun renderDiscount(discount: DiscountState?) {
        // The discount only exists for the viewer's own profile, and only while
        // PRO is offered; otherwise the card stays hidden.
        if (discount == null || !ProUi.ENABLED) {
            binding.cardDiscount.visibility = View.GONE
            return
        }
        binding.cardDiscount.visibility = View.VISIBLE
        binding.tvDiscount.text = getString(R.string.forum_discount_title, discount.percent)
        binding.pbDiscount.max = discount.cap
        binding.pbDiscount.progress = discount.percent
        val unlocksAt = discount.unlocksAt
        binding.tvDiscountDetail.text = when {
            discount.frozen -> getString(R.string.forum_discount_frozen)
            unlocksAt != null -> getString(
                R.string.forum_discount_waiting,
                DateUtils.formatDateTime(this, unlocksAt * MILLIS, DateUtils.FORMAT_SHOW_DATE),
            )
            discount.percent >= discount.cap -> getString(R.string.forum_discount_max, discount.cap)
            else -> getString(
                R.string.forum_discount_detail,
                discount.achievements,
                discount.perAchievement,
                (discount.cap - discount.percent) / discount.perAchievement.coerceAtLeast(1),
                discount.cap,
            )
        }
    }

    private fun explain(item: AchievementProgress) {
        val tiers = item.tiers.joinToString("\n") { tier ->
            val line = if (tier.awardedAt != null) {
                R.string.forum_achievement_tier_earned
            } else {
                R.string.forum_achievement_tier_locked
            }
            getString(line, tier.tier, tier.threshold)
        }
        MaterialAlertDialogBuilder(this)
            .setTitle(ForumUi.achievementTitle(item.key))
            .setMessage(getString(ForumUi.achievementDescription(item.key)) + "\n\n" + tiers)
            .setPositiveButton(R.string.forum_close, null)
            .show()
    }

    private class AchievementAdapter(
        private val items: List<AchievementProgress>,
        private val onClick: (AchievementProgress) -> Unit,
    ) : RecyclerView.Adapter<AchievementAdapter.Holder>() {

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder =
            Holder(ItemAchievementBinding.inflate(LayoutInflater.from(parent.context), parent, false))

        override fun getItemCount(): Int = items.size

        override fun onBindViewHolder(holder: Holder, position: Int) = holder.bind(items[position])

        inner class Holder(private val binding: ItemAchievementBinding) : RecyclerView.ViewHolder(binding.root) {
            fun bind(item: AchievementProgress) {
                val context = binding.root.context
                val earned = item.earnedTiers > 0
                binding.ivBadge.setImageResource(ForumUi.achievementIcon(item.key))
                val color = ContextCompat.getColor(
                    context,
                    when {
                        item.earnedTiers >= item.tiers.size && item.tiers.isNotEmpty() -> R.color.warning_amber
                        earned -> R.color.primary
                        else -> R.color.on_surface_secondary
                    },
                )
                binding.ivBadge.imageTintList = ColorStateList.valueOf(color)
                binding.ivBadge.alpha = if (earned) 1f else LOCKED_ALPHA
                binding.tvTitle.setText(ForumUi.achievementTitle(item.key))
                val nextTier = item.tiers.firstOrNull { it.awardedAt == null }
                binding.tvProgress.text = when {
                    nextTier == null -> context.getString(R.string.forum_achievement_complete)
                    item.tiers.size > 1 -> context.getString(
                        R.string.forum_achievement_progress_tier,
                        item.value.coerceAtMost(nextTier.threshold),
                        nextTier.threshold,
                        nextTier.tier,
                        item.tiers.size,
                    )
                    else -> context.getString(
                        R.string.forum_achievement_progress,
                        item.value.coerceAtMost(nextTier.threshold),
                        nextTier.threshold,
                    )
                }
                binding.root.setOnClickListener { onClick(item) }
            }
        }
    }

    companion object {
        private const val EXTRA_AUTHOR_TOKEN = "extra_helper_author_token"
        private const val COLUMNS = 3
        private const val PERCENT = 100
        private const val MILLIS = 1000L
        private const val LOCKED_ALPHA = 0.45f

        fun intent(context: Context, authorToken: String): Intent =
            Intent(context, HelperProfileActivity::class.java).putExtra(EXTRA_AUTHOR_TOKEN, authorToken)

        /** The signed-in member's own profile, including their discount. */
        fun intentForMe(context: Context): Intent = Intent(context, HelperProfileActivity::class.java)
    }
}
