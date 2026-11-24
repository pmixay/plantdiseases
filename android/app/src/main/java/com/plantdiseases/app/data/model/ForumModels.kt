package com.plantdiseases.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Wire models for the community forum (/api/community/feed, posts, answers,
// search, achievements, moderation), decoded with kotlinx.serialization like
// Models.kt. Request bodies declare no default values so every field is
// always encoded.

@Serializable
data class ForumAuthor(
    @SerialName("nickname") val nickname: String = "",
    @SerialName("author_token") val authorToken: String? = null,
    @SerialName("is_me") val isMe: Boolean = false,
    @SerialName("is_post_author") val isPostAuthor: Boolean = false,
    /** Set when the member shows a profile photo; changes whenever the photo does. */
    @SerialName("avatar_version") val avatarVersion: Long? = null,
)

@Serializable
data class ForumMedia(
    @SerialName("media_id") val mediaId: String,
    @SerialName("width") val width: Int = 0,
    @SerialName("height") val height: Int = 0,
)

@Serializable
data class ForumAiVerdict(
    @SerialName("disease") val disease: String,
    @SerialName("confidence") val confidence: Double? = null,
    @SerialName("species") val species: String? = null,
)

@Serializable
data class ForumRecoveryUpdate(
    @SerialName("id") val id: String,
    @SerialName("title") val title: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
)

@Serializable
data class ForumAnswer(
    @SerialName("id") val id: String,
    @SerialName("parent_id") val parentId: String? = null,
    @SerialName("depth") val depth: Int = 0,
    @SerialName("is_ai") val isAi: Boolean = false,
    @SerialName("author") val author: ForumAuthor? = null,
    @SerialName("body") val body: String = "",
    @SerialName("steps") val steps: List<String> = emptyList(),
    @SerialName("diagnosis") val diagnosis: String? = null,
    @SerialName("state") val state: String = "visible",
    @SerialName("like_count") val likeCount: Int = 0,
    @SerialName("not_helpful_count") val notHelpfulCount: Int = 0,
    @SerialName("my_reaction") val myReaction: String? = null,
    @SerialName("accepted") val accepted: Boolean = false,
    @SerialName("verified") val verified: Boolean = false,
    @SerialName("media") val media: List<ForumMedia> = emptyList(),
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("edited_at") val editedAt: Long? = null,
) {
    val isRemoved: Boolean get() = state != "visible"
}

@Serializable
data class ForumPost(
    @SerialName("id") val id: String,
    @SerialName("kind") val kind: String = "question",
    @SerialName("space") val space: String = "general",
    @SerialName("title") val title: String = "",
    @SerialName("body") val body: String = "",
    @SerialName("species") val species: String? = null,
    @SerialName("condition") val condition: String? = null,
    @SerialName("urgent") val urgent: Boolean = false,
    @SerialName("author") val author: ForumAuthor = ForumAuthor(),
    @SerialName("ai") val ai: ForumAiVerdict? = null,
    @SerialName("media") val media: List<ForumMedia> = emptyList(),
    @SerialName("media_count") val mediaCount: Int = 0,
    @SerialName("like_count") val likeCount: Int = 0,
    @SerialName("answer_count") val answerCount: Int = 0,
    @SerialName("liked_by_me") val likedByMe: Boolean = false,
    @SerialName("solved") val solved: Boolean = false,
    @SerialName("accepted_answer_id") val acceptedAnswerId: String? = null,
    @SerialName("accepted_excerpt") val acceptedExcerpt: String? = null,
    @SerialName("parent_post_id") val parentPostId: String? = null,
    @SerialName("locked") val locked: Boolean = false,
    @SerialName("pinned") val pinned: Boolean = false,
    @SerialName("state") val state: String = "visible",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("edited_at") val editedAt: Long? = null,
    @SerialName("last_activity_at") val lastActivityAt: Long = 0,
    @SerialName("training_consent") val trainingConsent: Boolean? = null,
    @SerialName("recovery_updates")
    val recoveryUpdates: List<ForumRecoveryUpdate> = emptyList(),
    @SerialName("answers") val answers: List<ForumAnswer> = emptyList(),
)

@Serializable
data class ForumPage(
    @SerialName("items") val items: List<ForumPost> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String? = null,
    @SerialName("terms") val terms: List<String> = emptyList(),
)

@Serializable
data class ForumSpace(
    @SerialName("slug") val slug: String,
    @SerialName("name_en") val nameEn: String = "",
    @SerialName("name_ru") val nameRu: String = "",
    @SerialName("icon") val icon: String = "leaf",
    @SerialName("kind") val kind: String = "plant",
    @SerialName("post_count") val postCount: Int = 0,
)

@Serializable
data class ForumSpacesResponse(
    @SerialName("items") val items: List<ForumSpace> = emptyList(),
)

@Serializable
data class ForumMediaUpload(
    @SerialName("media_id") val mediaId: String,
    @SerialName("width") val width: Int = 0,
    @SerialName("height") val height: Int = 0,
)

/** Reply to `PUT /api/community/me/avatar`. */
@Serializable
data class ForumAvatarUpload(
    @SerialName("author_token") val authorToken: String? = null,
    @SerialName("avatar_version") val avatarVersion: Long? = null,
)

@Serializable
data class ForumPostCreateRequest(
    @SerialName("kind") val kind: String,
    @SerialName("title") val title: String,
    @SerialName("body") val body: String,
    @SerialName("space") val space: String?,
    @SerialName("species") val species: String?,
    @SerialName("condition") val condition: String?,
    @SerialName("urgent") val urgent: Boolean,
    @SerialName("media_ids") val mediaIds: List<String>,
    @SerialName("scan_token") val scanToken: String?,
    @SerialName("parent_post_id") val parentPostId: String?,
    @SerialName("training_consent") val trainingConsent: Boolean,
    @SerialName("locale") val locale: String?,
)

/** Author edit of a post's text and tags; null leaves a field unchanged. */
@Serializable
data class ForumPostEditRequest(
    @SerialName("title") val title: String? = null,
    @SerialName("body") val body: String? = null,
    @SerialName("space") val space: String? = null,
    @SerialName("species") val species: String? = null,
    @SerialName("condition") val condition: String? = null,
    @SerialName("urgent") val urgent: Boolean? = null,
    @SerialName("training_consent") val trainingConsent: Boolean? = null,
)

@Serializable
data class ForumAnswerCreateRequest(
    @SerialName("body") val body: String,
    @SerialName("parent_id") val parentId: String?,
    @SerialName("steps") val steps: List<String>,
    @SerialName("diagnosis") val diagnosis: String?,
    @SerialName("media_ids") val mediaIds: List<String>,
    @SerialName("training_consent") val trainingConsent: Boolean,
)

@Serializable
data class ForumLikeRequest(
    @SerialName("liked") val liked: Boolean,
)

@Serializable
data class ForumLikeResponse(
    @SerialName("liked") val liked: Boolean = false,
    @SerialName("like_count") val likeCount: Int = 0,
)

@Serializable
data class ForumReactionRequest(
    @SerialName("reaction") val reaction: String?,
)

@Serializable
data class ForumReactionResponse(
    @SerialName("reaction") val reaction: String? = null,
    @SerialName("like_count") val likeCount: Int = 0,
    @SerialName("not_helpful_count") val notHelpfulCount: Int = 0,
)

@Serializable
data class ForumAcceptRequest(
    @SerialName("answer_id") val answerId: String?,
)

@Serializable
data class ForumReportRequest(
    @SerialName("target_type") val targetType: String,
    @SerialName("target_id") val targetId: String,
    @SerialName("reason") val reason: String,
)

@Serializable
data class ForumBlockRequest(
    @SerialName("target_type") val targetType: String,
    @SerialName("target_id") val targetId: String,
)

@Serializable
data class ForumActivityItem(
    @SerialName("type") val type: String,
    @SerialName("post_id") val postId: String,
    @SerialName("post_title") val postTitle: String? = null,
    @SerialName("post_kind") val postKind: String? = null,
    @SerialName("answer_id") val answerId: String? = null,
    @SerialName("nickname") val nickname: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
)

@Serializable
data class ForumActivityResponse(
    @SerialName("items") val items: List<ForumActivityItem> = emptyList(),
    @SerialName("unread") val unread: Int = 0,
)

// ── achievements ─────────────────────────────────────────────────────────

@Serializable
data class AchievementTier(
    @SerialName("tier") val tier: Int,
    @SerialName("threshold") val threshold: Int,
    @SerialName("awarded_at") val awardedAt: Long? = null,
)

@Serializable
data class AchievementProgress(
    @SerialName("key") val key: String,
    @SerialName("group") val group: String = "",
    @SerialName("value") val value: Int = 0,
    @SerialName("tiers") val tiers: List<AchievementTier> = emptyList(),
    @SerialName("earned_tiers") val earnedTiers: Int = 0,
)

@Serializable
data class HelperStats(
    @SerialName("accepted_answers") val acceptedAnswers: Int = 0,
    @SerialName("plants_saved") val plantsSaved: Int = 0,
    @SerialName("labels_taught") val labelsTaught: Int = 0,
)

@Serializable
data class DiscountState(
    @SerialName("achievements") val achievements: Int = 0,
    @SerialName("percent") val percent: Int = 0,
    @SerialName("per_achievement") val perAchievement: Int = 2,
    @SerialName("cap") val cap: Int = 40,
    @SerialName("unlocks_at") val unlocksAt: Long? = null,
    @SerialName("frozen") val frozen: Boolean = false,
)

@Serializable
data class HelperProfile(
    @SerialName("author_token") val authorToken: String? = null,
    @SerialName("nickname") val nickname: String = "",
    @SerialName("avatar_version") val avatarVersion: Long? = null,
    @SerialName("level") val level: Int = 1,
    @SerialName("level_name") val levelName: String = "seedling",
    @SerialName("leaves") val leaves: Int = 0,
    @SerialName("next_level_leaves") val nextLevelLeaves: Int? = null,
    @SerialName("stats") val stats: HelperStats = HelperStats(),
    @SerialName("earned") val earned: Int = 0,
    @SerialName("total") val total: Int = 30,
    @SerialName("achievements")
    val achievements: List<AchievementProgress> = emptyList(),
    @SerialName("discount") val discount: DiscountState? = null,
    @SerialName("can_mark_not_helpful")
    val canMarkNotHelpful: Boolean? = null,
)

// ── moderation ───────────────────────────────────────────────────────────

@Serializable
data class StaffRole(
    @SerialName("role") val role: String,
    @SerialName("scope") val scope: String = "*",
)

@Serializable
data class StaffMe(
    @SerialName("available") val available: Boolean = false,
    @SerialName("roles") val roles: List<StaffRole> = emptyList(),
) {
    val isAdmin: Boolean get() = roles.any { it.role == "admin" }
    val isModerator: Boolean get() = isAdmin || roles.any { it.role == "moderator" }
    val isExpert: Boolean get() = isAdmin || roles.any { it.role == "expert" }
    val isStaff: Boolean get() = roles.isNotEmpty()
}

@Serializable
data class ModQueueItem(
    @SerialName("target_type") val targetType: String? = null,
    @SerialName("target_id") val targetId: String? = null,
    @SerialName("post_id") val postId: String? = null,
    @SerialName("report_count") val reportCount: Int = 0,
    @SerialName("reasons") val reasons: List<String> = emptyList(),
    @SerialName("excerpt") val excerpt: String? = null,
    @SerialName("state") val state: String? = null,
    @SerialName("space") val space: String? = null,
    @SerialName("author") val author: ForumAuthor? = null,
    @SerialName("appeal_id") val appealId: String? = null,
    @SerialName("text") val text: String? = null,
    @SerialName("action") val action: String? = null,
    @SerialName("reason_code") val reasonCode: String? = null,
    @SerialName("title") val title: String? = null,
    @SerialName("ai_disease") val aiDisease: String? = null,
    @SerialName("ai_confidence") val aiConfidence: Double? = null,
    @SerialName("community_label") val communityLabel: String? = null,
    @SerialName("ai_not_helpful") val aiNotHelpful: Int = 0,
    @SerialName("nickname") val nickname: String? = null,
    @SerialName("author_token") val authorToken: String? = null,
    @SerialName("reason") val reason: String? = null,
    @SerialName("created_at") val createdAt: Long? = null,
)

@Serializable
data class ModQueue(
    @SerialName("kind") val kind: String = "",
    @SerialName("items") val items: List<ModQueueItem> = emptyList(),
)

@Serializable
data class ModPostActionRequest(
    @SerialName("action") val action: String,
    @SerialName("reason_code") val reasonCode: String?,
    @SerialName("note") val note: String?,
    @SerialName("space") val space: String?,
)

@Serializable
data class ModAnswerActionRequest(
    @SerialName("action") val action: String,
    @SerialName("reason_code") val reasonCode: String?,
    @SerialName("note") val note: String?,
)

@Serializable
data class ModSanctionRequest(
    @SerialName("kind") val kind: String,
    @SerialName("reason_code") val reasonCode: String?,
    @SerialName("hours") val hours: Int?,
    @SerialName("note") val note: String?,
)

@Serializable
data class ModDismissRequest(
    @SerialName("target_type") val targetType: String,
    @SerialName("target_id") val targetId: String,
)

@Serializable
data class ModVerifyRequest(
    @SerialName("verified") val verified: Boolean,
)

@Serializable
data class ModRewardFreezeRequest(
    @SerialName("frozen") val frozen: Boolean,
)

@Serializable
data class ModAppealDecisionRequest(
    @SerialName("decision") val decision: String,
)

@Serializable
data class ModActionResult(
    @SerialName("action_id") val actionId: String? = null,
    @SerialName("action") val action: String? = null,
)

@Serializable
data class ModNotice(
    @SerialName("action_id") val actionId: String,
    @SerialName("action") val action: String,
    @SerialName("target_type") val targetType: String = "",
    @SerialName("reason_code") val reasonCode: String? = null,
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("post_id") val postId: String? = null,
    @SerialName("post_title") val postTitle: String? = null,
    @SerialName("automatic") val automatic: Boolean = false,
    @SerialName("appeal_state") val appealState: String? = null,
    @SerialName("can_appeal") val canAppeal: Boolean = false,
)

@Serializable
data class ActiveSanction(
    @SerialName("kind") val kind: String,
    @SerialName("until") val until: Long? = null,
)

@Serializable
data class ModNotices(
    @SerialName("active_sanction") val activeSanction: ActiveSanction? = null,
    @SerialName("items") val items: List<ModNotice> = emptyList(),
)

@Serializable
data class ModAppealRequest(
    @SerialName("action_id") val actionId: String,
    @SerialName("text") val text: String,
)
