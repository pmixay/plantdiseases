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
    fun parseAccountTimestamp_acceptsServerIsoInstant() {
        assertThat(parseAccountTimestamp("2026-01-02T03:04:05Z"))
            .isEqualTo(Instant.parse("2026-01-02T03:04:05Z"))
    }

    @Test
    fun parseAccountTimestamp_rejectsMalformedValue() {
        assertThat(parseAccountTimestamp("not-a-timestamp")).isNull()
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
}
