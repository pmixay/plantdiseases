package com.plantdiseases.app.data.repository

import android.content.Context
import androidx.exifinterface.media.ExifInterface
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.SymptomDiagnoseRequest
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.testutil.FakeScanDao
import com.plantdiseases.app.util.CommunityIdentity
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.awt.image.BufferedImage
import java.io.File
import javax.imageio.ImageIO

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
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ScanRepositoryNetworkTest {

    private lateinit var server: MockWebServer
    private lateinit var repo: ScanRepository
    private lateinit var client: PlantApiClient
    private lateinit var imageFile: File
    private lateinit var appContext: Context

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        client = PlantApiClient(server.url("/").toString())
        repo = ScanRepository(FakeScanDao(), client)
        appContext = ApplicationProvider.getApplicationContext()
        CommunityPrefs.setUploadsEnabled(appContext, false)
        CommunityIdentity.clear(appContext)
        imageFile = File.createTempFile("upload", ".jpg")
        // A real JPEG (written by the JVM's encoder, independent of
        // Robolectric's Bitmap shadow): identify and feedback-image uploads
        // decode and re-encode the file, so garbage bytes would never reach
        // the wire.
        ImageIO.write(BufferedImage(320, 240, BufferedImage.TYPE_INT_RGB), "jpg", imageFile)
    }

    @After
    fun teardown() {
        server.shutdown()
        imageFile.delete()
    }

    private fun enqueueCommunityAdopt() {
        CommunityPrefs.enable(appContext)
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"ok":true,"author_id":"au_test","display_name":"Leafy Sage 7","created_at":1,"last_seen_at":1,"status":"active"}""",
                ),
        )
    }

    private fun takeAndAssertCommunityAdopt() {
        val adopt = server.takeRequest()
        assertThat(adopt.path).isEqualTo("/api/community/authors/adopt")
        assertThat(adopt.body.readUtf8())
            .contains("\"legacy_pseudo_id\":\"${TelemetryIds.userPseudoId(appContext)}\"")
    }

    private fun validAnalysisBody(): String =
        """
        {"class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":0.9,
         "all_probs":{"gray_mold_botrytis":0,"healthy":0.1,"leaf_scorch_underwatering":0,
          "leaf_spot":0.9,"not_a_plant":0,"nutrient_deficiency":0,"pests":0,
          "powdery_mildew":0,"viral_disease":0},
         "top_k":[{"class":"leaf_spot","confidence":0.9},{"class":"healthy","confidence":0.1}],
         "abstained":false,"verdict_status":"accepted"}
        """.trimIndent()

    @Test
    fun analyze_success_parsesBodyAndBuildsMultipartPost() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(validAnalysisBody()),
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
                .setBody(validAnalysisBody()),
        )
        runBlocking { repo.analyzeImage(imageFile, speciesHint = "Ficus elastica", locale = "ru") }
        val request = server.takeRequest()
        assertThat(request.path).contains("species=Ficus")
        assertThat(request.path).contains("locale=ru")
    }

    @Test
    fun analyze_200Unavailable_isFailureInsteadOfBlankDiagnosis() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"available":false,"family":"frontier","reason":"frontier_disabled","message":"Pick Gamma"}""",
                ),
        )

        val result = runBlocking { repo.analyzeImage(imageFile, family = "frontier") }

        assertThat(result.isFailure).isTrue()
        val error = result.exceptionOrNull()
        assertThat(error).isInstanceOf(ScanRepository.ModelUnavailableException::class.java)
        error as ScanRepository.ModelUnavailableException
        assertThat(error.family).isEqualTo("frontier")
        assertThat(error.reason).isEqualTo("frontier_disabled")
        assertThat(error.serverMessage).isEqualTo("Pick Gamma")
        assertThat(server.takeRequest().path).contains("family=frontier")
    }

    @Test
    fun analyze_empty200_isRejectedInsteadOfBecomingBlankDiagnosis() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{}"),
        )

        val result = runBlocking { repo.analyzeImage(imageFile) }

        assertThat(result.isFailure).isTrue()
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.InvalidAnalysisResponseException::class.java)
    }

    @Test
    fun analyze_contradictoryProbabilityPayloads_areRejected() {
        val bodies = listOf(
            // Declared class is not the probability argmax.
            """
                {"class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":0.1,
                 "all_probs":{"gray_mold_botrytis":0,"healthy":0.9,
                  "leaf_scorch_underwatering":0,"leaf_spot":0.1,"not_a_plant":0,
                  "nutrient_deficiency":0,"pests":0,"powdery_mildew":0,"viral_disease":0},
                 "top_k":[{"class":"healthy","confidence":0.9},{"class":"leaf_spot","confidence":0.1}]}
            """.trimIndent(),
            // Declared confidence disagrees with the class probability.
            """
                {"class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":0.6,
                 "all_probs":{"gray_mold_botrytis":0,"healthy":0.1,
                  "leaf_scorch_underwatering":0,"leaf_spot":0.9,"not_a_plant":0,
                  "nutrient_deficiency":0,"pests":0,"powdery_mildew":0,"viral_disease":0},
                 "top_k":[{"class":"leaf_spot","confidence":0.9},{"class":"healthy","confidence":0.1}]}
            """.trimIndent(),
            // A normalised one-class map is still a partial nine-class head.
            """
                {"class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":1,
                 "all_probs":{"leaf_spot":1},
                 "top_k":[{"class":"leaf_spot","confidence":1}]}
            """.trimIndent(),
        )
        bodies.forEach { body ->
            server.enqueue(
                MockResponse().setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(body),
            )
            val result = runBlocking { repo.analyzeImage(imageFile) }
            assertThat(result.exceptionOrNull())
                .isInstanceOf(ScanRepository.InvalidAnalysisResponseException::class.java)
        }
    }

    @Test
    fun analyze_imageQualityAbstention_allowsHonestEmptyDiagnostics() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """
                    {"class_name":"unknown","confidence":0,"all_probs":{},"top_k":[],
                     "warnings":["blurry_image"],"abstained":true,
                     "abstention_reason":"image_quality","verdict_status":"abstained"}
                    """.trimIndent(),
                ),
        )

        val result = runBlocking { repo.analyzeImage(imageFile) }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.abstentionReason).isEqualTo("image_quality")
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

    // --- GET /api/species-info (Ktor, PlantApiClient wrapper) ---

    @Test
    fun speciesInfo_success_parsesBodyAndQueries() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"available":true,"title":"Ficus","image_url":"http://x/i.jpg"}"""),
        )
        val info = runBlocking { client.speciesInfo("Ficus elastica", "en") }
        assertThat(info?.available).isTrue()
        assertThat(info?.imageUrl).isEqualTo("http://x/i.jpg")
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("GET")
        assertThat(req.path).startsWith("/api/species-info")
        assertThat(req.path).contains("name=Ficus")
        assertThat(req.path).contains("locale=en")
    }

    @Test
    fun speciesInfo_nonSuccess_returnsNull() {
        // Mirrors the old Retrofit .body() contract: null on non-2xx.
        server.enqueue(MockResponse().setResponseCode(500))
        val info = runBlocking { client.speciesInfo("X", "en") }
        assertThat(info).isNull()
    }

    // --- GET /api/species-diseases (Ktor) ---

    @Test
    fun speciesDiseases_success_parsesItemsAndQueries() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"available":true,"diseases":[{"name":"Root rot","doc_key":"root_rot"}]}""",
                ),
        )
        val resp = runBlocking { client.speciesDiseases("Ficus", "Rubber plant", "ru") }
        assertThat(resp?.diseases).hasSize(1)
        assertThat(resp?.diseases?.get(0)?.docKey).isEqualTo("root_rot")
        val req = server.takeRequest()
        assertThat(req.path).startsWith("/api/species-diseases")
        assertThat(req.path).contains("name=Ficus")
        assertThat(req.path).contains("common=Rubber")
        assertThat(req.path).contains("locale=ru")
    }

    // --- POST /api/chat (Ktor, Map<String,Any?> diagnosis via AnyMapSerializer) ---

    @Test
    fun chat_success_serializesDiagnosisMapAsJsonObject() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"available":true,"reply":"Water less.","session":{"session_id":"s1"}}"""),
        )
        val request = ChatRequest(
            sessionId = "s1",
            message = "help",
            diagnosis = mapOf(
                "class_name" to "leaf_spot",
                "confidence" to 0.9,
                "is_healthy" to false,
                "warnings" to listOf("blurry"),
                "species" to null,
            ),
        )
        val result = runBlocking { repo.sendChat(request) }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.reply).isEqualTo("Water less.")
        // `session` is deliberately not modelled (nothing renders the quota),
        // so the extra key must decode away instead of throwing.
        assertThat(result.getOrNull()?.available).isTrue()

        val sent = server.takeRequest()
        assertThat(sent.method).isEqualTo("POST")
        assertThat(sent.path).isEqualTo("/api/chat")
        val body = sent.body.readUtf8()
        // diagnosis must be a real nested JSON object, not a toString() blob.
        assertThat(body).contains("\"diagnosis\":{")
        assertThat(body).contains("\"class_name\":\"leaf_spot\"")
        assertThat(body).contains("\"confidence\":0.9")
        assertThat(body).contains("\"is_healthy\":false")
        assertThat(body).contains("\"warnings\":[\"blurry\"]")
    }

    @Test
    fun chat_429_mapsToRateLimit() {
        // POST retries an explicit 429 once.
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(MockResponse().setResponseCode(429))
        val result = runBlocking { repo.sendChat(ChatRequest(sessionId = "s", message = "x")) }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.RateLimitException::class.java)
    }

    // --- POST /api/progression (Ktor, List<Map<String,Any?>> via AnyMapListSerializer) ---

    @Test
    fun progression_success_serializesHistoryAndParsesTrend() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"trend":"worsening","sparkline":[1.0,2.0,3.0],"alert":true}"""),
        )
        val request = ProgressionRequest(
            history = listOf(
                mapOf("class_name" to "leaf_spot", "confidence" to 0.8, "severity" to "high"),
                mapOf("class_name" to "leaf_spot", "confidence" to 0.9, "severity" to "high"),
            ),
        )
        val result = runBlocking { repo.fetchProgression(request) }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.trend).isEqualTo("worsening")
        assertThat(result.getOrNull()?.alert).isTrue()

        val sent = server.takeRequest()
        assertThat(sent.path).isEqualTo("/api/progression")
        val body = sent.body.readUtf8()
        // history must be a JSON array of objects.
        assertThat(body).contains("\"history\":[{")
        assertThat(body).contains("\"class_name\":\"leaf_spot\"")
        assertThat(body).contains("\"severity\":\"high\"")
    }

    // --- POST /api/identify (Ktor multipart) ---

    @Test
    fun identify_success_parsesCandidatesAndBuildsMultipart() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"available":true,"results":[""" +
                        """{"scientific_name":"Ficus elastica","common_names":["Rubber plant"],""" +
                        """"score":0.7}]}""",
                ),
        )
        val result = runBlocking { repo.identifySpecies(imageFile, organ = "leaf", locale = "ru") }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.results?.get(0)?.scientificName).isEqualTo("Ficus elastica")

        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).startsWith("/api/identify")
        assertThat(req.path).contains("organ=leaf")
        assertThat(req.path).contains("locale=ru")
        assertThat(req.getHeader("Content-Type")).contains("multipart/form-data")
    }

    @Test
    fun identify_napRejection_preservesServerMarker() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"available":false,"results":[],"source":"skipped_nap","organ":"auto"}""",
                ),
        )

        val result = runBlocking { repo.identifySpecies(imageFile, organ = "auto") }

        assertThat(result.isSuccess).isTrue()
        val response = result.getOrThrow()
        assertThat(response.available).isFalse()
        assertThat(response.results).isEmpty()
        assertThat(response.wasRejectedAsNotAPlant).isTrue()
    }

    @Test
    fun identify_400_mapsToUnsupportedImage() {
        server.enqueue(MockResponse().setResponseCode(400))
        val result = runBlocking { repo.identifySpecies(imageFile) }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.UnsupportedImageException::class.java)
    }

    // --- POST /api/feedback/image (Ktor multipart) ---

    @Test
    fun uploadFeedbackImage_success_postsScanIdAndImageParts() {
        server.enqueue(MockResponse().setResponseCode(200))
        val result = runBlocking { repo.uploadFeedbackImage("scan-7", imageFile) }

        assertThat(result.isSuccess).isTrue()
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/feedback/image")
        val body = req.body.readUtf8()
        // multipart carries the scan_id text part and the image part.
        // Ktor 3 quotes Content-Disposition parameter values — name="scan_id" —
        // where Ktor 2 emitted them bare. The quoted form is what RFC 7578
        // specifies, so this follows the fix rather than working around it.
        assertThat(body).contains("""name="scan_id"""")
        assertThat(body).contains("scan-7")
        assertThat(body).contains("""name="image"""")
    }

    @Test
    fun photoUploads_reencode_soCameraExifNeverLeavesTheDevice() {
        // What a phone camera writes: a model string and a GPS fix.
        ExifInterface(imageFile.path).apply {
            setAttribute(ExifInterface.TAG_MODEL, EXIF_MARKER)
            setLatLong(55.7558, 37.6173)
            saveAttributes()
        }
        assertThat(latin1(imageFile.readBytes())).contains(EXIF_MARKER) // fixture sanity

        server.enqueue(MockResponse().setResponseCode(200))
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"available":false,"results":[]}"""),
        )
        assertThat(runBlocking { repo.uploadFeedbackImage("scan-7", imageFile) }.isSuccess).isTrue()
        assertThat(runBlocking { repo.identifySpecies(imageFile) }.isSuccess).isTrue()

        repeat(2) {
            val body = latin1(server.takeRequest().body.readByteArray())
            assertThat(body).doesNotContain(EXIF_MARKER)
            assertThat(body).doesNotContain("Exif\u0000\u0000")
        }
    }

    private fun latin1(bytes: ByteArray) = String(bytes, Charsets.ISO_8859_1)

    // --- POST /api/community/scan (Ktor multipart) ---

    @Test
    fun uploadCommunityScan_success_sendsRequiredPartsOmitsBlankOptionals() {
        enqueueCommunityAdopt()
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"id":99}"""),
        )
        val result = runBlocking {
            repo.uploadCommunityScan(
                scanId = "sc-1",
                userPseudoId = "u-1",
                region = "", // blank → omitted
                disease = "leaf_spot",
                confidence = 0.8f,
                species = null, // null → omitted
                context = appContext,
            )
        }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.id).isEqualTo(99L)

        takeAndAssertCommunityAdopt()
        val req = server.takeRequest()
        assertThat(req.path).isEqualTo("/api/community/scan")
        assertThat(req.getHeader("X-Community-Author-Key")).hasLength(64)
        val body = req.body.readUtf8()
        // Quoted parameter values — see the note in the feedback-image test.
        // The doesNotContain assertions have to move with them: against a Ktor
        // 3 body the bare `name=region` never appears whether or not the part
        // was sent, so leaving them unquoted would make them pass vacuously
        // and stop pinning the omit-blanks behaviour they exist for.
        assertThat(body).contains("""name="scan_id"""")
        assertThat(body).doesNotContain("""name="country"""")
        assertThat(body).contains("""name="confidence"""")
        // blank region / null species must NOT be sent.
        assertThat(body).doesNotContain("""name="region"""")
        assertThat(body).doesNotContain("""name="species"""")
    }

    // --- Community read GETs (Ktor) ---

    @Test
    fun communityNearby_success_parsesItemsAndOmitsBlankQueries() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"items":[{"scan_id":"s1","disease":"leaf_spot","opinion_count":2}],""" +
                        """"summary":{"country":"US","total":5}}""",
                ),
        )
        val result = runBlocking {
            repo.communityNearby(
                region = "", // blank → omitted
                disease = null, // null → omitted
                userPseudoId = "u1",
            )
        }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.items?.get(0)?.scanId).isEqualTo("s1")
        assertThat(result.getOrNull()?.summary?.total).isEqualTo(5)

        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("GET")
        assertThat(req.path).startsWith("/api/community/nearby")
        assertThat(req.path).doesNotContain("country=")
        // The pseudo-id is a bearer value: header, never the URL.
        assertThat(req.path).doesNotContain("user_pseudo_id")
        assertThat(req.getHeader("X-User-Pseudo-Id")).isEqualTo("u1")
        assertThat(req.path).contains("limit=12")
        assertThat(req.path).doesNotContain("region=")
        assertThat(req.path).doesNotContain("disease=")
    }

    @Test
    fun communityNearby_recommendedRoundTripsOpaqueCursor() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"items":[],"ranking":"recommended","next_cursor":"r1.next.signature"}"""),
        )
        val result = runBlocking {
            repo.communityNearby(
                disease = null,
                userPseudoId = "reader",
                ranking = "recommended",
                cursor = "r1.previous.signature",
            )
        }
        assertThat(result.getOrNull()?.ranking).isEqualTo("recommended")
        assertThat(result.getOrNull()?.nextCursor).isEqualTo("r1.next.signature")
        val path = server.takeRequest().path.orEmpty()
        assertThat(path).contains("ranking=recommended")
        assertThat(path).contains("cursor=r1.previous.signature")
    }

    @Test
    fun communityNearby_recentRequestsNewestFirstRanking() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"items":[],"ranking":"recent"}"""),
        )

        val result = runBlocking {
            repo.communityNearby(
                disease = null,
                userPseudoId = "reader",
                ranking = "recent",
            )
        }

        assertThat(result.getOrThrow().ranking).isEqualTo("recent")
        assertThat(server.takeRequest().path.orEmpty()).contains("ranking=recent")
    }

    @Test
    fun communityBySpecies_success_buildsQuery() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"items":[]}"""),
        )
        runBlocking { repo.communityScansBySpecies("Ficus elastica", userPseudoId = "u9") }
        val req = server.takeRequest()
        assertThat(req.path).startsWith("/api/community/by-species")
        assertThat(req.path).contains("species=Ficus")
        assertThat(req.path).doesNotContain("user_pseudo_id")
        assertThat(req.getHeader("X-User-Pseudo-Id")).isEqualTo("u9")
    }

    @Test
    fun getCommunityOpinions_success_pathAndParse() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"scan_id":"sc-9","opinions":[""" +
                        """{"suggested_disease":"rust","note":"looks like rust"}],"views":4}""",
                ),
        )
        val result = runBlocking { repo.getCommunityOpinions("sc-9") }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.views).isEqualTo(4)
        assertThat(result.getOrNull()?.opinions?.get(0)?.suggestedDisease).isEqualTo("rust")
        assertThat(server.takeRequest().path).isEqualTo("/api/community/opinions/sc-9")
    }

    @Test
    fun fetchMyScanOpinions_success_pathAndParse() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"scans":[{"scan_id":"a","disease":"leaf_spot","opinion_count":3}],""" +
                        """"total_opinions":3,"total_views":10}""",
                ),
        )
        val result = runBlocking { repo.fetchMyScanOpinions("user-42") }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.totalOpinions).isEqualTo(3)
        assertThat(result.getOrNull()?.scans?.get(0)?.opinionCount).isEqualTo(3)
        val req = server.takeRequest()
        assertThat(req.path).isEqualTo("/api/community/my-scan-opinions")
        assertThat(req.getHeader("X-User-Pseudo-Id")).isEqualTo("user-42")
    }

    // --- Community write POSTs / DELETE / push (Ktor) ---

    @Test
    fun submitCommunityOpinion_success_postsBodyAndParses() {
        enqueueCommunityAdopt()
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"scan_id":"sc-2","ok":true,"count":1,"opinions":[]}"""),
        )
        val result = runBlocking {
            repo.submitCommunityOpinion(
                appContext,
                CommunityOpinionRequest(
                    scanId = "sc-2",
                    userPseudoId = "u1",
                    suggestedDisease = "rust",
                ),
            )
        }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.ok).isTrue()
        takeAndAssertCommunityAdopt()
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/community/opinion")
        val body = req.body.readUtf8()
        assertThat(body).contains("\"scan_id\":\"sc-2\"")
        assertThat(body).contains("\"suggested_disease\":\"rust\"")
        assertThat(req.getHeader("X-Community-Author-Key")).hasLength(64)
    }

    @Test
    fun submitCommunityOpinion_withoutAdoptedKey_failsWithoutARequest() {
        // Community off and nothing adopted: the server would answer 401.
        val result = runBlocking {
            repo.submitCommunityOpinion(
                appContext,
                CommunityOpinionRequest("sc-nokey", "u1", "healthy"),
            )
        }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.CommunityIdentityUnavailableException::class.java)
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun submitCommunityOpinion_423_mapsToLockedException() {
        enqueueCommunityAdopt()
        server.enqueue(MockResponse().setResponseCode(423))
        val result = runBlocking {
            repo.submitCommunityOpinion(
                appContext,
                CommunityOpinionRequest("sc-locked", "u1", "healthy"),
            )
        }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.CommunityOpinionsLockedException::class.java)
        takeAndAssertCommunityAdopt()
    }

    @Test
    fun communitySafety_postsReportAndBlockTargets() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"created":true}"""),
        )
        val report = runBlocking {
            repo.reportCommunityContent("reader", "opinion", "co123", "spam_or_misleading")
        }
        assertThat(report.getOrNull()?.created).isTrue()
        val reportRequest = server.takeRequest()
        assertThat(reportRequest.path).isEqualTo("/api/community/safety/report")
        assertThat(reportRequest.body.readUtf8()).contains("\"target_id\":\"co123\"")

        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"blocked":true}"""),
        )
        val block = runBlocking {
            repo.blockCommunityAuthor("reader", "scan", "cs123")
        }
        assertThat(block.getOrNull()?.blocked).isTrue()
        assertThat(server.takeRequest().path).isEqualTo("/api/community/safety/block")
    }

    @Test
    fun requestSecondOpinion_returnsFlaggedFlag() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"flagged":true}"""),
        )
        val result = runBlocking { repo.requestSecondOpinion("sc-3", "u2") }
        assertThat(result.getOrNull()).isTrue()
        assertThat(server.takeRequest().path).isEqualTo("/api/community/request-opinion")
    }

    @Test
    fun recordCommunityView_firesPostWithBody() {
        enqueueCommunityAdopt()
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        runBlocking { repo.recordCommunityView("sc-5", "viewer-1", appContext) }
        takeAndAssertCommunityAdopt()
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/community/view")
        assertThat(req.getHeader("X-Community-Author-Key")).hasLength(64)
        assertThat(req.body.readUtf8()).contains("\"scan_id\":\"sc-5\"")
    }

    @Test
    fun registerPushToken_firesPostWithBody() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        runBlocking { repo.registerPushToken("u-7", "fcm-token-xyz") }
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/push/register")
        assertThat(req.body.readUtf8()).contains("\"token\":\"fcm-token-xyz\"")
    }

    @Test
    fun adoptCommunityAuthor_sendsKeyAsHeaderNotInBody() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"ok":true,"author_id":"au_abc123","display_name":"Leafy Sage 7",
                       "created_at":1,"last_seen_at":1,"status":"active"}""",
                ),
        )
        val result = runBlocking {
            repo.adoptCommunityAuthor("f".repeat(64), "Leafy Sage 7", "legacy-reader-7")
        }
        assertThat(result.getOrNull()?.authorId).isEqualTo("au_abc123")

        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/community/authors/adopt")
        // The key travels as a header — never in the JSON body, never in the URL.
        assertThat(req.getHeader("X-Community-Author-Key")).isEqualTo("f".repeat(64))
        val body = req.body.readUtf8()
        assertThat(body).doesNotContain("f".repeat(64))
        assertThat(body).contains("\"seed_display_name\":\"Leafy Sage 7\"")
        assertThat(body).contains("\"legacy_pseudo_id\":\"legacy-reader-7\"")
    }

    private companion object {
        const val EXIF_MARKER = "LeakyPhone9"
    }
}
