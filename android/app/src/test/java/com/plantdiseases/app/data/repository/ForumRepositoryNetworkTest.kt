package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.data.model.ForumPostCreateRequest
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.remote.RetryInterceptor
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * The forum client against a real [MockWebServer]: guest reads, query
 * parameters, error-code mapping and the signed-in requirement for writes.
 */
class ForumRepositoryNetworkTest {

    private lateinit var server: MockWebServer
    private lateinit var repo: ForumRepository

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        val client = PlantApiClient(
            server.url("/").toString(),
            retryInterceptor = RetryInterceptor(maxRetries = 0, initialDelayMs = 1, sleeper = {}),
        )
        repo = ForumRepository(client, authRepository = null)
    }

    @After
    fun teardown() {
        server.shutdown()
    }

    private fun json(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    @Test
    fun guestFeedDecodesCardsAndSendsNoAuthorization() = runBlocking {
        server.enqueue(
            json(
                """
                {"items":[{"id":"fp0123456789abcdef0123456789ab","kind":"scan","space":"succulents",
                  "title":"","body":"","author":{"nickname":"Fern","author_token":"ca1","is_me":false},
                  "ai":{"disease":"powdery_mildew","confidence":0.71,"species":null},
                  "media":[{"media_id":"fm0","width":800,"height":600}],"media_count":1,
                  "like_count":3,"answer_count":0,"liked_by_me":false,"solved":false,
                  "created_at":1800000000,"last_activity_at":1800000000,"unknown_field":true}],
                 "next_cursor":"bzoyMA"}
                """.trimIndent(),
            ),
        )
        val page = repo.feed(tab = "needs_help", sort = "hot", space = "succulents").getOrThrow()

        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/api/community/feed?tab=needs_help&sort=hot&space=succulents")
        assertThat(request.getHeader("Authorization")).isNull()
        assertThat(page.nextCursor).isEqualTo("bzoyMA")
        val post = page.items.single()
        assertThat(post.kind).isEqualTo("scan")
        assertThat(post.ai?.disease).isEqualTo("powdery_mildew")
        assertThat(post.media.single().mediaId).isEqualTo("fm0")
        assertThat(post.author.authorToken).isEqualTo("ca1")
    }

    @Test
    fun searchSendsOnlyTheFiltersThatAreSet() = runBlocking {
        server.enqueue(json("""{"items":[],"terms":["white","fuzz"]}"""))
        val page = repo.search(
            ForumRepository.SearchQuery(text = " white fuzz ", solvedOnly = true, condition = "pests"),
        ).getOrThrow()

        val path = server.takeRequest().path.orEmpty()
        assertThat(path).startsWith("/api/community/search?")
        assertThat(path).contains("q=white%20fuzz")
        assertThat(path).contains("solved=true")
        assertThat(path).contains("condition=pests")
        assertThat(path).doesNotContain("with_photos")
        assertThat(path).doesNotContain("species")
        assertThat(page.terms).containsExactly("white", "fuzz").inOrder()
    }

    @Test
    fun refusalsCarryTheServerErrorCode() = runBlocking {
        server.enqueue(json("""{"detail":"post_not_found"}""", code = 404))
        val error = repo.post("fp0123456789abcdef0123456789ab").exceptionOrNull()

        assertThat(error).isInstanceOf(ForumRepository.ForumException::class.java)
        error as ForumRepository.ForumException
        assertThat(error.status).isEqualTo(404)
        assertThat(error.code).isEqualTo("post_not_found")
    }

    @Test
    fun writesRequireAnAccount() = runBlocking {
        val result = repo.createPost(
            ForumPostCreateRequest(
                kind = "tip",
                title = "Bottom watering",
                body = "Soak for 20 minutes",
                space = null,
                species = null,
                condition = null,
                urgent = false,
                mediaIds = emptyList(),
                scanToken = null,
                parentPostId = null,
                trainingConsent = false,
                locale = "en",
            ),
        )
        assertThat(result.exceptionOrNull()).isInstanceOf(AuthenticationRequiredException::class.java)
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun mediaUrlsUseTheApiOrigin() {
        assertThat(repo.mediaUrl("fmabc")).isEqualTo("${server.url("/")}api/community/media/fmabc")
    }
}
