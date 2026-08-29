package com.plantdiseases.app.ui.account

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.data.account.AccountProfile
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.LinkedProvider
import com.plantdiseases.app.data.account.ProviderProfile
import org.junit.Test
import java.time.Instant

class AccountUiPolicyTest {
    @Test
    fun reauthProviders_includesEveryRecognizedLinkedProviderOnce() {
        val profile = AccountProfile(
            nickname = "Gardener",
            communityActorId = "actor-id",
            primaryProvider = ProviderProfile("yandex", "Yandex user"),
            linkedProviders = listOf(
                LinkedProvider("yandex", "2026-01-01T00:00:00Z"),
                LinkedProvider("vk", "2026-01-02T00:00:00Z"),
                LinkedProvider("future-provider", "2026-01-03T00:00:00Z"),
            ),
            createdAt = "2026-01-01T00:00:00Z",
            updatedAt = "2026-01-03T00:00:00Z",
            state = "active",
        )

        assertThat(profile.reauthProviders()).containsExactly(
            AuthProvider.YANDEX,
            AuthProvider.VK,
        ).inOrder()
    }

    @Test
    fun emailPrimaryAccountCanReauthenticateWithoutAnOauthProvider() {
        val profile = AccountProfile(
            nickname = "Gardener",
            communityActorId = "actor-id",
            primaryProvider = ProviderProfile("email", "Email"),
            linkedProviders = listOf(LinkedProvider("email", "2026-01-01T00:00:00Z")),
            createdAt = "2026-01-01T00:00:00Z",
            updatedAt = "2026-01-03T00:00:00Z",
            state = "active",
        )

        assertThat(profile.reauthProviders()).containsExactly(AuthProvider.EMAIL)
    }

    @Test
    fun emailCredentialValidationMatchesThePublicAccountPolicy() {
        assertThat(
            validateEmailCredential(
                "gardener@xn--e1afmkfd.xn--p1ai",
                "correct horse garden staple",
            ),
        ).isNull()
        assertThat(
            validateEmailCredential("not-an-address", "correct horse garden staple"),
        ).isEqualTo(EmailCredentialIssue.INVALID_EMAIL)
        assertThat(
            validateEmailCredential("gardener@example.com", "too short"),
        ).isEqualTo(EmailCredentialIssue.INVALID_PASSWORD_LENGTH)
    }

    @Test
    fun passwordLengthUsesUnicodeCodePointsLikeTheAccountServer() {
        val leaf = "🌿"

        assertThat(passwordLengthIsValid(leaf.repeat(14))).isFalse()
        assertThat(passwordLengthIsValid(leaf.repeat(15))).isTrue()
        assertThat(passwordLengthIsValid(leaf.repeat(128))).isTrue()
        assertThat(passwordLengthIsValid(leaf.repeat(129))).isFalse()
    }

    @Test
    fun registrationErrorsDoNotRevealWhetherAnEmailAlreadyExists() {
        assertThat(
            emailAuthenticationError(
                EmailAuthenticationAction.REGISTER,
                "invalid_registration",
            ),
        ).isEqualTo(EmailAuthenticationError.REGISTRATION)
        assertThat(
            emailAuthenticationError(
                EmailAuthenticationAction.REGISTER,
                "registration_unavailable",
            ),
        ).isEqualTo(EmailAuthenticationError.REGISTRATION)
    }

    @Test
    fun parseAccountTimestamp_acceptsServerIsoInstant() {
        assertThat(parseAccountTimestamp("2026-01-02T03:04:05Z"))
            .isEqualTo(Instant.parse("2026-01-02T03:04:05Z"))
    }

    @Test
    fun parseAccountTimestamp_rejectsMalformedValue() {
        assertThat(parseAccountTimestamp("not-a-timestamp")).isNull()
    }

    @Test
    fun nicknameRemainsLockedUntilThirtyFullDaysHaveElapsed() {
        val changedAt = Instant.parse("2026-08-01T10:00:00Z")
        val profile = accountProfile(changedAt.toString())

        assertThat(nicknameChangeAvailableAt(profile))
            .isEqualTo(Instant.parse("2026-08-31T10:00:00Z"))
        assertThat(
            nicknameCanChange(profile, Instant.parse("2026-08-31T09:59:59Z")),
        ).isFalse()
        assertThat(
            nicknameCanChange(profile, Instant.parse("2026-08-31T10:00:00Z")),
        ).isTrue()
    }

    @Test
    fun legacyProfileWithoutNicknameTimestampRemainsEditable() {
        assertThat(nicknameCanChange(accountProfile(null), Instant.parse("2026-08-01T10:00:00Z")))
            .isTrue()
    }

    @Test
    fun repeatedAmbiguousFailureCannotReenableFrozenSignupNickname() {
        val frozen = AuthState.ProfileRequired(
            provider = AuthProvider.YANDEX,
            pendingSignupToken = "pending",
            expiresInSeconds = 600,
            pendingNickname = "Durable Fern",
        )

        assertThat(signupNicknameIsEditable(frozen, loading = true)).isFalse()
        assertThat(signupNicknameIsEditable(frozen, loading = false)).isFalse()

        val rejectedBeforeCommit = frozen.copy(pendingNickname = null)
        assertThat(signupNicknameIsEditable(rejectedBeforeCommit, loading = true)).isFalse()
        assertThat(signupNicknameIsEditable(rejectedBeforeCommit, loading = false)).isTrue()
    }

    private fun accountProfile(nicknameChangedAt: String?): AccountProfile = AccountProfile(
        nickname = "Gardener",
        communityActorId = "actor-id",
        primaryProvider = ProviderProfile("yandex", "Yandex user"),
        linkedProviders = listOf(LinkedProvider("yandex", "2026-01-01T00:00:00Z")),
        nicknameChangedAt = nicknameChangedAt,
        createdAt = "2026-01-01T00:00:00Z",
        updatedAt = "2026-01-03T00:00:00Z",
        state = "active",
    )
}
