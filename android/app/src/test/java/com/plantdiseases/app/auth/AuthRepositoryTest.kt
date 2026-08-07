package com.plantdiseases.app.auth

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.ExchangeResult
import com.plantdiseases.app.testutil.FakeAccountApi
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

class AuthRepositoryTest {
    @Test
    fun restore_exchangesOnlyEncryptedRefreshToken() = runTest {
        val vault = FakeVault("stored-refresh")
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("restored-access", "rotated-refresh")
        }
        val repository = repository(api, vault)

        repository.restore()

        assertThat(api.refreshCalls).isEqualTo(1)
        assertThat(vault.value).isEqualTo("rotated-refresh")
        assertThat(repository.state.value).isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun firstLogin_requiresProfile_andCancelReturnsToGuest() = runTest {
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.YANDEX,
                "pending-token",
                600,
            )
        }
        val repository = repository(api)

        repository.signIn(AuthProvider.YANDEX, "synthetic-provider-token")
        assertThat(repository.state.value).isInstanceOf(AuthState.ProfileRequired::class.java)

        repository.cancelProfile()

        assertThat(api.cancelCalls).isEqualTo(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun concurrent401s_shareOneRefresh() = runTest {
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        }
        val repository = repository(api)
        repository.signIn(AuthProvider.VK, "synthetic-provider-token")

        var attempts = 0
        val firstFailures = AtomicInteger()
        val bothCallsReached401 = CompletableDeferred<Unit>()
        val calls = List(2) {
            async {
                repository.authenticatedCall { token ->
                    attempts++
                    if (token == "access-1") {
                        if (firstFailures.incrementAndGet() == 2) bothCallsReached401.complete(Unit)
                        bothCallsReached401.await()
                        throw AccountApiException(401, "auth_required")
                    }
                    "ok"
                }
            }
        }
        val results = calls.awaitAll()

        assertThat(results.map { it.getOrNull() }).containsExactly("ok", "ok")
        assertThat(api.refreshCalls).isEqualTo(1)
        assertThat(attempts).isEqualTo(4)
    }

    @Test
    fun second401_isNotRefreshedAgain_andClearsCredentials() = runTest {
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        }
        val vault = FakeVault()
        val repository = repository(api, vault)
        repository.signIn(AuthProvider.VK, "synthetic-provider-token")
        var attempts = 0

        val result = repository.authenticatedCall<Unit> {
            attempts++
            throw AccountApiException(401, "auth_required")
        }

        assertThat(result.isFailure).isTrue()
        assertThat(attempts).isEqualTo(2)
        assertThat(api.refreshCalls).isEqualTo(1)
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun staleSecond401_doesNotClearNewerTokenForSameSession() = runTest {
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        }
        val repository = repository(api)
        repository.signIn(AuthProvider.VK, "synthetic-provider-token")

        val result = repository.authenticatedCall<Unit> { token ->
            if (token == "access-1") throw AccountApiException(401, "auth_required")
            api.refreshResult = FakeAccountApi.authenticated("access-3", "refresh-3")
            val newer = repository.authenticatedCall { nestedToken ->
                if (nestedToken == "access-2") {
                    throw AccountApiException(401, "auth_required")
                }
                nestedToken
            }
            assertThat(newer.getOrNull()).isEqualTo("access-3")
            throw AccountApiException(401, "auth_required")
        }

        assertThat(result.isFailure).isTrue()
        assertThat(repository.state.value).isInstanceOf(AuthState.Authenticated::class.java)
        assertThat(repository.authenticatedCall { it }.getOrNull()).isEqualTo("access-3")
        assertThat(api.refreshCalls).isEqualTo(2)
    }

    @Test
    fun mutationStartedUnderOldAccount_isNeverReplayedAfterNewSignIn() = runTest {
        val api = FakeAccountApi()
        val repository = repository(api)
        repository.signIn(AuthProvider.YANDEX, "synthetic-old-provider-token")
        val requestStarted = CompletableDeferred<Unit>()
        val releaseUnauthorized = CompletableDeferred<Unit>()
        var attempts = 0

        val mutation = async {
            repository.authenticatedCall {
                attempts++
                requestStarted.complete(Unit)
                releaseUnauthorized.await()
                throw AccountApiException(401, "auth_required")
            }
        }
        requestStarted.await()
        api.exchangeResult = FakeAccountApi.authenticated(
            accessToken = "new-access",
            refreshToken = "new-refresh",
            profile = FakeAccountApi.profile("New Gardener"),
            session = FakeAccountApi.session().copy(
                sessionId = "00000000-0000-4000-8000-000000000004",
            ),
        )
        repository.signIn(AuthProvider.VK, "synthetic-new-provider-token")
        releaseUnauthorized.complete(Unit)

        val result = mutation.await()
        assertThat(result.exceptionOrNull())
            .isInstanceOf(AuthenticationRequiredException::class.java)
        assertThat(attempts).isEqualTo(1)
        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat((repository.state.value as AuthState.Authenticated).profile.nickname)
            .isEqualTo("New Gardener")
    }

    @Test
    fun logoutClearsCredentialsAndProviderState_evenWhenNetworkFails() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IllegalStateException("offline") }
        val vault = FakeVault()
        var providerClears = 0
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            ProviderSessionCleaner { providerClears++ },
        )
        val localRoomRows = mutableListOf("scan", "garden", "reminder")
        repository.signIn(AuthProvider.YANDEX, "synthetic-provider-token")

        runCatching { repository.logout() }

        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(providerClears).isEqualTo(1)
        assertThat(api.disablePushCalls).isEqualTo(1)
        assertThat(localRoomRows).containsExactly("scan", "garden", "reminder").inOrder()
    }

    @Test
    fun guestCannotRegisterPush_andAuthenticatedRegistrationHasNoDeviceModel() = runTest {
        val api = FakeAccountApi()
        val repository = repository(api)

        assertThat(repository.registerPush("synthetic-fcm-token").isFailure).isTrue()
        assertThat(api.registerPushCalls).isEqualTo(0)

        repository.signIn(AuthProvider.YANDEX, "synthetic-provider-token")
        assertThat(repository.registerPush("synthetic-fcm-token").isSuccess).isTrue()
        assertThat(api.lastPushToken).isEqualTo("synthetic-fcm-token")
    }

    private fun repository(
        api: FakeAccountApi,
        vault: FakeVault = FakeVault(),
    ) = AuthRepository(api, vault, FakeInstallationId)

    private class FakeVault(initial: String? = null) : RefreshTokenVault {
        var value: String? = initial

        override fun read(): String? = value

        override fun write(token: String) {
            value = token
        }

        override fun clear() {
            value = null
        }
    }

    private data object FakeInstallationId : InstallationIdProvider {
        override fun get(): String = FakeAccountApi.INSTALLATION_ID
    }
}
