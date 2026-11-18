package com.plantdiseases.app.data.model

import com.google.gson.annotations.SerializedName
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Wire models for the community forum (/api/community/feed, posts, answers,
// search, achievements, moderation). Like Models.kt they carry both
// @SerialName (kotlinx, the wire format) and @SerializedName (Gson, used when
// a model rides an Intent or the response cache). Request bodies declare no
// default values so every field is always encoded.

@Serializable
data class ForumAuthor(
    @SerializedName("nickname") @SerialName("nickname") val nickname: String = "",
    @SerializedName("author_token") @SerialName("author_token") val authorToken: String? = null,
    @SerializedName("is_me") @SerialName("is_me") val isMe: Boolean = false,
    @SerializedName("is_post_author") @SerialName("is_post_author") val isPostAuthor: Boolean = false,
)

@Serializable
data class ForumMedia(
    @SerializedName("media_id") @SerialName("media_id") val mediaId: String,
    @SerializedName("width") @SerialName("width") val width: Int = 0,
    @SerializedName("height") @SerialName("height") val height: Int = 0,
)

@Serializable
data class ForumAiVerdict(
    @SerializedName("disease") @SerialName("disease") val disease: String,
    @SerializedName("confidence") @SerialName("confidence") val confidence: Double? = null,
    @SerializedName("species") @SerialName("species") val species: String? = null,
)

@Serializable
data class ForumRecoveryUpdate(
    @SerializedName("id") @SerialName("id") val id: String,
    @SerializedName("title") @SerialName("title") val title: String = "",
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0,
)

@Serializable
data class ForumAnswer(
    @SerializedName("id") @SerialName("id") val id: String,
    @SerializedName("parent_id") @SerialName("parent_id") val parentId: String? = null,
    @SerializedName("depth") @SerialName("depth") val depth: Int = 0,
    @SerializedName("is_ai") @SerialName("is_ai") val isAi: Boolean = false,
    @SerializedName("author") @SerialName("author") val author: ForumAuthor? = null,
    @SerializedName("body") @SerialName("body") val body: String = "",
    @SerializedName("steps") @SerialName("steps") val steps: List<String> = emptyList(),
    @SerializedName("diagnosis") @SerialName("diagnosis") val diagnosis: String? = null,
    @SerializedName("state") @SerialName("state") val state: String = "visible",
    @SerializedName("like_count") @SerialName("like_count") val likeCount: Int = 0,
    @SerializedName("not_helpful_count") @SerialName("not_helpful_count") val notHelpfulCount: Int = 0,
    @SerializedName("my_reaction") @SerialName("my_reaction") val myReaction: String? = null,
    @SerializedName("accepted") @SerialName("accepted") val accepted: Boolean = false,
    @SerializedName("verified") @SerialName("verified") val verified: Boolean = false,
    @SerializedName("media") @SerialName("media") val media: List<ForumMedia> = emptyList(),
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0,
    @SerializedName("edited_at") @SerialName("edited_at") val editedAt: Long? = null,
) {
    val isRemoved: Boolean get() = state != "visible"
}

@Serializable
data class ForumPost(
    @SerializedName("id") @SerialName("id") val id: String,
    @SerializedName("kind") @SerialName("kind") val kind: String = "question",
    @SerializedName("space") @SerialName("space") val space: String = "general",
    @SerializedName("title") @SerialName("title") val title: String = "",
    @SerializedName("body") @SerialName("body") val body: String = "",
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("condition") @SerialName("condition") val condition: String? = null,
    @SerializedName("urgent") @SerialName("urgent") val urgent: Boolean = false,
    @SerializedName("author") @SerialName("author") val author: ForumAuthor = ForumAuthor(),
    @SerializedName("ai") @SerialName("ai") val ai: ForumAiVerdict? = null,
    @SerializedName("media") @SerialName("media") val media: List<ForumMedia> = emptyList(),
    @SerializedName("media_count") @SerialName("media_count") val mediaCount: Int = 0,
    @SerializedName("like_count") @SerialName("like_count") val likeCount: Int = 0,
    @SerializedName("answer_count") @SerialName("answer_count") val answerCount: Int = 0,
    @SerializedName("liked_by_me") @SerialName("liked_by_me") val likedByMe: Boolean = false,
    @SerializedName("solved") @SerialName("solved") val solved: Boolean = false,
    @SerializedName("accepted_answer_id") @SerialName("accepted_answer_id") val acceptedAnswerId: String? = null,
    @SerializedName("accepted_excerpt") @SerialName("accepted_excerpt") val acceptedExcerpt: String? = null,
    @SerializedName("parent_post_id") @SerialName("parent_post_id") val parentPostId: String? = null,
    @SerializedName("locked") @SerialName("locked") val locked: Boolean = false,
    @SerializedName("pinned") @SerialName("pinned") val pinned: Boolean = false,
    @SerializedName("state") @SerialName("state") val state: String = "visible",
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0,
    @SerializedName("edited_at") @SerialName("edited_at") val editedAt: Long? = null,
    @SerializedName("last_activity_at") @SerialName("last_activity_at") val lastActivityAt: Long = 0,
    @SerializedName("training_consent") @SerialName("training_consent") val trainingConsent: Boolean? = null,
    @SerializedName("recovery_updates") @SerialName("recovery_updates")
    val recoveryUpdates: List<ForumRecoveryUpdate> = emptyList(),
    @SerializedName("answers") @SerialName("answers") val answers: List<ForumAnswer> = emptyList(),
)

@Serializable
data class ForumPage(
    @SerializedName("items") @SerialName("items") val items: List<ForumPost> = emptyList(),
    @SerializedName("next_cursor") @SerialName("next_cursor") val nextCursor: String? = null,
    @SerializedName("terms") @SerialName("terms") val terms: List<String> = emptyList(),
)

@Serializable
data class ForumSpace(
    @SerializedName("slug") @SerialName("slug") val slug: String,
    @SerializedName("name_en") @SerialName("name_en") val nameEn: String = "",
    @SerializedName("name_ru") @SerialName("name_ru") val nameRu: String = "",
    @SerializedName("icon") @SerialName("icon") val icon: String = "leaf",
    @SerializedName("kind") @SerialName("kind") val kind: String = "plant",
    @SerializedName("post_count") @SerialName("post_count") val postCount: Int = 0,
)

@Serializable
data class ForumSpacesResponse(
    @SerializedName("items") @SerialName("items") val items: List<ForumSpace> = emptyList(),
)

@Serializable
data class ForumMediaUpload(
    @SerializedName("media_id") @SerialName("media_id") val mediaId: String,
    @SerializedName("width") @SerialName("width") val width: Int = 0,
    @SerializedName("height") @SerialName("height") val height: Int = 0,
)

@Serializable
data class ForumPostCreateRequest(
    @SerializedName("kind") @SerialName("kind") val kind: String,
    @SerializedName("title") @SerialName("title") val title: String,
    @SerializedName("body") @SerialName("body") val body: String,
    @SerializedName("space") @SerialName("space") val space: String?,
    @SerializedName("species") @SerialName("species") val species: String?,
    @SerializedName("condition") @SerialName("condition") val condition: String?,
    @SerializedName("urgent") @SerialName("urgent") val urgent: Boolean,
    @SerializedName("media_ids") @SerialName("media_ids") val mediaIds: List<String>,
    @SerializedName("scan_token") @SerialName("scan_token") val scanToken: String?,
    @SerializedName("parent_post_id") @SerialName("parent_post_id") val parentPostId: String?,
    @SerializedName("training_consent") @SerialName("training_consent") val trainingConsent: Boolean,
    @SerializedName("locale") @SerialName("locale") val locale: String?,
)

/** Author edit of a post's text and tags; null leaves a field unchanged. */
@Serializable
data class ForumPostEditRequest(
    @SerializedName("title") @SerialName("title") val title: String? = null,
    @SerializedName("body") @SerialName("body") val body: String? = null,
    @SerializedName("space") @SerialName("space") val space: String? = null,
    @SerializedName("species") @SerialName("species") val species: String? = null,
    @SerializedName("condition") @SerialName("condition") val condition: String? = null,
    @SerializedName("urgent") @SerialName("urgent") val urgent: Boolean? = null,
    @SerializedName("training_consent") @SerialName("training_consent") val trainingConsent: Boolean? = null,
)

@Serializable
data class ForumAnswerCreateRequest(
    @SerializedName("body") @SerialName("body") val body: String,
    @SerializedName("parent_id") @SerialName("parent_id") val parentId: String?,
    @SerializedName("steps") @SerialName("steps") val steps: List<String>,
    @SerializedName("diagnosis") @SerialName("diagnosis") val diagnosis: String?,
    @SerializedName("media_ids") @SerialName("media_ids") val mediaIds: List<String>,
    @SerializedName("training_consent") @SerialName("training_consent") val trainingConsent: Boolean,
)

@Serializable
data class ForumLikeRequest(
    @SerializedName("liked") @SerialName("liked") val liked: Boolean,
)

@Serializable
data class ForumLikeResponse(
    @SerializedName("liked") @SerialName("liked") val liked: Boolean = false,
    @SerializedName("like_count") @SerialName("like_count") val likeCount: Int = 0,
)

@Serializable
data class ForumReactionRequest(
    @SerializedName("reaction") @SerialName("reaction") val reaction: String?,
)

@Serializable
data class ForumReactionResponse(
    @SerializedName("reaction") @SerialName("reaction") val reaction: String? = null,
    @SerializedName("like_count") @SerialName("like_count") val likeCount: Int = 0,
    @SerializedName("not_helpful_count") @SerialName("not_helpful_count") val notHelpfulCount: Int = 0,
)

@Serializable
data class ForumAcceptRequest(
    @SerializedName("answer_id") @SerialName("answer_id") val answerId: String?,
)

@Serializable
data class ForumReportRequest(
    @SerializedName("target_type") @SerialName("target_type") val targetType: String,
    @SerializedName("target_id") @SerialName("target_id") val targetId: String,
    @SerializedName("reason") @SerialName("reason") val reason: String,
)

@Serializable
data class ForumBlockRequest(
    @SerializedName("target_type") @SerialName("target_type") val targetType: String,
    @SerializedName("target_id") @SerialName("target_id") val targetId: String,
)

@Serializable
data class ForumActivityItem(
    @SerializedName("type") @SerialName("type") val type: String,
    @SerializedName("post_id") @SerialName("post_id") val postId: String,
    @SerializedName("post_title") @SerialName("post_title") val postTitle: String? = null,
    @SerializedName("post_kind") @SerialName("post_kind") val postKind: String? = null,
    @SerializedName("answer_id") @SerialName("answer_id") val answerId: String? = null,
    @SerializedName("nickname") @SerialName("nickname") val nickname: String = "",
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0,
)

@Serializable
data class ForumActivityResponse(
    @SerializedName("items") @SerialName("items") val items: List<ForumActivityItem> = emptyList(),
    @SerializedName("unread") @SerialName("unread") val unread: Int = 0,
)

// ── achievements ─────────────────────────────────────────────────────────

@Serializable
data class AchievementTier(
    @SerializedName("tier") @SerialName("tier") val tier: Int,
    @SerializedName("threshold") @SerialName("threshold") val threshold: Int,
    @SerializedName("awarded_at") @SerialName("awarded_at") val awardedAt: Long? = null,
)

@Serializable
data class AchievementProgress(
    @SerializedName("key") @SerialName("key") val key: String,
    @SerializedName("group") @SerialName("group") val group: String = "",
    @SerializedName("value") @SerialName("value") val value: Int = 0,
    @SerializedName("tiers") @SerialName("tiers") val tiers: List<AchievementTier> = emptyList(),
    @SerializedName("earned_tiers") @SerialName("earned_tiers") val earnedTiers: Int = 0,
)

@Serializable
data class HelperStats(
    @SerializedName("accepted_answers") @SerialName("accepted_answers") val acceptedAnswers: Int = 0,
    @SerializedName("plants_saved") @SerialName("plants_saved") val plantsSaved: Int = 0,
    @SerializedName("labels_taught") @SerialName("labels_taught") val labelsTaught: Int = 0,
)

@Serializable
data class DiscountState(
    @SerializedName("achievements") @SerialName("achievements") val achievements: Int = 0,
    @SerializedName("percent") @SerialName("percent") val percent: Int = 0,
    @SerializedName("per_achievement") @SerialName("per_achievement") val perAchievement: Int = 2,
    @SerializedName("cap") @SerialName("cap") val cap: Int = 40,
    @SerializedName("unlocks_at") @SerialName("unlocks_at") val unlocksAt: Long? = null,
    @SerializedName("frozen") @SerialName("frozen") val frozen: Boolean = false,
)

@Serializable
data class HelperProfile(
    @SerializedName("author_token") @SerialName("author_token") val authorToken: String? = null,
    @SerializedName("nickname") @SerialName("nickname") val nickname: String = "",
    @SerializedName("level") @SerialName("level") val level: Int = 1,
    @SerializedName("level_name") @SerialName("level_name") val levelName: String = "seedling",
    @SerializedName("leaves") @SerialName("leaves") val leaves: Int = 0,
    @SerializedName("next_level_leaves") @SerialName("next_level_leaves") val nextLevelLeaves: Int? = null,
    @SerializedName("stats") @SerialName("stats") val stats: HelperStats = HelperStats(),
    @SerializedName("earned") @SerialName("earned") val earned: Int = 0,
    @SerializedName("total") @SerialName("total") val total: Int = 30,
    @SerializedName("achievements") @SerialName("achievements")
    val achievements: List<AchievementProgress> = emptyList(),
    @SerializedName("discount") @SerialName("discount") val discount: DiscountState? = null,
    @SerializedName("can_mark_not_helpful") @SerialName("can_mark_not_helpful")
    val canMarkNotHelpful: Boolean? = null,
)

// ── moderation ───────────────────────────────────────────────────────────

@Serializable
data class StaffRole(
    @SerializedName("role") @SerialName("role") val role: String,
    @SerializedName("scope") @SerialName("scope") val scope: String = "*",
)

@Serializable
data class StaffMe(
    @SerializedName("available") @SerialName("available") val available: Boolean = false,
    @SerializedName("roles") @SerialName("roles") val roles: List<StaffRole> = emptyList(),
) {
    val isAdmin: Boolean get() = roles.any { it.role == "admin" }
    val isModerator: Boolean get() = isAdmin || roles.any { it.role == "moderator" }
    val isExpert: Boolean get() = isAdmin || roles.any { it.role == "expert" }
    val isStaff: Boolean get() = roles.isNotEmpty()
}

@Serializable
data class ModQueueItem(
    @SerializedName("target_type") @SerialName("target_type") val targetType: String? = null,
    @SerializedName("target_id") @SerialName("target_id") val targetId: String? = null,
    @SerializedName("post_id") @SerialName("post_id") val postId: String? = null,
    @SerializedName("report_count") @SerialName("report_count") val reportCount: Int = 0,
    @SerializedName("reasons") @SerialName("reasons") val reasons: List<String> = emptyList(),
    @SerializedName("excerpt") @SerialName("excerpt") val excerpt: String? = null,
    @SerializedName("state") @SerialName("state") val state: String? = null,
    @SerializedName("space") @SerialName("space") val space: String? = null,
    @SerializedName("author") @SerialName("author") val author: ForumAuthor? = null,
    @SerializedName("appeal_id") @SerialName("appeal_id") val appealId: String? = null,
    @SerializedName("text") @SerialName("text") val text: String? = null,
    @SerializedName("action") @SerialName("action") val action: String? = null,
    @SerializedName("reason_code") @SerialName("reason_code") val reasonCode: String? = null,
    @SerializedName("title") @SerialName("title") val title: String? = null,
    @SerializedName("ai_disease") @SerialName("ai_disease") val aiDisease: String? = null,
    @SerializedName("ai_confidence") @SerialName("ai_confidence") val aiConfidence: Double? = null,
    @SerializedName("community_label") @SerialName("community_label") val communityLabel: String? = null,
    @SerializedName("ai_not_helpful") @SerialName("ai_not_helpful") val aiNotHelpful: Int = 0,
    @SerializedName("nickname") @SerialName("nickname") val nickname: String? = null,
    @SerializedName("author_token") @SerialName("author_token") val authorToken: String? = null,
    @SerializedName("reason") @SerialName("reason") val reason: String? = null,
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long? = null,
)

@Serializable
data class ModQueue(
    @SerializedName("kind") @SerialName("kind") val kind: String = "",
    @SerializedName("items") @SerialName("items") val items: List<ModQueueItem> = emptyList(),
)

@Serializable
data class ModPostActionRequest(
    @SerializedName("action") @SerialName("action") val action: String,
    @SerializedName("reason_code") @SerialName("reason_code") val reasonCode: String?,
    @SerializedName("note") @SerialName("note") val note: String?,
    @SerializedName("space") @SerialName("space") val space: String?,
)

@Serializable
data class ModAnswerActionRequest(
    @SerializedName("action") @SerialName("action") val action: String,
    @SerializedName("reason_code") @SerialName("reason_code") val reasonCode: String?,
    @SerializedName("note") @SerialName("note") val note: String?,
)

@Serializable
data class ModSanctionRequest(
    @SerializedName("kind") @SerialName("kind") val kind: String,
    @SerializedName("reason_code") @SerialName("reason_code") val reasonCode: String?,
    @SerializedName("hours") @SerialName("hours") val hours: Int?,
    @SerializedName("note") @SerialName("note") val note: String?,
)

@Serializable
data class ModDismissRequest(
    @SerializedName("target_type") @SerialName("target_type") val targetType: String,
    @SerializedName("target_id") @SerialName("target_id") val targetId: String,
)

@Serializable
data class ModVerifyRequest(
    @SerializedName("verified") @SerialName("verified") val verified: Boolean,
)

@Serializable
data class ModRewardFreezeRequest(
    @SerializedName("frozen") @SerialName("frozen") val frozen: Boolean,
)

@Serializable
data class ModAppealDecisionRequest(
    @SerializedName("decision") @SerialName("decision") val decision: String,
)

@Serializable
data class ModActionResult(
    @SerializedName("action_id") @SerialName("action_id") val actionId: String? = null,
    @SerializedName("action") @SerialName("action") val action: String? = null,
)

@Serializable
data class ModNotice(
    @SerializedName("action_id") @SerialName("action_id") val actionId: String,
    @SerializedName("action") @SerialName("action") val action: String,
    @SerializedName("target_type") @SerialName("target_type") val targetType: String = "",
    @SerializedName("reason_code") @SerialName("reason_code") val reasonCode: String? = null,
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0,
    @SerializedName("post_id") @SerialName("post_id") val postId: String? = null,
    @SerializedName("post_title") @SerialName("post_title") val postTitle: String? = null,
    @SerializedName("automatic") @SerialName("automatic") val automatic: Boolean = false,
    @SerializedName("appeal_state") @SerialName("appeal_state") val appealState: String? = null,
    @SerializedName("can_appeal") @SerialName("can_appeal") val canAppeal: Boolean = false,
)

@Serializable
data class ActiveSanction(
    @SerializedName("kind") @SerialName("kind") val kind: String,
    @SerializedName("until") @SerialName("until") val until: Long? = null,
)

@Serializable
data class ModNotices(
    @SerializedName("active_sanction") @SerialName("active_sanction") val activeSanction: ActiveSanction? = null,
    @SerializedName("items") @SerialName("items") val items: List<ModNotice> = emptyList(),
)

@Serializable
data class ModAppealRequest(
    @SerializedName("action_id") @SerialName("action_id") val actionId: String,
    @SerializedName("text") @SerialName("text") val text: String,
)
