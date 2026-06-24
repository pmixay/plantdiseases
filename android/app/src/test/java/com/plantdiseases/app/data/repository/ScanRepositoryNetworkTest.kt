package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.SymptomDiagnoseRequest
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.testutil.FakeScanDao
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * Integration coverage of the ML-diagnosis network hop driven against a
 * real [MockWebServer] (so OkHttp/Ktor request building, multipart
 * upload, the RetryInterceptor and the HTTP-status → typed-exception
 * mapping are all exercised) — without a device. Enabled by the
 * [PlantApiClient] base-URL test seam.
 *
 * `analyzeImage` has moved to the Ktor client (step 2). Ktor's
 * ContentNegotiation only deserializes a response whose `Content-Type` is
 * `application/json`, so success mocks declare it explicitly — the real
 * server already sends it; Gson never required it.
 */
class ScanRepositoryNetworkTest {

    private lateinit var server: MockWebServer
    private lateinit var repo: ScanRepository
    private lateinit var imageFile: File

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        repo = ScanRepository(FakeScanDao(), PlantApiClient(server.url("/").toString()))
        imageFile = File.createTempFile("upload", ".jpg")
        imageFile.writeBytes(ByteArray(128) { it.toByte() })
    }

    @After
    fun teardown() {
        server.shutdown()
        imageFile.delete()
    }

    @Test
    fun analyze_success_parsesBodyAndBuildsMultipartPost() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":0.9}""",
                ),
        )

        val result = runBlocking { repo.analyzeImage(imageFile, locale = "en") }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.diseaseName).isEqualTo("Leaf Spot")

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).startsWith("/api/analyze")
        assertThat(request.path).contains("locale=en")
        assertThat(request.getHeader("Content-Type")).contains("multipart/form-data")
    }

    @Test
    fun analyze_includesSpeciesHintQuery_whenProvided() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"disease_name":"Leaf Spot"}"""),
        )
        runBlocking { repo.analyzeImage(imageFile, speciesHint = "Ficus elastica", locale = "ru") }
        val request = server.takeRequest()
        assertThat(request.path).contains("species=Ficus")
        assertThat(request.path).contains("locale=ru")
    }

    @Test
    fun analyze_400_mapsToUnsupportedImage() {
        server.enqueue(MockResponse().setResponseCode(400))
        val result = runBlocking { repo.analyzeImage(imageFile) }
        assertThat(result.isFailure).isTrue()
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.UnsupportedImageException::class.java)
    }

    @Test
    fun analyze_415_mapsToUnsupportedImage() {
        server.enqueue(MockResponse().setResponseCode(415))
        val result = runBlocking { repo.analyzeImage(imageFile) }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.UnsupportedImageException::class.java)
    }

    @Test
    fun analyze_429_mapsToRateLimit() {
        // POST retries an explicit 429 once, so enqueue two.
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(MockResponse().setResponseCode(429))
        val result = runBlocking { repo.analyzeImage(imageFile) }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.RateLimitException::class.java)
    }

    @Test
    fun analyze_503_mapsToServerStarting() {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(503))
        val result = runBlocking { repo.analyzeImage(imageFile) }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.ServerStartingException::class.java)
    }

    @Test
    fun analyze_500_mapsToGenericFailure() {
        server.enqueue(MockResponse().setResponseCode(500))
        val result = runBlocking { repo.analyzeImage(imageFile) }
        assertThat(result.isFailure).isTrue()
        // Not one of the typed exceptions — a generic "Server error" carrier.
        val ex = result.exceptionOrNull()!!
        assertThat(ex).isNotInstanceOf(ScanRepository.UnsupportedImageException::class.java)
        assertThat(ex.message).contains("Server error")
    }

    // --- GET /api/capabilities (Ktor) ---

    @Test
    fun capabilities_success_parsesFamiliesAndGets() {
        // Extra unknown field ("note") proves ignoreUnknownKeys is on.
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"default":"gamma","note":"x","families":""" +
                        """[{"id":"gamma","available":true,"location":"server"},""" +
                        """{"id":"frontier","available":false}]}""",
                ),
        )

        val result = runBlocking { repo.fetchCapabilities() }

        assertThat(result.isSuccess).isTrue()
        val body = result.getOrNull()!!
        assertThat(body.default).isEqualTo("gamma")
        assertThat(body.families).hasSize(2)
        assertThat(body.families[0].available).isTrue()

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("GET")
        assertThat(request.path).isEqualTo("/api/capabilities")
    }

    @Test
    fun capabilities_500_mapsToGenericFailure() {
        // 500 isn't retried (RetryInterceptor only replays 429/503), so one response.
        server.enqueue(MockResponse().setResponseCode(500))
        val result = runBlocking { repo.fetchCapabilities() }
        assertThat(result.isFailure).isTrue()
        assertThat(result.exceptionOrNull()?.message).contains("Server error")
    }

    // --- GET /api/health (Ktor) ---

    @Test
    fun health_success_parsesBodyAndGets() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"status":"ok","version":"1.4","num_classes":9}"""),
        )

        val result = runBlocking { repo.checkServerHealth() }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.status).isEqualTo("ok")
        assertThat(result.getOrNull()?.numClasses).isEqualTo(9)

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("GET")
        assertThat(request.path).isEqualTo("/api/health")
    }

    @Test
    fun health_500_mapsToGenericFailure() {
        server.enqueue(MockResponse().setResponseCode(500))
        val result = runBlocking { repo.checkServerHealth() }
        assertThat(result.isFailure).isTrue()
        assertThat(result.exceptionOrNull()?.message).contains("Server error")
    }

    // --- POST /api/symptom-diagnose (Ktor) ---

    @Test
    fun symptomDiagnose_success_parsesResultsAndPostsJsonBody() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"results":[{"class":"underwater","name_en":"Underwatering","score":0.8}],""" +
                        """"answered":3,"low_confidence":false}""",
                ),
        )

        val result = runBlocking {
            repo.diagnoseSymptoms(SymptomDiagnoseRequest(answers = mapOf("color" to "yellow")))
        }

        assertThat(result.isSuccess).isTrue()
        val body = result.getOrNull()!!
        assertThat(body.answered).isEqualTo(3)
        assertThat(body.results).hasSize(1)
        assertThat(body.results[0].classKey).isEqualTo("underwater")

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).isEqualTo("/api/symptom-diagnose")
        assertThat(request.getHeader("Content-Type")).contains("application/json")
        // JSON body carries the answers map under its wire key.
        assertThat(request.body.readUtf8()).contains("\"answers\"")
    }

    @Test
    fun symptomDiagnose_503_mapsToServerStarting() {
        // POST retries an explicit 503 once, so enqueue two.
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(503))
        val result = runBlocking {
            repo.diagnoseSymptoms(SymptomDiagnoseRequest(answers = emptyMap()))
        }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.ServerStartingException::class.java)
    }

    // --- POST /api/feedback (Ktor) ---

    @Test
    fun feedback_success_parsesIdAndPostsJsonBody() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"id":42}"""),
        )

        val result = runBlocking {
            repo.submitFeedback(FeedbackRequest(scanId = "scan-1", correct = true))
        }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.id).isEqualTo(42L)

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).isEqualTo("/api/feedback")
        val sent = request.body.readUtf8()
        assertThat(sent).contains("\"scan_id\":\"scan-1\"")
        // explicitNulls is NOT set on the wired client, but optional null fields
        // simply serialize as null — the key presence isn't asserted here.
        assertThat(sent).contains("\"correct\":true")
    }

    @Test
    fun feedback_429_mapsToRateLimit() {
        // POST retries an explicit 429 once, so enqueue two.
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(MockResponse().setResponseCode(429))
        val result = runBlocking {
            repo.submitFeedback(FeedbackRequest(scanId = "s", correct = false))
        }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.RateLimitException::class.java)
    }

    @Test
    fun feedback_422_mapsToInvalidPayload() {
        // 422 isn't retried, so a single response.
        server.enqueue(MockResponse().setResponseCode(422))
        val result = runBlocking {
            repo.submitFeedback(FeedbackRequest(scanId = "s", correct = true))
        }
        assertThat(result.isFailure).isTrue()
        assertThat(result.exceptionOrNull()?.message).contains("Invalid feedback payload")
    }
}
