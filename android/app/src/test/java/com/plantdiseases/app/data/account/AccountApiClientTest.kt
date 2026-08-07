package com.plantdiseases.app.data.account

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.testutil.FakeAccountApi
import kotlinx.coroutines.runBlocking
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

        val failure = runCatching { api.refresh("expired-refresh") }.exceptionOrNull()

        assertThat(failure).isInstanceOf(AccountApiException::class.java)
        assertThat((failure as AccountApiException).statusCode).isEqualTo(401)
        assertThat(failure.errorCode).isEqualTo("refresh_expired")
        assertThat(server.takeRequest().getHeader("Authorization")).isNull()
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
            api.completeSignup("one-use-pending", "Beta Gardener", FakeAccountApi.INSTALLATION_ID)
        }.exceptionOrNull()

        assertThat(failure).isInstanceOf(AccountApiException::class.java)
        assertThat(server.requestCount).isEqualTo(1)
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
}
