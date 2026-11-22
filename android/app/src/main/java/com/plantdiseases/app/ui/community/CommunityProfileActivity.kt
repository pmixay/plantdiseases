package com.plantdiseases.app.ui.community

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.text.format.DateUtils
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.account.QuickSignIn
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.data.model.CommunityPublicProfile
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityCommunityProfileBinding
import com.plantdiseases.app.ui.community.forum.ForumInsets
import com.plantdiseases.app.ui.result.CommunityScanSheet
import com.plantdiseases.app.util.Avatars
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/** Public account surface for community activity.
 *
 * Only an opaque community author token reaches this screen. Provider details
 * and the account service's internal actor id never enter the public API.
 */
@AndroidEntryPoint
class CommunityProfileActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCommunityProfileBinding
    private lateinit var adapter: NearbyScansAdapter
    private var ownProfile = false

    // Guests sign in in place; their likes and comments unlock on return.
    private val quickSignIn = QuickSignIn.of(this) { adapter.guest = false }

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var authRepository: AuthRepository

    private val authorToken: String by lazy {
        intent.getStringExtra(EXTRA_AUTHOR_TOKEN).orEmpty()
    }
    private val isRu: Boolean get() = LocaleHelper.isRussian(this)

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (authorToken.isBlank()) {
            finish()
            return
        }
        binding = ActivityCommunityProfileBinding.inflate(layoutInflater)
        setContentView(binding.root)
        ForumInsets.apply(this, binding.appBar, binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        adapter = NearbyScansAdapter(
            onOpen = { item -> openPost(item) },
            onAuthor = { item ->
                item.authorToken
                    ?.takeIf { it.isNotBlank() && it != authorToken }
                    ?.let { startActivity(intent(this, it)) }
            },
            onLike = { item -> toggleLike(item) },
        )
        adapter.guest = !quickSignIn.isSignedIn
        binding.rvPosts.layoutManager = LinearLayoutManager(this)
        binding.rvPosts.adapter = adapter
        supportFragmentManager.setFragmentResultListener(
            CommunityScanSheet.CONTENT_BLOCKED,
            this,
        ) { _, _ -> load() }
        load()
    }

    private fun load() {
        binding.progress.visibility = View.VISIBLE
        binding.tvEmpty.visibility = View.GONE
        lifecycleScope.launch {
            scanRepository.getCommunityProfile(authorToken)
                .onSuccess(::render)
                .onFailure {
                    binding.progress.visibility = View.GONE
                    binding.rvPosts.visibility = View.GONE
                    binding.tvPostsTitle.visibility = View.GONE
                    binding.tvEmpty.visibility = View.VISIBLE
                    binding.tvEmpty.setText(R.string.community_profile_unavailable)
                }
        }
    }

    private fun render(profile: CommunityPublicProfile) {
        val nickname = profile.nickname
            ?.takeIf { it.isNotBlank() }
            ?: getString(R.string.community_anonymous_author)
        binding.tvNickname.text = nickname
        binding.tvAvatar.text = Avatars.monogramForNick(nickname)
        binding.tvAvatar.backgroundTintList = ColorStateList.valueOf(Avatars.colorFor(nickname))
        binding.tvJoined.text = if (profile.joinedAt > 0L) {
            getString(
                R.string.community_profile_joined,
                DateUtils.formatDateTime(
                    this,
                    profile.joinedAt * 1000L,
                    DateUtils.FORMAT_SHOW_DATE or DateUtils.FORMAT_SHOW_YEAR,
                ),
            )
        } else {
            ""
        }
        binding.tvScans.text = getString(R.string.community_profile_scans, profile.scanCount)
        binding.tvLikes.text = getString(R.string.community_profile_likes, profile.likeCount)
        binding.progress.visibility = View.GONE
        binding.tvPostsTitle.visibility = if (profile.items.isEmpty()) View.GONE else View.VISIBLE
        binding.rvPosts.visibility = if (profile.items.isEmpty()) View.GONE else View.VISIBLE
        binding.tvEmpty.visibility = if (profile.items.isEmpty()) View.VISIBLE else View.GONE
        if (profile.items.isEmpty()) {
            binding.tvEmpty.setText(R.string.community_profile_no_posts)
        }
        ownProfile = profile.isMe
        adapter.submit(profile.items)
    }

    private fun openPost(item: CommunityScanItem) {
        lifecycleScope.launch { scanRepository.recordCommunityView(item.scanId) }
        CommunityScanSheet.show(
            supportFragmentManager,
            item,
            DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
            mine = ownProfile,
        )
    }

    private fun toggleLike(item: CommunityScanItem) {
        if (!quickSignIn.require()) return
        val next = !item.likedByMe
        val optimisticCount = (item.likeCount + if (next) 1 else -1).coerceAtLeast(0)
        adapter.updateLike(item.scanId, optimisticCount, next)
        lifecycleScope.launch {
            scanRepository.setCommunityLike(item.scanId, next)
                .onSuccess { adapter.updateLike(item.scanId, it.count, it.liked) }
                .onFailure {
                    adapter.updateLike(item.scanId, item.likeCount, item.likedByMe)
                }
        }
    }

    companion object {
        private const val EXTRA_AUTHOR_TOKEN = "extra_author_token"

        fun intent(context: Context, authorToken: String): Intent =
            Intent(context, CommunityProfileActivity::class.java)
                .putExtra(EXTRA_AUTHOR_TOKEN, authorToken)
    }
}
