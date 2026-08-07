package com.plantdiseases.app.data.account

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

enum class AuthProvider(val wireName: String) {
    YANDEX("yandex"),
    VK("vk"),
}

@Serializable
data class TokenProfile(
    val nickname: String,
    @SerialName("community_actor_id") val communityActorId: String,
)

@Serializable
data class AccountSession(
    @SerialName("session_id") val sessionId: String,
    @SerialName("installation_id") val installationId: String,
    @SerialName("device_label") val deviceLabel: String = GENERIC_DEVICE_LABEL,
    @SerialName("created_at") val createdAt: String,
    @SerialName("last_used_at") val lastUsedAt: String,
    @SerialName("idle_expires_at") val idleExpiresAt: String,
    @SerialName("absolute_expires_at") val absoluteExpiresAt: String,
    val current: Boolean,
) {
    companion object {
        const val GENERIC_DEVICE_LABEL = "Android device"
    }
}

@Serializable
data class AuthResponse(
    val status: String,
    @SerialName("access_token") val accessToken: String? = null,
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("token_type") val tokenType: String? = null,
    @SerialName("expires_in") val expiresIn: Int = 0,
    val profile: TokenProfile? = null,
    val session: AccountSession? = null,
    val provider: String? = null,
    @SerialName("pending_signup_token") val pendingSignupToken: String? = null,
)

sealed interface ExchangeResult {
    data class Authenticated(
        val accessToken: String,
        val refreshToken: String,
        val expiresIn: Int,
        val profile: TokenProfile,
        val session: AccountSession,
    ) : ExchangeResult

    data class ProfileRequired(
        val provider: AuthProvider,
        val pendingSignupToken: String,
        val expiresIn: Int,
    ) : ExchangeResult
}

@Serializable
data class ProviderProfile(
    val provider: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("avatar_url") val avatarUrl: String? = null,
    @SerialName("refreshed_at") val refreshedAt: String? = null,
)

@Serializable
data class LinkedProvider(
    val provider: String,
    @SerialName("refreshed_at") val refreshedAt: String,
)

@Serializable
data class AccountProfile(
    val nickname: String,
    @SerialName("community_actor_id") val communityActorId: String,
    @SerialName("primary_provider") val primaryProvider: ProviderProfile,
    @SerialName("linked_providers") val linkedProviders: List<LinkedProvider> = emptyList(),
    @SerialName("nickname_changed_at") val nicknameChangedAt: String? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
    val state: String,
)

@Serializable
data class SessionsResponse(val sessions: List<AccountSession> = emptyList())

@Serializable
data class ReauthResponse(
    @SerialName("reauth_token") val reauthToken: String,
    val scope: String,
    @SerialName("expires_in") val expiresIn: Int,
)

@Serializable
data class LinkResponse(val status: String, val provider: String)

@Serializable
data class NicknameResponse(
    val nickname: String,
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String,
    @SerialName("expires_in") val expiresIn: Int,
)

@Serializable
data class DeletionResponse(val status: String)

@Serializable
internal data class ExchangeRequest(
    @SerialName("provider_access_token") val providerAccessToken: String,
    @SerialName("installation_id") val installationId: String,
)

@Serializable
internal data class CompleteSignupRequest(
    @SerialName("pending_signup_token") val pendingSignupToken: String,
    val nickname: String,
    @SerialName("installation_id") val installationId: String,
)

@Serializable
internal data class CancelSignupRequest(
    @SerialName("pending_signup_token") val pendingSignupToken: String,
)

@Serializable
internal data class RefreshRequest(@SerialName("refresh_token") val refreshToken: String)

@Serializable
internal data class ReauthRequest(
    val provider: String,
    @SerialName("provider_access_token") val providerAccessToken: String,
    val scope: String,
)

@Serializable
internal data class LinkRequest(
    @SerialName("provider_access_token") val providerAccessToken: String,
    @SerialName("reauth_token") val reauthToken: String,
)

@Serializable
internal data class NicknameRequest(val nickname: String)

@Serializable
internal data class DeletionRequest(@SerialName("reauth_token") val reauthToken: String)

@Serializable
internal data class PushDeviceRequest(
    @SerialName("fcm_token") val fcmToken: String,
    val platform: String,
)

@Serializable
internal data class ErrorEnvelope(val error: ErrorCode)

@Serializable
internal data class ErrorCode(val code: String)
