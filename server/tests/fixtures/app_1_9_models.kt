// Verbatim from app 1.9's data/model/Models.kt (identical at c89dd2d and
// efc3096, the two commits 1.9 may have been built from). Read by
// tests/test_legacy_app_routes.py; delete with server/routes_legacy_app.py.

@Serializable
data class MyScanOpinionsResponse(
    @SerializedName("scans") @SerialName("scans") val scans: List<ScanOpinionItem> = emptyList(),
    @SerializedName("total_opinions") @SerialName("total_opinions") val totalOpinions: Int = 0,
    @SerializedName("total_views") @SerialName("total_views") val totalViews: Int = 0,
)

@Serializable
data class ScanOpinionItem(
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String = "",
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("opinion_count") @SerialName("opinion_count") val opinionCount: Int = 0,
    @SerializedName("latest_at") @SerialName("latest_at") val latestAt: Long = 0,
    @SerializedName("view_count") @SerialName("view_count") val viewCount: Int = 0,
    @SerializedName("latest_nickname") @SerialName("latest_nickname") val latestNickname: String? = null,
)

@Serializable
data class CommunityTopDisease(
    @SerializedName("disease") @SerialName("disease") val disease: String = "",
    @SerializedName("count") @SerialName("count") val count: Int = 0,
)

@Serializable
data class CommunityOpinion(
    @SerializedName("suggested_disease") @SerialName("suggested_disease") val suggestedDisease: String = "",
    @SerializedName("note") @SerialName("note") val note: String = "",
    @SerializedName("created_at") @SerialName("created_at") val createdAt: Long = 0L,
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String = "",
    @SerializedName("nickname") @SerialName("nickname") val nickname: String? = null,
    // Opaque public account handle used only to open the author's community
    // profile. Null for unlinked legacy comments.
    @SerializedName("author_token") @SerialName("author_token") val authorToken: String? = null,
    // Opaque public token for reporting/blocking this reply. It cannot be used
    // to recover the database row id or account actor id.
    @SerializedName("content_id") @SerialName("content_id") val contentId: String? = null,
    // Viewer-relative server flag; lets the UI avoid offering safety actions
    // against the caller's own reply without exposing any account identifier.
    @SerializedName("is_mine") @SerialName("is_mine") val isMine: Boolean = false,
)

@Serializable
data class CommunityOpinionsResponse(
    @SerializedName("scan_id") @SerialName("scan_id") val scanId: String = "",
    @SerializedName("opinions") @SerialName("opinions") val opinions: List<CommunityOpinion> = emptyList(),
    @SerializedName("consensus") @SerialName("consensus") val consensus: List<CommunityTopDisease> = emptyList(),
    // The signed-in account's most recent pick on this scan, resolved from
    // the bearer principal. Null for anonymous reads or no prior opinion.
    @SerializedName("my_opinion") @SerialName("my_opinion") val myOpinion: String? = null,
    // Distinct-viewer count on this scan (per-user deduped server-side).
    @SerializedName("views") @SerialName("views") val views: Int = 0,
    // Whether the owner has a LIVE second-opinion request on this scan
    // (requests auto-expire server-side after 72 h). Lets the owner's app
    // restore its "waiting" state across restarts.
    @SerializedName("wants_opinion") @SerialName("wants_opinion") val wantsOpinion: Boolean = false,
    // Epoch seconds of that live request (null when none) — drives the
    // "N h left" countdown toward the 72 h expiry.
    @SerializedName("opinion_requested_at") @SerialName("opinion_requested_at") val opinionRequestedAt: Long? = null,
    @SerializedName("ok") @SerialName("ok") val ok: Boolean = false,
    @SerializedName("count") @SerialName("count") val count: Int = 0,
    @SerializedName("like_count") @SerialName("like_count") val likeCount: Int = 0,
    @SerializedName("liked_by_me") @SerialName("liked_by_me") val likedByMe: Boolean = false,
)

@Serializable
data class CommunityCancelOpinionResponse(
    @SerializedName("ok") @SerialName("ok") val ok: Boolean = false,
    @SerializedName("cleared") @SerialName("cleared") val cleared: Boolean = false,
)

@Serializable
data class CommunityScanCount(
    @SerializedName("opinions") @SerialName("opinions") val opinions: Int = 0,
    @SerializedName("views") @SerialName("views") val views: Int = 0,
)

@Serializable
data class CommunityScanCountsResponse(
    @SerializedName("counts") @SerialName("counts") val counts: Map<String, CommunityScanCount> = emptyMap(),
)

