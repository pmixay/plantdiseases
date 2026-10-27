package com.plantdiseases.app.auth

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.ExchangeResult
import com.plantdiseases.app.testutil.FakeAccountApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test
import java.io.IOException
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
    fun restore_transientFailurePreservesCredentialAndBecomesUnavailable() = runTest {
        val vault = FakeVault("stored-refresh")
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onRefresh = { throw IOException("offline") }
        }
        val repository = repository(api, vault, installationId = installationId)

        repository.restore()

        assertThat(vault.value).isEqualTo("stored-refresh")
        assertThat(repository.state.value).isEqualTo(AuthState.Unavailable())
        assertThat(installationId.rotations).isEqualTo(0)
    }

    @Test
    fun restorePersistenceFailureQueuesOldFamilyProofAndStaysGuest() = runTest {
        val vault = FakeVault("stored-refresh").apply {
            writeFailure = IOException("disk")
        }
        val revocations = FakeLogoutRevocations()
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("restored-access", "rotated-refresh")
        }
        val repository = repository(api, vault, revocations, installationId)

        repository.restore()

        assertThat(revocations.tokens).containsExactly("stored-refresh")
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun unavailableRestoreCanBeRetriedWithoutSigningInAgain() = runTest {
        val vault = FakeVault("stored-refresh")
        var offline = true
        var firstRotationId: String? = null
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("restored-access", "rotated-refresh")
            onRefresh = {
                if (offline) {
                    firstRotationId = lastRefreshRotationId
                    throw IOException("offline")
                }
                assertThat(lastRefreshRotationId).isEqualTo(firstRotationId)
            }
        }
        val repository = repository(api, vault)
        repository.restore()
        offline = false

        repository.restore()

        assertThat(repository.state.value).isInstanceOf(AuthState.Authenticated::class.java)
        assertThat(vault.value).isEqualTo("rotated-refresh")
    }

    @Test
    fun restore_definitiveRefreshRejectionClearsCredential() = runTest {
        val vault = FakeVault("expired-refresh")
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onRefresh = { throw AccountApiException(401, "refresh_expired") }
        }
        val repository = repository(api, vault, installationId = installationId)

        repository.restore()

        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun restore_deletionPendingIsDefinitiveAndClearsCredential() = runTest {
        val vault = FakeVault("deleting-refresh")
        val api = FakeAccountApi().apply {
            onRefresh = { throw AccountApiException(409, "deletion_pending") }
        }
        val repository = repository(api, vault)

        repository.restore()

        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
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
    fun emailRegistrationUsesTheSameDurablePendingProfilePath() = runTest {
        val signupStore = FakeSignupStore()
        val api = FakeAccountApi().apply {
            emailRegistrationResult = ExchangeResult.ProfileRequired(
                AuthProvider.EMAIL,
                "email-pending",
                600,
            )
        }
        val repository = repository(api, signupCompletions = signupStore)

        repository.registerWithEmail(
            "gardener@example.com",
            "correct horse garden staple",
        )

        assertThat(api.emailRegistrationCalls).isEqualTo(1)
        assertThat(api.lastEmailInstallationId).isEqualTo(FakeAccountApi.INSTALLATION_ID)
        assertThat(signupStore.value?.provider).isEqualTo(AuthProvider.EMAIL)
        assertThat(signupStore.value?.pendingSignupToken).isEqualTo("email-pending")
        val state = repository.state.value as AuthState.ProfileRequired
        assertThat(state.provider).isEqualTo(AuthProvider.EMAIL)
    }

    @Test
    fun emailLoginDisplacesAndRevokesTheOldCredentialBeforeSendingPassword() = runTest {
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi()
        val repository = repository(
            api = api,
            vault = FakeVault("old-refresh"),
            revocations = FakeLogoutRevocations(),
            installationId = installationId,
        )

        repository.signInWithEmail(
            "gardener@example.com",
            "correct horse garden staple",
        )

        assertThat(api.logoutRefreshTokens).containsExactly("old-refresh")
        assertThat(installationId.rotations).isEqualTo(1)
        assertThat(api.lastEmailInstallationId).isEqualTo(RecordingInstallationId.ROTATED_ID)
        assertThat(repository.state.value).isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun emailReauthenticationUsesCurrentAccountSessionWithoutPersistingAnotherCredential() = runTest {
        val vault = FakeVault()
        val api = FakeAccountApi()
        val repository = repository(api, vault)
        repository.signIn(AuthProvider.YANDEX, "provider-proof")

        val result = repository.reauthenticateWithEmail(
            "correct horse garden staple",
            "link_vk",
        )

        assertThat(result.getOrThrow().reauthToken).isEqualTo("email-reauth")
        assertThat(api.emailReauthCalls).isEqualTo(1)
        assertThat(api.lastEmailReauthScope).isEqualTo("link_vk")
        assertThat(vault.value).isEqualTo("refresh-1")
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
    fun lostSignupResponseAndProcessDeathReuseTheDurableCompletionIdentity() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val firstApi = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.YANDEX,
                "pending-token",
                600,
            )
            onComplete = { throw IOException("response lost") }
        }
        val firstRepository = repository(
            firstApi,
            vault,
            signupCompletions = signupStore,
        )
        firstRepository.signIn(AuthProvider.YANDEX, "provider-proof")

        val firstFailure = runCatching {
            firstRepository.completeProfile("Retry Fern")
        }.exceptionOrNull()

        assertThat(firstFailure).isInstanceOf(IOException::class.java)
        val durable = checkNotNull(signupStore.value)
        assertThat(durable.nickname).isEqualTo("Retry Fern")
        assertThat(firstApi.lastCompletionId).isEqualTo(durable.completionId)
        assertThat(vault.value).isNull()

        val restartedApi = FakeAccountApi()
        val restarted = repository(
            restartedApi,
            vault,
            signupCompletions = signupStore,
        )
        restarted.restore()

        assertThat(restartedApi.completeCalls).isEqualTo(1)
        assertThat(restartedApi.lastCompletionId).isEqualTo(durable.completionId)
        assertThat(restartedApi.lastCompletionInstallationId).isEqualTo(durable.installationId)
        assertThat(restartedApi.lastCompletionNickname).isEqualTo("Retry Fern")
        assertThat(restartedApi.confirmCalls).isEqualTo(1)
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isEqualTo("refresh-1")
        assertThat(restarted.state.value).isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun processDeathAfterVaultWriteRetriesConfirmationBeforeExposingAccount() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.VK,
                "pending-token",
                600,
            )
            onConfirm = { throw IOException("confirmation response lost") }
        }
        val first = repository(api, vault, signupCompletions = signupStore)
        first.signIn(AuthProvider.VK, "provider-proof")

        val failure = runCatching { first.completeProfile("Durable Fern") }.exceptionOrNull()

        assertThat(failure).isInstanceOf(IOException::class.java)
        assertThat(vault.value).isEqualTo("refresh-1")
        assertThat(signupStore.value?.nickname).isEqualTo("Durable Fern")
        assertThat(signupStore.value?.confirmationAttempted).isTrue()
        assertThat(first.state.value).isInstanceOf(AuthState.ProfileRequired::class.java)

        api.onConfirm = {}
        val restarted = repository(api, vault, signupCompletions = signupStore)
        restarted.restore()

        assertThat(api.completeCalls).isEqualTo(2)
        assertThat(api.confirmCalls).isEqualTo(2)
        assertThat(signupStore.value).isNull()
        assertThat(restarted.state.value).isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun ambiguousRetryIgnoresEditedTextAndReusesTheDurableNickname() = runTest {
        val signupStore = FakeSignupStore()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.YANDEX,
                "pending-token",
                600,
            )
            onComplete = { throw IOException("response lost") }
        }
        val repository = repository(api, signupCompletions = signupStore)
        repository.signIn(AuthProvider.YANDEX, "provider-proof")
        runCatching { repository.completeProfile("First Fern") }

        val retry = runCatching {
            repository.completeProfile("Second Fern")
        }.exceptionOrNull()

        assertThat(retry).isInstanceOf(IOException::class.java)
        assertThat(api.completeCalls).isEqualTo(2)
        assertThat(api.lastCompletionNickname).isEqualTo("First Fern")
        assertThat(signupStore.value?.nickname).isEqualTo("First Fern")
    }

    @Test
    fun precommitNicknameRejectionReopensTheSamePendingOperationForEditing() = runTest {
        listOf("nickname_taken", "nickname_invalid").forEach { errorCode ->
            val signupStore = FakeSignupStore()
            var rejectFirstAttempt = true
            val api = FakeAccountApi().apply {
                exchangeResult = ExchangeResult.ProfileRequired(
                    AuthProvider.YANDEX,
                    "pending-$errorCode",
                    600,
                )
                onComplete = {
                    if (rejectFirstAttempt) {
                        rejectFirstAttempt = false
                        throw AccountApiException(409, errorCode)
                    }
                }
            }
            val repository = repository(api, signupCompletions = signupStore)
            repository.signIn(AuthProvider.YANDEX, "provider-proof")
            val original = checkNotNull(signupStore.value)

            val failure = runCatching {
                repository.completeProfile("Rejected Fern")
            }.exceptionOrNull()

            assertThat(failure).isInstanceOf(AccountApiException::class.java)
            assertThat((failure as AccountApiException).errorCode).isEqualTo(errorCode)
            val editable = checkNotNull(signupStore.value)
            assertThat(editable.nickname).isNull()
            assertThat(editable.pendingSignupToken).isEqualTo(original.pendingSignupToken)
            assertThat(editable.completionId).isEqualTo(original.completionId)
            assertThat(editable.installationId).isEqualTo(original.installationId)
            val pendingState = repository.state.value as AuthState.ProfileRequired
            assertThat(pendingState.pendingNickname).isNull()

            repository.completeProfile("Available Fern")

            assertThat(api.completeCalls).isEqualTo(2)
            assertThat(api.lastCompletionId).isEqualTo(original.completionId)
            assertThat(api.lastCompletionNickname).isEqualTo("Available Fern")
            assertThat(signupStore.value).isNull()
            assertThat(repository.state.value)
                .isInstanceOf(AuthState.Authenticated::class.java)
        }
    }

    @Test
    fun processDeathNicknameRejectionAlsoReopensDurableOperation() = runTest {
        val original = pendingSignupOperation("Taken After Restart")
        val signupStore = FakeSignupStore(original)
        val api = FakeAccountApi().apply {
            onComplete = { throw AccountApiException(409, "nickname_taken") }
        }
        val restarted = repository(api, signupCompletions = signupStore)

        restarted.restore()

        val editable = checkNotNull(signupStore.value)
        assertThat(editable.nickname).isNull()
        assertThat(editable.pendingSignupToken).isEqualTo(original.pendingSignupToken)
        assertThat(editable.completionId).isEqualTo(original.completionId)
        assertThat(editable.installationId).isEqualTo(original.installationId)
        val state = restarted.state.value as AuthState.ProfileRequired
        assertThat(state.pendingNickname).isNull()
    }

    @Test
    fun prunedConfirmedMarkerReconcilesDurableRefreshWithoutDestroyingAccount() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.VK,
                "pending-token",
                600,
            )
            onConfirm = { throw IOException("confirmation response lost") }
        }
        val repository = repository(api, vault, signupCompletions = signupStore)
        repository.signIn(AuthProvider.VK, "provider-proof")
        runCatching { repository.completeProfile("Confirmed Fern") }
        assertThat(vault.value).isEqualTo("refresh-1")
        api.onConfirm = {}
        api.onComplete = { throw AccountApiException(401, "pending_signup_expired") }
        api.refreshResult = FakeAccountApi.authenticated(
            accessToken = "recovered-access",
            refreshToken = "recovered-refresh",
        )

        val failure = runCatching {
            repository.completeProfile("ignored")
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(SignupAlreadyConfirmedException::class.java)
        assertThat(api.refreshCalls).isEqualTo(1)
        assertThat(vault.value).isEqualTo("recovered-refresh")
        assertThat(signupStore.value).isNull()
        assertThat(repository.state.value)
            .isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun cancelWithPrunedConfirmedMarkerRecoversInsteadOfReportingGuest() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.YANDEX,
                "pending-token",
                600,
            )
            onCancel = { throw AccountApiException(401, "pending_signup_expired") }
            refreshResult = FakeAccountApi.authenticated(
                accessToken = "recovered-access",
                refreshToken = "rotated-confirmed-refresh",
            )
        }
        val repository = repository(api, vault, signupCompletions = signupStore)
        repository.signIn(AuthProvider.YANDEX, "provider-proof")
        signupStore.value = checkNotNull(signupStore.value).copy(nickname = "Confirmed Fern")
        vault.value = "durable-confirmed-refresh"

        val failure = runCatching { repository.cancelProfile() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(SignupAlreadyConfirmedException::class.java)
        assertThat(api.refreshCalls).isEqualTo(1)
        assertThat(vault.value).isEqualTo("rotated-confirmed-refresh")
        assertThat(signupStore.value).isNull()
        assertThat(repository.state.value)
            .isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun revokedSessionAfterLostConfirmRequiresProviderSigninNotCancellationSuccess() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val installation = RecordingInstallationId()
        var providerClears = 0
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.VK,
                "pending-token",
                600,
            )
            onCancel = { throw AccountApiException(401, "pending_signup_expired") }
            onRefresh = { throw AccountApiException(401, "refresh_expired") }
        }
        val repository = AuthRepository(
            api,
            vault,
            installation,
            ProviderSessionCleaner { providerClears++ },
            signupCompletions = signupStore,
        )
        repository.signIn(AuthProvider.VK, "provider-proof")
        signupStore.value = checkNotNull(signupStore.value).copy(
            nickname = "Confirmed Fern",
            confirmationAttempted = true,
        )
        vault.value = "revoked-confirmed-refresh"

        val failure = runCatching { repository.cancelProfile() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(SignupAlreadyConfirmedException::class.java)
        assertThat(api.refreshCalls).isEqualTo(1)
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isNull()
        assertThat(installation.rotations).isEqualTo(1)
        assertThat(providerClears).isEqualTo(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun cancelAfterLostConfirmationRecoversAuthenticatedInsteadOfClaimingSuccess() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.VK,
                "pending-token",
                600,
            )
            onConfirm = { throw IOException("lost") }
        }
        val repository = repository(api, vault, signupCompletions = signupStore)
        repository.signIn(AuthProvider.VK, "provider-proof")
        runCatching { repository.completeProfile("Confirmed Fern") }
        api.onConfirm = {}
        api.onCancel = { throw AccountApiException(409, "signup_already_confirmed") }

        val result = runCatching { repository.cancelProfile() }.exceptionOrNull()

        assertThat(result).isInstanceOf(SignupAlreadyConfirmedException::class.java)
        assertThat(repository.state.value).isInstanceOf(AuthState.Authenticated::class.java)
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isEqualTo("refresh-1")
    }

    @Test
    fun definitiveCompletionExpiryClearsPendingStateWithoutAProcessRestart() = runTest {
        val signupStore = FakeSignupStore()
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.YANDEX,
                "pending-token",
                600,
            )
            onComplete = {
                throw AccountApiException(409, "signup_completion_expired")
            }
        }
        val repository = repository(
            api,
            installationId = installationId,
            signupCompletions = signupStore,
        )
        repository.signIn(AuthProvider.YANDEX, "provider-proof")

        val failure = runCatching { repository.completeProfile("Expired Fern") }
            .exceptionOrNull()

        assertThat(failure).isInstanceOf(AccountApiException::class.java)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(signupStore.value).isNull()
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun replayedPendingAbandonmentClearsProviderSessionAfterDurableLocalCleanup() = runTest {
        val signupStore = FakeSignupStore()
        val installationId = RecordingInstallationId()
        var providerClears = 0
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.VK,
                "pending-token",
                600,
            )
            onComplete = { throw AccountApiException(409, "pending_signup_replayed") }
        }
        val repository = AuthRepository(
            api,
            FakeVault(),
            installationId,
            ProviderSessionCleaner { providerClears++ },
            signupCompletions = signupStore,
        )
        repository.signIn(AuthProvider.VK, "provider-proof")

        val failure = runCatching { repository.completeProfile("Replayed Fern") }
            .exceptionOrNull()

        assertThat(failure).isInstanceOf(AccountApiException::class.java)
        assertThat(signupStore.value).isNull()
        assertThat(installationId.rotations).isEqualTo(1)
        assertThat(providerClears).isEqualTo(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun expiredCompletionWithTransientRefreshPreservesFrozenProofs() = runTest {
        val signupStore = FakeSignupStore()
        val vault = FakeVault()
        val api = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.YANDEX,
                "pending-token",
                600,
            )
            onConfirm = { throw IOException("confirmation response lost") }
        }
        val repository = repository(api, vault, signupCompletions = signupStore)
        repository.signIn(AuthProvider.YANDEX, "provider-proof")
        runCatching { repository.completeProfile("Frozen Fern") }
        api.onConfirm = {}
        api.onComplete = { throw AccountApiException(401, "pending_signup_expired") }
        api.onRefresh = { throw IOException("offline") }

        val failure = runCatching { repository.completeProfile("edited") }.exceptionOrNull()

        assertThat(failure).isInstanceOf(IOException::class.java)
        assertThat(signupStore.value?.nickname).isEqualTo("Frozen Fern")
        assertThat(vault.value).isEqualTo("refresh-1")
        val state = repository.state.value as AuthState.ProfileRequired
        assertThat(state.pendingNickname).isEqualTo("Frozen Fern")
    }

    @Test
    fun processDeathExpiryWithoutRefreshRotatesBeforeBecomingGuest() = runTest {
        val operation = pendingSignupOperation("Expired Fern")
        val signupStore = FakeSignupStore(operation)
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onComplete = { throw AccountApiException(401, "pending_signup_expired") }
        }
        val restarted = repository(
            api,
            installationId = installationId,
            signupCompletions = signupStore,
        )

        restarted.restore()

        assertThat(signupStore.value).isNull()
        assertThat(installationId.rotations).isEqualTo(1)
        assertThat(restarted.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun processDeathWithDeadConfirmedSessionRequiresFreshProviderSignin() = runTest {
        val signupStore = FakeSignupStore(
            pendingSignupOperation("Confirmed Fern").copy(
                confirmationAttempted = true,
            ),
        )
        val vault = FakeVault("dead-confirmed-refresh")
        val installationId = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onComplete = { throw AccountApiException(401, "pending_signup_expired") }
            onRefresh = { throw AccountApiException(401, "refresh_expired") }
        }
        val restarted = repository(
            api,
            vault,
            installationId = installationId,
            signupCompletions = signupStore,
        )

        restarted.restore()

        assertThat(restarted.state.value).isEqualTo(AuthState.Unavailable())
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isNull()
        assertThat(installationId.rotations).isEqualTo(1)

        api.onRefresh = {}
        api.exchangeResult = FakeAccountApi.authenticated(
            accessToken = "provider-recovered-access",
            refreshToken = "provider-recovered-refresh",
        )
        restarted.signIn(AuthProvider.YANDEX, "fresh-provider-proof")

        assertThat(restarted.state.value)
            .isInstanceOf(AuthState.Authenticated::class.java)
    }

    @Test
    fun definitiveCancelExpiryBecomesGuestAndSuccessfulCancelRotatesInstallation() = runTest {
        val expiredStore = FakeSignupStore()
        val expiredInstallation = RecordingInstallationId()
        val expiredApi = FakeAccountApi().apply {
            exchangeResult = ExchangeResult.ProfileRequired(
                AuthProvider.VK,
                "expired-pending",
                600,
            )
            onCancel = { throw AccountApiException(409, "signup_completion_expired") }
        }
        val expiredRepository = repository(
            expiredApi,
            installationId = expiredInstallation,
            signupCompletions = expiredStore,
        )
        expiredRepository.signIn(AuthProvider.VK, "provider-proof")

        expiredRepository.cancelProfile()

        assertThat(expiredRepository.state.value).isEqualTo(AuthState.Guest)
        assertThat(expiredStore.value).isNull()
        assertThat(expiredInstallation.rotations).isEqualTo(1)

        val cancelledStore = FakeSignupStore()
        val cancelledInstallation = RecordingInstallationId()
        val cancelledRepository = repository(
            FakeAccountApi().apply {
                exchangeResult = ExchangeResult.ProfileRequired(
                    AuthProvider.YANDEX,
                    "cancelled-pending",
                    600,
                )
            },
            installationId = cancelledInstallation,
            signupCompletions = cancelledStore,
        )
        cancelledRepository.signIn(AuthProvider.YANDEX, "provider-proof")

        cancelledRepository.cancelProfile()

        assertThat(cancelledRepository.state.value).isEqualTo(AuthState.Guest)
        assertThat(cancelledStore.value).isNull()
        assertThat(cancelledInstallation.rotations).isEqualTo(1)
    }

    @Test
    fun failedAccountSwitchCannotLeaveAnUnrevokedOldCredentialAndExchangeNewToken() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val vault = FakeVault("old-refresh").apply { clearFailure = IOException("disk") }
        val revocations = FakeLogoutRevocations().apply { enqueueFailure = IOException("disk") }
        val repository = repository(api, vault, revocations)

        val failure = runCatching {
            repository.signIn(AuthProvider.VK, "new-provider-token")
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("old-refresh")
        assertThat(api.exchangeCalls).isEqualTo(0)
        assertThat(vault.value).isEqualTo("old-refresh")
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun accountSwitchRevokesDisplacedRefreshCredentialBeforeProviderExchange() = runTest {
        val api = FakeAccountApi()
        val vault = FakeVault("old-refresh")
        val revocations = FakeLogoutRevocations()
        val installationId = RecordingInstallationId()
        val repository = repository(api, vault, revocations, installationId)

        repository.signIn(AuthProvider.VK, "new-provider-token")

        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("old-refresh")
        assertThat(api.exchangeCalls).isEqualTo(1)
        assertThat(api.lastExchangeInstallationId).isEqualTo(RecordingInstallationId.ROTATED_ID)
        assertThat(installationId.rotations).isEqualTo(1)
        assertThat(revocations.tokens).isEmpty()
        assertThat(vault.value).isEqualTo("refresh-1")
    }

    @Test
    fun transientRefreshFailure_doesNotDestroyAuthenticatedCredential() = runTest {
        val api = FakeAccountApi().apply {
            onRefresh = { throw IOException("offline") }
        }
        val vault = FakeVault()
        val repository = repository(api, vault)
        repository.signIn(AuthProvider.VK, "synthetic-provider-token")

        val result = repository.authenticatedCall<Unit> {
            throw AccountApiException(401, "auth_required")
        }

        assertThat(result.exceptionOrNull()).isInstanceOf(IOException::class.java)
        assertThat(vault.value).isEqualTo("refresh-1")
        val unavailable = repository.state.value as AuthState.Unavailable
        assertThat(unavailable.lastAuthenticated?.profile?.nickname).isEqualTo("Beta Gardener")
    }

    @Test
    fun deletionPendingDuringUnauthorizedRefreshClearsCredential() = runTest {
        val api = FakeAccountApi().apply {
            onRefresh = { throw AccountApiException(409, "deletion_pending") }
        }
        val vault = FakeVault()
        val repository = repository(api, vault)
        repository.signIn(AuthProvider.VK, "synthetic-provider-token")

        val result = repository.authenticatedCall<Unit> {
            throw AccountApiException(401, "auth_required")
        }

        assertThat(result.exceptionOrNull())
            .isInstanceOf(AuthenticationRequiredException::class.java)
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
    fun logoutClearsCredentialsAndQueuesRefreshProof_evenWhenNetworkFails() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IllegalStateException("offline") }
        val vault = FakeVault()
        val revocations = FakeLogoutRevocations()
        val installationId = RecordingInstallationId()
        var providerClears = 0
        val repository = AuthRepository(
            api,
            vault,
            installationId,
            ProviderSessionCleaner { providerClears++ },
            revocations,
        )
        val localRoomRows = mutableListOf("scan", "garden", "reminder")
        repository.signIn(AuthProvider.YANDEX, "synthetic-provider-token")

        runCatching { repository.logout() }

        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(providerClears).isEqualTo(1)
        assertThat(api.disablePushCalls).isEqualTo(0)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("refresh-1")
        assertThat(revocations.tokens).containsExactly("refresh-1")
        assertThat(revocations.failures).isEqualTo(1)
        assertThat(installationId.rotations).isEqualTo(1)
        assertThat(localRoomRows).containsExactly("scan", "garden", "reminder").inOrder()
    }

    @Test
    fun logoutReportsWhenNeitherCredentialDestructionNorRevocationQueueIsDurable() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val vault = FakeVault()
        val revocations = FakeLogoutRevocations().apply { enqueueFailure = IOException("disk") }
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            logoutRevocations = revocations,
        )
        repository.signIn(AuthProvider.YANDEX, "provider-token")
        vault.clearFailure = IOException("disk")

        val failure = runCatching { repository.logout() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("refresh-1")
    }

    @Test
    fun providerCleanerCancellationCannotBypassRemoteLogoutDurabilityCheck() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val vault = FakeVault()
        val revocations = FakeLogoutRevocations()
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            ProviderSessionCleaner { throw CancellationException("cancelled") },
            revocations,
        )
        repository.signIn(AuthProvider.YANDEX, "provider-token")
        revocations.enqueueFailure = IOException("disk")

        val failure = runCatching { repository.logout() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("refresh-1")
        assertThat(vault.value).isNull()
    }

    @Test
    fun logoutPublishesGuestBeforeRemoteRevocationCompletes() = runTest {
        val revocationStarted = CompletableDeferred<Unit>()
        val releaseRevocation = CompletableDeferred<Unit>()
        val api = FakeAccountApi().apply {
            onLogout = {
                revocationStarted.complete(Unit)
                releaseRevocation.await()
            }
        }
        val vault = FakeVault()
        val revocations = FakeLogoutRevocations()
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            logoutRevocations = revocations,
        )
        repository.signIn(AuthProvider.YANDEX, "synthetic-provider-token")

        val logout = async { repository.logout() }
        revocationStarted.await()

        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(vault.value).isNull()
        assertThat(revocations.tokens).containsExactly("refresh-1")

        releaseRevocation.complete(Unit)
        logout.await()
        assertThat(revocations.tokens).isEmpty()
    }

    @Test
    fun restoreRetriesPendingLogoutBeforeReadingActiveCredential() = runTest {
        val api = FakeAccountApi()
        val revocations = FakeLogoutRevocations("old-refresh")
        val repository = AuthRepository(
            api,
            FakeVault(),
            FakeInstallationId,
            logoutRevocations = revocations,
        )

        repository.restore()

        assertThat(api.lastLogoutRefreshToken).isEqualTo("old-refresh")
        assertThat(revocations.tokens).isEmpty()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun restoreDrainsEveryPendingRemoteLogoutInOneStartup() = runTest {
        val api = FakeAccountApi()
        val revocations = FakeLogoutRevocations("old-refresh-1", "old-refresh-2")
        val repository = AuthRepository(
            api,
            FakeVault(),
            FakeInstallationId,
            logoutRevocations = revocations,
        )

        repository.restore()

        assertThat(api.logoutCalls).isEqualTo(2)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("old-refresh-2")
        assertThat(revocations.tokens).isEmpty()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun offlineLogoutSchedulesPersistedRemoteRetry() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val revocations = FakeLogoutRevocations()
        val scheduler = RecordingLogoutRetryScheduler()
        val repository = AuthRepository(
            api,
            FakeVault("refresh-to-revoke"),
            FakeInstallationId,
            logoutRevocations = revocations,
            logoutRetryScheduler = scheduler,
        )

        repository.logout()

        assertThat(revocations.tokens).containsExactly("refresh-to-revoke")
        assertThat(scheduler.calls).isAtLeast(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun failedSignInPersistenceQueuesNewRemoteSessionForRevocation() = runTest {
        val api = FakeAccountApi()
        val vault = FakeVault().apply { writeFailure = IOException("disk") }
        val revocations = FakeLogoutRevocations()
        val scheduler = RecordingLogoutRetryScheduler()
        val installationId = RecordingInstallationId()
        val repository = AuthRepository(
            api,
            vault,
            installationId,
            logoutRevocations = revocations,
            logoutRetryScheduler = scheduler,
        )

        val failure = runCatching {
            repository.signIn(AuthProvider.YANDEX, "provider-token")
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(IOException::class.java)
        assertThat(revocations.tokens).containsExactly("refresh-1")
        assertThat(scheduler.calls).isAtLeast(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun failedSignInPersistenceImmediatelyRevokesWhenQueuePersistenceFails() = runTest {
        val vaultFailure = IOException("vault disk")
        val api = FakeAccountApi()
        val vault = FakeVault().apply { writeFailure = vaultFailure }
        val revocations = FakeLogoutRevocations().apply {
            enqueueFailure = IOException("queue disk")
        }
        val installationId = RecordingInstallationId()
        val repository = repository(api, vault, revocations, installationId)

        val failure = runCatching {
            repository.signIn(AuthProvider.YANDEX, "provider-token")
        }.exceptionOrNull()

        assertThat(failure).isSameInstanceAs(vaultFailure)
        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("refresh-1")
        assertThat(revocations.tokens).isEmpty()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun failedSignInPersistenceReportsNonDurableWhenQueueAndRemoteRevokeFail() = runTest {
        val vaultFailure = IOException("vault disk")
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val vault = FakeVault().apply { writeFailure = vaultFailure }
        val revocations = FakeLogoutRevocations().apply {
            enqueueFailure = IOException("queue disk")
        }
        val installationId = RecordingInstallationId()
        val repository = repository(api, vault, revocations, installationId)

        val failure = runCatching {
            repository.signIn(AuthProvider.YANDEX, "provider-token")
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(failure?.cause).isSameInstanceAs(vaultFailure)
        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(revocations.tokens).isEmpty()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun refreshPersistenceImmediatelyRevokesFamilyWhenQueuePersistenceFails() = runTest {
        val vaultFailure = IOException("vault disk")
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        }
        val vault = FakeVault()
        val revocations = FakeLogoutRevocations()
        val installationId = RecordingInstallationId()
        val repository = repository(api, vault, revocations, installationId)
        repository.signIn(AuthProvider.VK, "provider-token")
        vault.writeFailure = vaultFailure
        revocations.enqueueFailure = IOException("queue disk")

        val result = repository.authenticatedCall<Unit> {
            throw AccountApiException(401, "auth_required")
        }

        assertThat(result.exceptionOrNull()).isSameInstanceAs(vaultFailure)
        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(api.lastLogoutRefreshToken).isEqualTo("refresh-1")
        assertThat(revocations.tokens).isEmpty()
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun refreshPersistenceReportsNonDurableWhenQueueAndRemoteRevokeFail() = runTest {
        val vaultFailure = IOException("vault disk")
        val api = FakeAccountApi().apply {
            refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        }
        val vault = FakeVault()
        val revocations = FakeLogoutRevocations()
        val installationId = RecordingInstallationId()
        val repository = repository(api, vault, revocations, installationId)
        repository.signIn(AuthProvider.VK, "provider-token")
        vault.writeFailure = vaultFailure
        revocations.enqueueFailure = IOException("queue disk")
        api.logoutFailure = IOException("offline")

        val result = repository.authenticatedCall<Unit> {
            throw AccountApiException(401, "auth_required")
        }

        val failure = result.exceptionOrNull()
        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(failure?.cause).isSameInstanceAs(vaultFailure)
        assertThat(api.logoutCalls).isEqualTo(1)
        assertThat(revocations.tokens).isEmpty()
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun successfulDeletionRotatesInstallationIdentity() = runTest {
        val api = FakeAccountApi()
        val installationId = RecordingInstallationId()
        val repository = repository(api, installationId = installationId)
        repository.signIn(AuthProvider.YANDEX, "provider-token")

        val result = repository.deleteAccount("reauth-token")

        assertThat(result.isSuccess).isTrue()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun queueOnlyRetryReportsFailureForWorkManagerBackoff() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val scheduler = RecordingLogoutRetryScheduler()
        val repository = AuthRepository(
            api,
            FakeVault("must-stay-unread"),
            FakeInstallationId,
            logoutRevocations = FakeLogoutRevocations("old-refresh"),
            logoutRetryScheduler = scheduler,
        )

        val drained = repository.retryPendingLogoutRevocations()

        assertThat(drained).isFalse()
        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat(scheduler.calls).isAtLeast(1)
    }

    @Test
    fun retryDrainsEachIssuerQueueOnlyThroughItsMatchingApi() = runTest {
        val productionApi = FakeAccountApi()
        val productionQueue = FakeLogoutRevocations("production-refresh")
        val stagingApi = FakeAccountApi()
        val stagingQueue = FakeLogoutRevocations("staging-refresh")
        val repository = AuthRepository(
            productionApi,
            FakeVault(),
            FakeInstallationId,
            logoutRevocations = productionQueue,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(stagingApi, stagingQueue),
            ),
        )

        val drained = repository.retryPendingLogoutRevocations()

        assertThat(drained).isTrue()
        assertThat(productionApi.logoutRefreshTokens).containsExactly("production-refresh")
        assertThat(stagingApi.logoutRefreshTokens).containsExactly("staging-refresh")
        assertThat(productionQueue.tokens).isEmpty()
        assertThat(stagingQueue.tokens).isEmpty()
    }

    @Test
    fun productionRestoreDurablyTeardownsActiveStagingVaultWithoutRestoringIt() = runTest {
        val productionApi = FakeAccountApi()
        val stagingApi = FakeAccountApi()
        val stagingVault = FakeVault("active-staging-refresh")
        val stagingQueue = FakeLogoutRevocations()
        val stagingInstallationId = RecordingInstallationId()
        val repository = AuthRepository(
            productionApi,
            FakeVault(),
            FakeInstallationId,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    stagingApi,
                    stagingQueue,
                    previousIssuerVault = stagingVault,
                    previousIssuerInstallationId = stagingInstallationId,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingVault.value).isNull()
        assertThat(stagingApi.logoutRefreshTokens).containsExactly("active-staging-refresh")
        assertThat(stagingQueue.tokens).isEmpty()
        assertThat(stagingInstallationId.rotations).isEqualTo(1)
        assertThat(productionApi.refreshCalls).isEqualTo(0)
        assertThat(stagingApi.refreshCalls).isEqualTo(0)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun productionRestoreCancelsOnlyTheIssuerPairedStagingPendingSignup() = runTest {
        val productionApi = FakeAccountApi()
        val stagingApi = FakeAccountApi()
        val stagingStore = FakeSignupStore(pendingSignupOperation())
        val stagingVault = FakeVault()
        val stagingInstallation = RecordingInstallationId()
        val repository = AuthRepository(
            productionApi,
            FakeVault(),
            FakeInstallationId,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    api = stagingApi,
                    store = FakeLogoutRevocations(),
                    previousIssuerVault = stagingVault,
                    previousIssuerInstallationId = stagingInstallation,
                    previousIssuerSignupStore = stagingStore,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingApi.cancelCalls).isEqualTo(1)
        assertThat(productionApi.cancelCalls).isEqualTo(0)
        assertThat(stagingStore.value).isNull()
        assertThat(stagingVault.value).isNull()
        assertThat(stagingInstallation.rotations).isEqualTo(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun productionRestoreRevokesDurableVaultForAlreadyConfirmedStagingSignup() = runTest {
        val stagingApi = FakeAccountApi().apply {
            completeResult = FakeAccountApi.authenticated(
                refreshToken = "recovered-staging-refresh",
            )
            onCancel = { throw AccountApiException(409, "signup_already_confirmed") }
        }
        val stagingStore = FakeSignupStore(pendingSignupOperation("Durable Fern"))
        val stagingVault = FakeVault("provisional-staging-refresh")
        val stagingInstallation = RecordingInstallationId()
        val repository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    api = stagingApi,
                    store = FakeLogoutRevocations(),
                    previousIssuerVault = stagingVault,
                    previousIssuerInstallationId = stagingInstallation,
                    previousIssuerSignupStore = stagingStore,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingApi.completeCalls).isEqualTo(0)
        assertThat(stagingApi.logoutRefreshTokens).contains("provisional-staging-refresh")
        assertThat(stagingStore.value).isNull()
        assertThat(stagingVault.value).isNull()
        assertThat(stagingInstallation.rotations).isEqualTo(1)
    }

    @Test
    fun unreadablePreviousSignupStoreStillRevokesReadableIssuerVault() = runTest {
        val stagingApi = FakeAccountApi()
        val stagingStore = FakeSignupStore(pendingSignupOperation()).apply {
            readFailure = CredentialUnreadableException()
        }
        val stagingVault = FakeVault("active-staging-refresh")
        val scheduler = RecordingLogoutRetryScheduler()
        val repository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            logoutRetryScheduler = scheduler,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    api = stagingApi,
                    store = FakeLogoutRevocations(),
                    previousIssuerVault = stagingVault,
                    previousIssuerInstallationId = RecordingInstallationId(),
                    previousIssuerSignupStore = stagingStore,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingApi.logoutRefreshTokens).contains("active-staging-refresh")
        assertThat(stagingVault.value).isNull()
        assertThat(stagingStore.value).isNotNull()
        assertThat(scheduler.calls).isAtLeast(1)
    }

    @Test
    fun previousSignupMarkerSurvivesRotationOrVaultClearFailure() = runTest {
        val rotationStore = FakeSignupStore(pendingSignupOperation())
        val rotationVault = FakeVault("rotation-refresh")
        val failedInstallation = RecordingInstallationId().apply {
            rotateFailure = IOException("identity storage")
        }
        val rotationRepository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    api = FakeAccountApi(),
                    store = FakeLogoutRevocations(),
                    previousIssuerVault = rotationVault,
                    previousIssuerInstallationId = failedInstallation,
                    previousIssuerSignupStore = rotationStore,
                ),
            ),
        )

        rotationRepository.restore()

        assertThat(rotationStore.value).isNotNull()
        assertThat(rotationVault.value).isEqualTo("rotation-refresh")

        val clearStore = FakeSignupStore(pendingSignupOperation())
        val clearVault = FakeVault("clear-refresh").apply {
            clearFailure = IOException("keystore")
        }
        val clearRepository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    api = FakeAccountApi(),
                    store = FakeLogoutRevocations(),
                    previousIssuerVault = clearVault,
                    previousIssuerInstallationId = RecordingInstallationId(),
                    previousIssuerSignupStore = clearStore,
                ),
            ),
        )

        clearRepository.restore()

        assertThat(clearStore.value).isNotNull()
        assertThat(clearVault.value).isEqualTo("clear-refresh")
    }

    @Test
    fun previousIssuerVaultIsNotClearedUntilMatchingQueueWriteIsVerified() = runTest {
        val stagingVault = FakeVault("active-staging-refresh")
        val stagingQueue = FakeLogoutRevocations().apply {
            enqueueFailure = IOException("secure storage unavailable")
        }
        val scheduler = RecordingLogoutRetryScheduler()
        val stagingApi = FakeAccountApi()
        val stagingInstallationId = RecordingInstallationId()
        val repository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            logoutRetryScheduler = scheduler,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    stagingApi,
                    stagingQueue,
                    previousIssuerVault = stagingVault,
                    previousIssuerInstallationId = stagingInstallationId,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingVault.value).isEqualTo("active-staging-refresh")
        assertThat(stagingApi.logoutCalls).isEqualTo(0)
        assertThat(stagingInstallationId.rotations).isEqualTo(0)
        assertThat(scheduler.calls).isAtLeast(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun previousIssuerProofIsNotDrainedUntilScopedVaultIsCleared() = runTest {
        val stagingVault = FakeVault("active-staging-refresh").apply {
            clearFailure = IOException("keystore unavailable")
        }
        val stagingQueue = FakeLogoutRevocations()
        val scheduler = RecordingLogoutRetryScheduler()
        val stagingApi = FakeAccountApi()
        val repository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            logoutRetryScheduler = scheduler,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    stagingApi,
                    stagingQueue,
                    previousIssuerVault = stagingVault,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingVault.value).isEqualTo("active-staging-refresh")
        assertThat(stagingQueue.tokens).containsExactly("active-staging-refresh")
        assertThat(stagingApi.logoutCalls).isEqualTo(0)
        assertThat(scheduler.calls).isAtLeast(1)
    }

    @Test
    fun unreadablePreviousVaultDoesNotBlockOlderIssuerProofs() = runTest {
        val stagingVault = FakeVault().apply {
            readFailure = CredentialUnreadableException()
        }
        val stagingQueue = FakeLogoutRevocations("older-staging-refresh")
        val scheduler = RecordingLogoutRetryScheduler()
        val stagingApi = FakeAccountApi()
        val repository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            logoutRetryScheduler = scheduler,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    stagingApi,
                    stagingQueue,
                    previousIssuerVault = stagingVault,
                ),
            ),
        )

        repository.restore()

        assertThat(stagingApi.logoutRefreshTokens).containsExactly("older-staging-refresh")
        assertThat(stagingQueue.tokens).isEmpty()
        assertThat(scheduler.calls).isAtLeast(1)
    }

    @Test
    fun accountDisabledModeAlsoTeardownsPreviousIssuerVault() = runTest {
        val stagingVault = FakeVault("active-staging-refresh")
        val stagingQueue = FakeLogoutRevocations()
        val stagingApi = FakeAccountApi()
        val repository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
            additionalLogoutTargets = listOf(
                LogoutRevocationTarget(
                    stagingApi,
                    stagingQueue,
                    previousIssuerVault = stagingVault,
                ),
            ),
        )

        repository.enterAccountDisabledMode()

        assertThat(stagingVault.value).isNull()
        assertThat(stagingApi.logoutRefreshTokens).containsExactly("active-staging-refresh")
        assertThat(stagingQueue.tokens).isEmpty()
        assertThat(stagingApi.refreshCalls).isEqualTo(0)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun accountDisabledModeCancelsPendingAndInFlightPrimarySignups() = runTest {
        for (nickname in listOf(null, "In Flight Fern")) {
            val api = FakeAccountApi()
            val signupStore = FakeSignupStore(pendingSignupOperation(nickname))
            val vault = FakeVault(if (nickname == null) null else "provisional-refresh")
            val installation = RecordingInstallationId()
            val repository = AuthRepository(
                api,
                vault,
                installation,
                signupCompletions = signupStore,
            )

            repository.enterAccountDisabledMode()

            assertThat(api.cancelCalls).isEqualTo(1)
            assertThat(signupStore.value).isNull()
            assertThat(vault.value).isNull()
            assertThat(installation.rotations).isEqualTo(1)
            assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        }
    }

    @Test
    fun accountDisabledOfflineSignupCancelIsRetriedByRecoveryWorker() = runTest {
        val scheduler = RecordingLogoutRetryScheduler()
        val signupStore = FakeSignupStore(pendingSignupOperation())
        val installation = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onCancel = { throw IOException("offline") }
        }
        val repository = AuthRepository(
            api,
            FakeVault(),
            installation,
            logoutRetryScheduler = scheduler,
            signupCompletions = signupStore,
            accountUiEnabled = false,
        )

        val failure = runCatching { repository.enterAccountDisabledMode() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(signupStore.value).isNotNull()
        assertThat(scheduler.calls).isAtLeast(1)

        api.onCancel = {}
        val drained = repository.retryPendingLogoutRevocationsFromWorker()

        assertThat(drained).isTrue()
        assertThat(api.cancelCalls).isEqualTo(2)
        assertThat(signupStore.value).isNull()
        assertThat(installation.rotations).isEqualTo(1)
    }

    @Test
    fun normalWorkerNeverCancelsInteractiveSignup() = runTest {
        val signupStore = FakeSignupStore(pendingSignupOperation())
        val api = FakeAccountApi()
        val repository = repository(
            api,
            signupCompletions = signupStore,
            accountUiEnabled = true,
        )

        val drained = repository.retryPendingLogoutRevocationsFromWorker()

        assertThat(drained).isTrue()
        assertThat(api.cancelCalls).isEqualTo(0)
        assertThat(signupStore.value).isNotNull()
    }

    @Test
    fun recoveryWorkerRetriesLostConfirmedLogoutUsingDurableVaultFirst() = runTest {
        val signupStore = FakeSignupStore(pendingSignupOperation("Confirmed Fern"))
        val vault = FakeVault("durable-confirmed-refresh")
        val installation = RecordingInstallationId()
        var loseFirstResponse = true
        val api = FakeAccountApi().apply {
            onCancel = { throw AccountApiException(409, "signup_already_confirmed") }
            onLogout = {
                if (loseFirstResponse) {
                    loseFirstResponse = false
                    throw IOException("logout response lost")
                }
            }
        }
        val repository = repository(
            api,
            vault,
            installationId = installation,
            signupCompletions = signupStore,
            accountUiEnabled = false,
        )

        assertThat(repository.retryPendingLogoutRevocationsFromWorker()).isFalse()
        assertThat(signupStore.value).isNotNull()
        assertThat(vault.value).isEqualTo("durable-confirmed-refresh")

        assertThat(repository.retryPendingLogoutRevocationsFromWorker()).isTrue()

        assertThat(api.completeCalls).isEqualTo(0)
        assertThat(api.logoutRefreshTokens).containsExactly(
            "durable-confirmed-refresh",
            "durable-confirmed-refresh",
        ).inOrder()
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isNull()
        assertThat(installation.rotations).isEqualTo(1)
    }

    @Test
    fun recoveryWorkerConvergesAfterPostRevokeVaultClearFailure() = runTest {
        val signupStore = FakeSignupStore(pendingSignupOperation("Confirmed Fern"))
        val vault = FakeVault("durable-confirmed-refresh").apply {
            clearFailure = IOException("keystore busy")
        }
        val installation = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onCancel = { throw AccountApiException(409, "signup_already_confirmed") }
        }
        val repository = repository(
            api,
            vault,
            installationId = installation,
            signupCompletions = signupStore,
            accountUiEnabled = false,
        )

        assertThat(repository.retryPendingLogoutRevocationsFromWorker()).isFalse()
        assertThat(signupStore.value).isNotNull()
        assertThat(vault.value).isEqualTo("durable-confirmed-refresh")

        vault.clearFailure = null
        assertThat(repository.retryPendingLogoutRevocationsFromWorker()).isTrue()

        assertThat(api.completeCalls).isEqualTo(0)
        // The durable remoteTerminationConfirmed marker prevents a second
        // network revocation after only local vault erasure failed.
        assertThat(api.logoutRefreshTokens).containsExactly("durable-confirmed-refresh")
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isNull()
        assertThat(installation.rotations).isEqualTo(2)
    }

    @Test
    fun recoveryWorkerFinishesLocalCleanupAfterFinalMarkerClearFailure() = runTest {
        val signupStore = FakeSignupStore(pendingSignupOperation("Confirmed Fern")).apply {
            clearFailure = IOException("marker storage busy")
        }
        val vault = FakeVault("durable-confirmed-refresh")
        val installation = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onCancel = { throw AccountApiException(409, "signup_already_confirmed") }
        }
        val repository = repository(
            api,
            vault,
            installationId = installation,
            signupCompletions = signupStore,
            accountUiEnabled = false,
        )

        assertThat(repository.retryPendingLogoutRevocationsFromWorker()).isFalse()
        assertThat(signupStore.value?.remoteTerminationConfirmed).isTrue()
        assertThat(vault.value).isNull()

        signupStore.clearFailure = null
        assertThat(repository.retryPendingLogoutRevocationsFromWorker()).isTrue()

        assertThat(api.cancelCalls).isEqualTo(1)
        assertThat(api.logoutRefreshTokens).containsExactly("durable-confirmed-refresh")
        assertThat(signupStore.value).isNull()
        assertThat(installation.rotations).isEqualTo(2)
    }

    @Test
    fun recoveryWorkerDoesNotTreatPrunedConfirmedMarkerWithoutVaultAsTerminated() = runTest {
        val signupStore = FakeSignupStore(
            pendingSignupOperation("Confirmed Fern").copy(
                confirmationAttempted = true,
            ),
        )
        val installation = RecordingInstallationId()
        val api = FakeAccountApi().apply {
            onCancel = { throw AccountApiException(401, "pending_signup_expired") }
        }
        val repository = repository(
            api,
            FakeVault(),
            installationId = installation,
            signupCompletions = signupStore,
            accountUiEnabled = false,
        )

        val drained = repository.retryPendingLogoutRevocationsFromWorker()

        assertThat(drained).isFalse()
        assertThat(signupStore.value).isNotNull()
        assertThat(signupStore.value?.remoteTerminationConfirmed).isFalse()
        assertThat(installation.rotations).isEqualTo(0)
        assertThat(api.logoutCalls).isEqualTo(0)
    }

    @Test
    fun accountDisabledModeRevokesDurableVaultForAlreadyConfirmedPrimarySignup() = runTest {
        val api = FakeAccountApi().apply {
            completeResult = FakeAccountApi.authenticated(refreshToken = "confirmed-refresh")
            onCancel = { throw AccountApiException(409, "signup_already_confirmed") }
        }
        val signupStore = FakeSignupStore(pendingSignupOperation("Confirmed Fern"))
        val vault = FakeVault("provisional-refresh")
        val installation = RecordingInstallationId()
        val repository = AuthRepository(
            api,
            vault,
            installation,
            signupCompletions = signupStore,
        )

        repository.enterAccountDisabledMode()

        assertThat(api.completeCalls).isEqualTo(0)
        assertThat(api.logoutRefreshTokens).containsExactly("provisional-refresh")
        assertThat(signupStore.value).isNull()
        assertThat(vault.value).isNull()
        assertThat(installation.rotations).isEqualTo(1)
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun accountDisabledModeDrainsLogoutQueueWithoutRestoringCredential() = runTest {
        val api = FakeAccountApi()
        val vault = FakeVault("must-stay-unread")
        val revocations = FakeLogoutRevocations("old-refresh")
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            logoutRevocations = revocations,
        )

        repository.enterAccountDisabledMode()

        assertThat(api.logoutCalls).isEqualTo(2)
        assertThat(api.logoutRefreshTokens).containsExactly(
            "must-stay-unread",
            "old-refresh",
        ).inOrder()
        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat(vault.value).isNull()
        assertThat(revocations.tokens).isEmpty()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun cancelledStartupLogoutRetryDoesNotLeaveRepositoryRestoring() = runTest {
        val api = FakeAccountApi().apply {
            onLogout = { throw CancellationException("screen closed") }
        }
        val repository = AuthRepository(
            api,
            FakeVault("must-stay-unread"),
            FakeInstallationId,
            logoutRevocations = FakeLogoutRevocations("old-refresh"),
        )

        val failure = runCatching { repository.restore() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(CancellationException::class.java)
        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat(repository.state.value).isEqualTo(AuthState.Unavailable())
    }

    @Test
    fun accountDisabledModeBecomesGuestEvenWhenLogoutRetryIsCancelled() = runTest {
        val api = FakeAccountApi().apply {
            onLogout = { throw CancellationException("application stopping") }
        }
        val vault = FakeVault("must-stay-unread")
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            logoutRevocations = FakeLogoutRevocations("old-refresh"),
        )

        val failure = runCatching { repository.enterAccountDisabledMode() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(CancellationException::class.java)
        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun unreadableAuthenticatedVaultRevokesCapturedCurrentSession() = runTest {
        val api = FakeAccountApi()
        val vault = FakeVault()
        val installationId = RecordingInstallationId()
        val repository = repository(api, vault, installationId = installationId)
        repository.signIn(AuthProvider.YANDEX, "provider-token")
        vault.readFailure = CredentialUnreadableException()

        repository.logout()

        assertThat(api.logoutCalls).isEqualTo(0)
        assertThat(api.revokeSessionCalls).isEqualTo(1)
        assertThat(api.lastRevokedAccessToken).isEqualTo("access-1")
        assertThat(api.lastRevokedSessionId).isEqualTo(FakeAccountApi.SESSION_ID)
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(installationId.rotations).isEqualTo(1)
    }

    @Test
    fun unreadableVaultStillReportsNotDurableWhenSessionRevocationFails() = runTest {
        val api = FakeAccountApi().apply {
            revokeSessionFailure = IOException("offline")
        }
        val vault = FakeVault()
        val repository = repository(api, vault)
        repository.signIn(AuthProvider.YANDEX, "provider-token")
        vault.readFailure = CredentialUnreadableException()

        val failure = runCatching { repository.logout() }.exceptionOrNull()

        assertThat(failure).isInstanceOf(LogoutNotDurableException::class.java)
        assertThat(api.logoutCalls).isEqualTo(0)
        assertThat(api.revokeSessionCalls).isEqualTo(1)
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun unresolvedLogoutCredentialIsNeverRestoredAsAuthenticated() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val vault = FakeVault("old-refresh")
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            logoutRevocations = FakeLogoutRevocations("old-refresh"),
        )

        repository.restore()

        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat(vault.value).isNull()
        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
    }

    @Test
    fun unresolvedLogoutStillBlocksRestoreAfterMoreThanThreeClearFailures() = runTest {
        val api = FakeAccountApi().apply { logoutFailure = IOException("offline") }
        val vault = FakeVault("old-refresh").apply { clearFailure = IOException("disk") }
        val revocations = FakeLogoutRevocations("old-refresh")
        val repository = AuthRepository(
            api,
            vault,
            FakeInstallationId,
            logoutRevocations = revocations,
        )

        repeat(5) { repository.restore() }

        assertThat(api.logoutCalls).isEqualTo(5)
        assertThat(api.refreshCalls).isEqualTo(0)
        assertThat(vault.value).isEqualTo("old-refresh")
        assertThat(revocations.tokens).containsExactly("old-refresh")
        assertThat(revocations.failures).isEqualTo(5)
        assertThat(repository.state.value).isInstanceOf(AuthState.Unavailable::class.java)
    }

    @Test
    fun stalledLogoutIsBoundedAndLeavesRevocationQueued() = runTest {
        val api = FakeAccountApi().apply { onLogout = { awaitCancellation() } }
        val revocations = FakeLogoutRevocations()
        val repository = AuthRepository(
            api,
            FakeVault(),
            FakeInstallationId,
            logoutRevocations = revocations,
        )
        repository.signIn(AuthProvider.YANDEX, "provider-token")

        repository.logout()

        assertThat(repository.state.value).isEqualTo(AuthState.Guest)
        assertThat(revocations.tokens).containsExactly("refresh-1")
        assertThat(revocations.failures).isEqualTo(1)
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

    @Test
    fun pushRegistrationCannotCrossAnAccountSwitchAfterAsyncTokenFetch() = runTest {
        val api = FakeAccountApi()
        val repository = repository(api)
        repository.signIn(AuthProvider.YANDEX, "old-provider-token")
        val oldSessionId = (repository.state.value as AuthState.Authenticated).session.sessionId
        api.exchangeResult = FakeAccountApi.authenticated(
            profile = FakeAccountApi.profile("New Gardener"),
            session = FakeAccountApi.session().copy(
                sessionId = "00000000-0000-4000-8000-000000000099",
            ),
        )
        repository.signIn(AuthProvider.VK, "new-provider-token")

        val result = repository.registerPush("late-fcm-token", oldSessionId)

        assertThat(result.isFailure).isTrue()
        assertThat(api.registerPushCalls).isEqualTo(0)
    }

    @Test
    fun pushOptOutWaitsForInFlightRegistrationAndFinishesDisabled() = runTest {
        val registrationStarted = CompletableDeferred<Unit>()
        val releaseRegistration = CompletableDeferred<Unit>()
        val events = mutableListOf<String>()
        val api = FakeAccountApi().apply {
            onRegisterPush = {
                registrationStarted.complete(Unit)
                releaseRegistration.await()
                events += "register"
            }
            onDisablePush = { events += "disable" }
        }
        val repository = repository(api)
        repository.signIn(AuthProvider.YANDEX, "provider-token")

        val registration = async { repository.registerPush("fcm-token") }
        registrationStarted.await()
        val optOut = async { repository.disablePush() }
        assertThat(api.disablePushCalls).isEqualTo(0)
        releaseRegistration.complete(Unit)

        assertThat(registration.await().isSuccess).isTrue()
        assertThat(optOut.await().isSuccess).isTrue()
        assertThat(events).containsExactly("register", "disable").inOrder()
    }

    @Test
    fun queuedPushRegistrationRechecksOptInInsideMutationLock() = runTest {
        val firstRegistrationStarted = CompletableDeferred<Unit>()
        val releaseFirstRegistration = CompletableDeferred<Unit>()
        val api = FakeAccountApi().apply {
            onRegisterPush = {
                if (registerPushCalls == 1) {
                    firstRegistrationStarted.complete(Unit)
                    releaseFirstRegistration.await()
                }
            }
        }
        val repository = repository(api)
        repository.signIn(AuthProvider.YANDEX, "provider-token")
        var registrationAllowed = true
        val first = async { repository.registerPush("first-token") }
        firstRegistrationStarted.await()
        val stale = async {
            repository.registerPush("stale-token") { registrationAllowed }
        }
        runCurrent()

        registrationAllowed = false
        val optOut = async { repository.disablePush() }
        releaseFirstRegistration.complete(Unit)

        assertThat(first.await().isSuccess).isTrue()
        assertThat(stale.await().isSuccess).isTrue()
        assertThat(optOut.await().isSuccess).isTrue()
        assertThat(api.registerPushCalls).isEqualTo(1)
        assertThat(api.lastPushToken).isEqualTo("first-token")
        assertThat(api.disablePushCalls).isEqualTo(1)
    }

    private fun pendingSignupOperation(nickname: String? = null) = PendingSignupOperation(
        provider = AuthProvider.YANDEX,
        pendingSignupToken = "pending-token",
        completionId = "00000000-0000-4000-8000-000000000011",
        installationId = FakeAccountApi.INSTALLATION_ID,
        expiresInSeconds = 600,
        nickname = nickname,
    )

    private fun repository(
        api: FakeAccountApi,
        vault: FakeVault = FakeVault(),
        revocations: LogoutRevocationStore = NoOpLogoutRevocationStore,
        installationId: InstallationIdProvider = FakeInstallationId,
        additionalLogoutTargets: List<LogoutRevocationTarget> = emptyList(),
        signupCompletions: SignupCompletionStore = InMemorySignupCompletionStore(),
        accountUiEnabled: Boolean = true,
    ) = AuthRepository(
        api,
        vault,
        installationId,
        logoutRevocations = revocations,
        additionalLogoutTargets = additionalLogoutTargets,
        signupCompletions = signupCompletions,
        accountUiEnabled = accountUiEnabled,
    )

    private class FakeSignupStore(
        var value: PendingSignupOperation? = null,
    ) : SignupCompletionStore {
        var readFailure: Exception? = null
        var writeFailure: Exception? = null
        var clearFailure: Exception? = null

        override fun read(): PendingSignupOperation? {
            readFailure?.let { throw it }
            return value
        }

        override fun write(operation: PendingSignupOperation) {
            writeFailure?.let { throw it }
            value = operation
        }

        override fun clear() {
            clearFailure?.let { throw it }
            value = null
        }
    }

    private class FakeVault(initial: String? = null) : RefreshTokenVault {
        var value: String? = initial
        var clearFailure: Exception? = null
        var readFailure: Exception? = null
        var writeFailure: Exception? = null
        private var pendingRotationId: String? = null

        override fun read(): String? {
            readFailure?.let { throw it }
            return value
        }

        override fun prepareRefresh(): PreparedRefresh? = value?.let { token ->
            val rotationId = pendingRotationId ?: TEST_ROTATION_ID.also { pendingRotationId = it }
            PreparedRefresh(token, rotationId)
        }

        override fun write(token: String) {
            writeFailure?.let { throw it }
            value = token
            pendingRotationId = null
        }

        override fun clear() {
            clearFailure?.let { throw it }
            value = null
            pendingRotationId = null
        }

        private companion object {
            const val TEST_ROTATION_ID = "00000000-0000-4000-8000-000000000010"
        }
    }

    private data object FakeInstallationId : InstallationIdProvider {
        override fun get(): String = FakeAccountApi.INSTALLATION_ID

        override fun rotate() = Unit
    }

    private class RecordingInstallationId : InstallationIdProvider {
        var rotations = 0
        var rotateFailure: Exception? = null

        override fun get(): String = if (rotations == 0) FakeAccountApi.INSTALLATION_ID else ROTATED_ID

        override fun rotate() {
            rotateFailure?.let { throw it }
            rotations++
        }

        companion object {
            const val ROTATED_ID = "00000000-0000-4000-8000-000000000004"
        }
    }

    private class RecordingLogoutRetryScheduler : LogoutRetryScheduler {
        var calls = 0

        override fun schedule() {
            calls++
        }
    }

    private class FakeLogoutRevocations(vararg initial: String) : LogoutRevocationStore {
        val tokens = linkedSetOf(*initial)
        var failures = 0
        var enqueueFailure: Exception? = null

        override fun enqueue(refreshToken: String) {
            enqueueFailure?.let { throw it }
            tokens += refreshToken
        }

        override fun next(): PendingLogoutRevocation? =
            tokens.firstOrNull()?.let(::PendingLogoutRevocation)

        override fun isPending(refreshToken: String): Boolean = refreshToken in tokens

        override fun complete(refreshToken: String) {
            tokens -= refreshToken
        }

        override fun recordFailure(refreshToken: String) {
            failures++
        }
    }
}
