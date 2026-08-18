package com.plantdiseases.app.data.account

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.testutil.FakeAccountApi
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test

class AccountApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: AccountApiClient

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        api = AccountApiClient(server.url("/id/").toString())
    }

    @After
    fun teardown() {
        server.shutdown()
    }

    @Test
    fun exchangeSendsNoBearerOrHardware_andParsesProfileRequired() = runBlocking {
        server.enqueue(
            jsonResponse(
                """
                {
                    "status":"profile_required",
                    "provider":"yandex",
                    "pending_signup_token":"pending",
                    "expires_in":600
                }
                """.trimIndent(),
            ),
        )

        val result = api.exchange(AuthProvider.YANDEX, "synthetic-token", FakeAccountApi.INSTALLATION_ID)

        assertThat(result).isInstanceOf(ExchangeResult.ProfileRequired::class.java)
        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/id/v1/auth/yandex/exchange")
        assertThat(request.getHeader("Authorization")).isNull()
        assertThat(request.getHeader("User-Agent")).isEqualTo("PlantDiseases-Android")
        val body = request.body.readUtf8()
        assertThat(body).contains("synthetic-token")
        assertThat(body).contains(FakeAccountApi.INSTALLATION_ID)
        assertThat(body.lowercase()).doesNotContain("model")
        assertThat(body.lowercase()).doesNotContain("manufacturer")
    }

    @Test
    fun protectedProfileRequestCarriesBearerOnlyToAccountOrigin() = runBlocking {
        server.enqueue(
            jsonResponse(
                """
                {
                    "nickname":"Beta",
                    "community_actor_id":"${FakeAccountApi.ACTOR_ID}",
                    "primary_provider":{
                        "provider":"vk",
                        "display_name":"Synthetic VK",
                        "avatar_url":null,
                        "refreshed_at":null
                    },
                    "linked_providers":[],
                    "nickname_changed_at":null,
                    "created_at":"2026-01-01T00:00:00Z",
                    "updated_at":"2026-01-01T00:00:00Z",
                    "state":"active"
                }
                """.trimIndent(),
            ),
        )

        val profile = api.me("account-access-token")

        assertThat(profile.nickname).isEqualTo("Beta")
        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/id/v1/me")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer account-access-token")
    }

    @Test
    fun stableErrorCodeIsPreserved() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(401)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"error":{"code":"refresh_expired"}}"""),
        )

        val failure = runCatching {
            api.refresh("expired-refresh", "00000000-0000-4000-8000-000000000010")
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(AccountApiException::class.java)
        assertThat((failure as AccountApiException).statusCode).isEqualTo(401)
        assertThat(failure.errorCode).isEqualTo("refresh_expired")
        val request = server.takeRequest()
        assertThat(request.getHeader("Authorization")).isNull()
        assertThat(request.body.readUtf8()).isEqualTo(
            """{"refresh_token":"expired-refresh","rotation_id":"00000000-0000-4000-8000-000000000010"}""",
        )
    }

    @Test
    fun logoutUsesRefreshProofWithoutBearerHeader() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(204))

        api.logout("refresh-proof")

        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/id/v1/auth/logout")
        assertThat(request.getHeader("Authorization")).isNull()
        assertThat(request.body.readUtf8()).isEqualTo("{\"refresh_token\":\"refresh-proof\"}")
    }

    @Test
    fun credentialedRedirectIsRejectedWithoutContactingRedirectOrigin() = runBlocking {
        val redirectOrigin = MockWebServer()
        redirectOrigin.start()
        try {
            server.enqueue(
                MockResponse()
                    .setResponseCode(307)
                    .setHeader("Location", redirectOrigin.url("/collect")),
            )

            val failure = runCatching { api.logout("must-not-leave-account-origin") }
                .exceptionOrNull()

            assertThat(failure).isInstanceOf(AccountApiException::class.java)
            assertThat(server.requestCount).isEqualTo(1)
            assertThat(redirectOrigin.requestCount).isEqualTo(0)
        } finally {
            redirectOrigin.shutdown()
        }
    }

    @Test
    fun oneUseAccountMutationIsNotAutomaticallyRetriedOn503() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(503)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"error":{"code":"temporarily_unavailable"}}"""),
        )
        server.enqueue(jsonResponse("""{"status":"authenticated"}"""))

        val failure = runCatching {
            api.completeSignup(
                "one-use-pending",
                "00000000-0000-4000-8000-000000000011",
                "Beta Gardener",
                FakeAccountApi.INSTALLATION_ID,
            )
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(AccountApiException::class.java)
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun signupCompletionCarriesTheSameLogicalIdentityThroughCompleteAndConfirm() = runBlocking {
        val completionId = "00000000-0000-4000-8000-000000000011"
        server.enqueue(
            jsonResponse(
                """
                {
                    "status":"confirmation_required",
                    "access_token":"access",
                    "refresh_token":"refresh",
                    "token_type":"Bearer",
                    "expires_in":900,
                    "profile":{
                        "nickname":"Beta Gardener",
                        "community_actor_id":"${FakeAccountApi.ACTOR_ID}"
                    },
                    "session":{
                        "session_id":"${FakeAccountApi.SESSION_ID}",
                        "installation_id":"${FakeAccountApi.INSTALLATION_ID}",
                        "device_label":"Android device",
                        "created_at":"2026-01-01T00:00:00Z",
                        "last_used_at":"2026-01-01T00:00:00Z",
                        "idle_expires_at":"2026-02-01T00:00:00Z",
                        "absolute_expires_at":"2026-03-01T00:00:00Z",
                        "current":true
                    }
                }
                """.trimIndent(),
            ),
        )
        server.enqueue(MockResponse().setResponseCode(204))

        api.completeSignup(
            "pending-proof",
            completionId,
            "Beta Gardener",
            FakeAccountApi.INSTALLATION_ID,
        )
        api.confirmSignup(
            "pending-proof",
            completionId,
            FakeAccountApi.INSTALLATION_ID,
        )

        val complete = server.takeRequest()
        assertThat(complete.path).isEqualTo("/id/v1/auth/signup/complete")
        assertThat(complete.body.readUtf8()).isEqualTo(
            """{"pending_signup_token":"pending-proof","completion_id":"$completionId","nickname":"Beta Gardener","installation_id":"${FakeAccountApi.INSTALLATION_ID}"}""",
        )
        val confirm = server.takeRequest()
        assertThat(confirm.path).isEqualTo("/id/v1/auth/signup/confirm")
        assertThat(confirm.body.readUtf8()).isEqualTo(
            """{"pending_signup_token":"pending-proof","completion_id":"$completionId","installation_id":"${FakeAccountApi.INSTALLATION_ID}"}""",
        )
    }

    @Test
    fun authResponseStatusCannotBeConfusedAcrossEndpoints() = runBlocking {
        val completionId = "00000000-0000-4000-8000-000000000011"
        server.enqueue(jsonResponse(authResponseBody("confirmation_required")))
        val exchangeFailure = runCatching {
            api.exchange(
                AuthProvider.YANDEX,
                "provider-proof",
                FakeAccountApi.INSTALLATION_ID,
            )
        }.exceptionOrNull()

        server.enqueue(jsonResponse(authResponseBody("authenticated")))
        val completionFailure = runCatching {
            api.completeSignup(
                "pending-proof",
                completionId,
                "Beta Gardener",
                FakeAccountApi.INSTALLATION_ID,
            )
        }.exceptionOrNull()

        server.enqueue(jsonResponse(authResponseBody("confirmation_required")))
        val refreshFailure = runCatching {
            api.refresh("refresh", completionId)
        }.exceptionOrNull()

        assertThat((exchangeFailure as AccountApiException).errorCode)
            .isEqualTo("invalid_auth_response")
        assertThat((completionFailure as AccountApiException).errorCode)
            .isEqualTo("invalid_signup_completion_response")
        assertThat((refreshFailure as AccountApiException).errorCode)
            .isEqualTo("invalid_auth_response")
    }

    @Test
    fun oneUseAccountMutationDisablesOkHttpConnectionRetries() {
        val field = AccountApiClient::class.java.getDeclaredField("okHttp")
            .apply { isAccessible = true }
        val transport = field.get(api) as OkHttpClient

        assertThat(transport.retryOnConnectionFailure).isFalse()
    }

    @Test
    fun credentialRequestIsNeverFollowedToRedirectTarget() = runBlocking {
        val redirectTarget = MockWebServer()
        redirectTarget.start()
        try {
            redirectTarget.enqueue(
                jsonResponse(
                    """{"status":"authenticated","access_token":"escaped","refresh_token":"escaped"}""",
                ),
            )
            server.enqueue(
                MockResponse().setResponseCode(307)
                    .setHeader("Location", redirectTarget.url("/capture")),
            )

            val failure = runCatching {
                api.refresh(
                    "private-refresh-token",
                    "00000000-0000-4000-8000-000000000012",
                )
            }.exceptionOrNull()

            assertThat(failure).isInstanceOf(AccountApiException::class.java)
            assertThat((failure as AccountApiException).statusCode).isEqualTo(307)
            assertThat(server.requestCount).isEqualTo(1)
            assertThat(redirectTarget.requestCount).isEqualTo(0)
        } finally {
            redirectTarget.shutdown()
        }
    }

    @Test
    fun pushRegistrationIsPrivateAndContainsNoHardwareFields() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(204))

        api.registerPush("account-access-token", "synthetic-fcm-token")

        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/id/v1/push/device")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer account-access-token")
        val body = request.body.readUtf8()
        assertThat(body).contains("\"fcm_token\":\"synthetic-fcm-token\"")
        assertThat(body).contains("\"platform\":\"android\"")
        for (forbidden in listOf("model", "manufacturer", "brand", "device", "product")) {
            assertThat(body.lowercase()).doesNotContain("\"$forbidden\"")
        }
    }

    private fun jsonResponse(body: String): MockResponse = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private fun authResponseBody(status: String) =
        """
        {
            "status":"$status",
            "access_token":"access",
            "refresh_token":"refresh",
            "token_type":"Bearer",
            "expires_in":900,
            "profile":{
                "nickname":"Beta Gardener",
                "community_actor_id":"${FakeAccountApi.ACTOR_ID}"
            },
            "session":{
                "session_id":"${FakeAccountApi.SESSION_ID}",
                "installation_id":"${FakeAccountApi.INSTALLATION_ID}",
                "device_label":"Android device",
                "created_at":"2026-01-01T00:00:00Z",
                "last_used_at":"2026-01-01T00:00:00Z",
                "idle_expires_at":"2026-02-01T00:00:00Z",
                "absolute_expires_at":"2026-03-01T00:00:00Z",
                "current":true
            }
        }
        """.trimIndent()
}
