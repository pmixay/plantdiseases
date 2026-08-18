package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.RefreshTokenVault
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.ErrorReport
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.SymptomDiagnoseRequest
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.testutil.FakeAccountApi
import com.plantdiseases.app.testutil.FakeScanDao
import com.plantdiseases.app.util.SanitizedImage
import com.plantdiseases.app.util.UploadImageSanitizer
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
    private lateinit var client: PlantApiClient
    private lateinit var imageFile: File
    private lateinit var accountApi: FakeAccountApi
    private lateinit var authRepository: AuthRepository
    private val sanitizedBytes = "only-sanitized-pixels".encodeToByteArray()

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        client = PlantApiClient(server.url("/").toString())
        accountApi = FakeAccountApi()
        authRepository = AuthRepository(accountApi, FakeVault(), FakeInstallationId)
        runBlocking { authRepository.signIn(AuthProvider.YANDEX, "synthetic-provider-token") }
        repo = ScanRepository(
            FakeScanDao(),
            client,
            imageSanitizer = object : UploadImageSanitizer {
                override fun sanitize(source: File, maxDimension: Int, quality: Int) =
                    SanitizedImage(sanitizedBytes, width = 12, height = 8)
            },
            authRepository = authRepository,
        )
        imageFile = File.createTempFile("PRIVATE-original-name", ".jpg")
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
        assertThat(request.path).contains("training_consent=false")
        assertThat(request.getHeader("Content-Type")).contains("multipart/form-data")
        val body = request.body.readUtf8()
        assertThat(body).contains("filename=\"scan.jpg\"")
        assertThat(body).contains("only-sanitized-pixels")
        assertThat(body).doesNotContain("PRIVATE-original-name")
    }

    @Test
    fun analyze_explicitTrainingConsentSendsCurrentVersionWithSanitizedBytes() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"disease_name":"Leaf Spot"}"""),
        )

        runBlocking {
            repo.analyzeImage(
                imageFile,
                trainingConsent = true,
                trainingConsentVersion = "model-improvement-v1",
            )
        }

        val request = server.takeRequest()
        assertThat(request.path).contains("training_consent=true")
        assertThat(request.path).contains("training_consent_version=model-improvement-v1")
        assertThat(request.body.readUtf8()).contains("only-sanitized-pixels")
    }

    @Test
    fun telemetry_postsOnlyMinimizedVersionedContract() {
        server.enqueue(MockResponse().setResponseCode(200))
        val report = ErrorReport(
            telemetryConsent = true,
            telemetryConsentVersion = "telemetry-errors-v1",
            stage = "analyze",
            kind = "timeout",
            fatal = false,
            httpStatus = 504,
            family = "gamma",
            appVersion = "1.8",
            androidApi = 33,
            breadcrumbs = listOf("scanner_opened"),
        )

        runBlocking { repo.reportError(report) }

        val body = server.takeRequest().body.readUtf8()
        assertThat(body).contains("\"telemetry_consent\":true")
        assertThat(body).contains("\"telemetry_consent_version\":\"telemetry-errors-v1\"")
        assertThat(body).doesNotContain("message")
        assertThat(body).doesNotContain("installation")
        assertThat(body).doesNotContain("device_model")
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
    fun feedback_success_parsesReceiptResultAndPostsStrictJsonBody() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"updated":true}"""),
        )

        val result = runBlocking {
            repo.submitFeedback(FeedbackRequest(feedbackReceipt = "x".repeat(43), correct = true))
        }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.updated).isTrue()

        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("POST")
        assertThat(request.path).isEqualTo("/api/feedback")
        val sent = request.body.readUtf8()
        assertThat(sent).contains("\"feedback_receipt\":\"${"x".repeat(43)}\"")
        assertThat(sent).doesNotContain("scan_id")
        assertThat(sent).doesNotContain("note")
        assertThat(sent).contains("\"correct\":true")
    }

    @Test
    fun feedback_429_mapsToRateLimit() {
        // POST retries an explicit 429 once, so enqueue two.
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(MockResponse().setResponseCode(429))
        val result = runBlocking {
            repo.submitFeedback(FeedbackRequest(feedbackReceipt = "x".repeat(43), correct = false))
        }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.RateLimitException::class.java)
    }

    @Test
    fun feedback_422_mapsToInvalidPayload() {
        // 422 isn't retried, so a single response.
        server.enqueue(MockResponse().setResponseCode(422))
        val result = runBlocking {
            repo.submitFeedback(FeedbackRequest(feedbackReceipt = "x".repeat(43), correct = true))
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

    // --- POST /api/care-plan (Ktor, custom watering_days serializer) ---

    @Test
    fun carePlan_success_parsesIntWateringAndPostsBody() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"species":"Ficus","disease":"healthy","watering_days":7,""" +
                        """"light_tier":"bright_indirect","light_lux_target":{"min":100,"max":300}}""",
                ),
        )
        val plan = runBlocking {
            client.carePlan(CarePlanRequest(disease = "healthy", species = "Ficus"))
        }
        // watering_days came off the wire as a JSON number → Int 7.
        assertThat(plan?.wateringDaysInt).isEqualTo(7)
        assertThat(plan?.wateringDaysIsAsNeeded).isFalse()
        // v2 nested field decodes too (proves @SerialName on the nested type).
        assertThat(plan?.lightLuxTarget?.max).isEqualTo(300)
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/care-plan")
        assertThat(req.getHeader("Content-Type")).contains("application/json")
        assertThat(req.body.readUtf8()).contains("\"disease\":\"healthy\"")
    }

    @Test
    fun carePlan_asNeededWatering_parsesString() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"disease":"x","watering_days":"as_needed"}"""),
        )
        val plan = runBlocking { client.carePlan(CarePlanRequest(disease = "x")) }
        // watering_days came off the wire as a string → as-needed, no int.
        assertThat(plan?.wateringDaysIsAsNeeded).isTrue()
        assertThat(plan?.wateringDaysInt).isNull()
    }

    @Test
    fun carePlan_nonSuccess_returnsNull() {
        // POST retries an explicit 503 once, so enqueue two.
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(503))
        val plan = runBlocking { client.carePlan(CarePlanRequest(disease = "x")) }
        assertThat(plan).isNull()
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
        assertThat(result.getOrNull()?.session?.sessionId).isEqualTo("s1")

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
    fun identify_400_mapsToUnsupportedImage() {
        server.enqueue(MockResponse().setResponseCode(400))
        val result = runBlocking { repo.identifySpecies(imageFile) }
        assertThat(result.exceptionOrNull())
            .isInstanceOf(ScanRepository.UnsupportedImageException::class.java)
    }

    // --- POST /api/feedback/image (Ktor multipart) ---

    @Test
    fun uploadFeedbackImage_success_postsReceiptAndSanitizedImageParts() {
        server.enqueue(MockResponse().setResponseCode(200))
        val request = FeedbackRequest(
            feedbackReceipt = "r".repeat(43),
            correct = false,
            correctedClass = "leaf_spot",
            reason = "wrong_class",
        )
        val result = runBlocking { repo.uploadFeedbackImage(request, imageFile) }

        assertThat(result.isSuccess).isTrue()
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/feedback/image")
        val body = req.body.readUtf8()
        assertThat(body).contains("""name="feedback_receipt"""")
        assertThat(body).contains("r".repeat(43))
        assertThat(body).doesNotContain("""name="scan_id"""")
        assertThat(body).contains("""name="image"""")
        assertThat(body).contains("filename=\"scan.jpg\"")
        assertThat(body).contains("only-sanitized-pixels")
        assertThat(body).doesNotContain("PRIVATE-original-name")
    }

    // --- POST /api/community/scan (Ktor multipart) ---

    @Test
    fun uploadCommunityScan_success_sendsRequiredPartsOmitsBlankOptionals() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"id":99}"""),
        )
        val result = runBlocking {
            repo.uploadCommunityScan(
                scanId = "sc-1",
                region = "", // blank → omitted
                disease = "leaf_spot",
                confidence = 0.8f,
                species = null, // null → omitted
                analysisImage = SanitizedImage(sanitizedBytes, 12, 8),
            )
        }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.id).isEqualTo(99L)

        val req = server.takeRequest()
        assertThat(req.path).isEqualTo("/api/community/scan")
        assertThat(req.getHeader("Authorization")).isEqualTo("Bearer access-1")
        val body = req.body.readUtf8()
        // Ktor 3 quotes Content-Disposition parameter values. Keep every
        // privacy omission assertion quoted too, or it could pass vacuously.
        assertThat(body).contains("""name="scan_id"""")
        assertThat(body).doesNotContain("user_pseudo_id")
        assertThat(body).doesNotContain("nickname")
        assertThat(body).doesNotContain("""name="country"""")
        assertThat(body).contains("""name="confidence"""")
        assertThat(body).contains("filename=\"scan.jpg\"")
        assertThat(body).contains("only-sanitized-pixels")
        assertThat(body).doesNotContain("PRIVATE-original-name")
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
            )
        }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.items?.get(0)?.scanId).isEqualTo("s1")
        assertThat(result.getOrNull()?.summary?.total).isEqualTo(5)

        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("GET")
        assertThat(req.path).startsWith("/api/community/nearby")
        assertThat(req.path).doesNotContain("country=")
        assertThat(req.path).doesNotContain("user_pseudo_id")
        assertThat(req.getHeader("Authorization")).isEqualTo("Bearer access-1")
        assertThat(req.path).contains("limit=12")
        assertThat(req.path).doesNotContain("region=")
        assertThat(req.path).doesNotContain("disease=")
    }

    @Test
    fun communityBySpecies_success_buildsQuery() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"items":[]}"""),
        )
        runBlocking { repo.communityScansBySpecies("Ficus elastica") }
        val req = server.takeRequest()
        assertThat(req.path).startsWith("/api/community/by-species")
        assertThat(req.path).contains("species=Ficus")
        assertThat(req.path).doesNotContain("user_pseudo_id")
        assertThat(req.getHeader("Authorization")).isEqualTo("Bearer access-1")
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
        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/api/community/opinions/sc-9")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer access-1")
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
        val result = runBlocking { repo.fetchMyScanOpinions() }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.totalOpinions).isEqualTo(3)
        assertThat(result.getOrNull()?.scans?.get(0)?.opinionCount).isEqualTo(3)
        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/api/community/my-scan-opinions")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer access-1")
    }

    // --- Community write POSTs / DELETE / push (Ktor) ---

    @Test
    fun submitCommunityOpinion_success_postsBodyAndParses() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"scan_id":"sc-2","ok":true,"count":1,"opinions":[]}"""),
        )
        val result = runBlocking {
            repo.submitCommunityOpinion(
                CommunityOpinionRequest(
                    scanId = "sc-2",
                    suggestedDisease = "rust",
                ),
            )
        }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.ok).isTrue()
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/community/opinion")
        val body = req.body.readUtf8()
        assertThat(body).contains("\"scan_id\":\"sc-2\"")
        assertThat(body).contains("\"suggested_disease\":\"rust\"")
        assertThat(body).doesNotContain("user_pseudo_id")
        assertThat(body).doesNotContain("nickname")
        assertThat(req.getHeader("Authorization")).isEqualTo("Bearer access-1")
    }

    @Test
    fun communitySafety_reportAndBlockNeverSendActorIds() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"created":true}"""),
        )
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"ok":true,"created":true,"block_id":"cb123456789012345678901234567890"}""",
                ),
        )

        val reported = runBlocking {
            repo.reportCommunityContent("opinion", "co-public", "hate_or_harassment")
        }
        val blocked = runBlocking { repo.blockCommunityAuthor("scan", "cs-public") }

        assertThat(reported.getOrNull()).isTrue()
        assertThat(blocked.getOrNull()?.blockId).startsWith("cb")
        val reportRequest = server.takeRequest()
        assertThat(reportRequest.path).isEqualTo("/api/community/safety/report")
        assertThat(reportRequest.getHeader("Authorization")).isEqualTo("Bearer access-1")
        val reportBody = reportRequest.body.readUtf8()
        assertThat(reportBody).contains("\"target_type\":\"opinion\"")
        assertThat(reportBody).contains("\"target_id\":\"co-public\"")
        assertThat(reportBody).contains("\"reason\":\"hate_or_harassment\"")
        assertThat(reportBody).doesNotContain("actor_id")

        val blockRequest = server.takeRequest()
        assertThat(blockRequest.path).isEqualTo("/api/community/safety/block")
        assertThat(blockRequest.body.readUtf8()).doesNotContain("actor_id")
    }

    @Test
    fun communitySafety_listsAndRemovesOnlyOpaqueBlockId() {
        val blockId = "cb123456789012345678901234567890"
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"items":[{"block_id":"$blockId","nickname":"Fern","created_at":9}]}""",
                ),
        )
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))

        val listed = runBlocking { repo.communityBlocks() }
        val removed = runBlocking { repo.unblockCommunityAuthor(blockId) }

        assertThat(listed.getOrNull()?.single()?.nickname).isEqualTo("Fern")
        assertThat(removed.isSuccess).isTrue()
        val listRequest = server.takeRequest()
        assertThat(listRequest.path).isEqualTo("/api/community/safety/blocks")
        assertThat(listRequest.getHeader("Authorization")).isEqualTo("Bearer access-1")
        val deleteRequest = server.takeRequest()
        assertThat(deleteRequest.method).isEqualTo("DELETE")
        assertThat(deleteRequest.path).isEqualTo("/api/community/safety/blocks/$blockId")
        assertThat(deleteRequest.getHeader("Authorization")).isEqualTo("Bearer access-1")
    }

    @Test
    fun guestCommunityReadStaysPublicWithoutBearer() {
        val guestRepository = ScanRepository(
            FakeScanDao(),
            client,
            authRepository = AuthRepository(accountApi, FakeVault(), FakeInstallationId),
        )
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"items":[]}"""),
        )

        assertThat(
            runBlocking { guestRepository.communityNearby(disease = null) }.isSuccess,
        ).isTrue()
        assertThat(server.takeRequest().getHeader("Authorization")).isNull()
    }

    @Test
    fun signedCommunityReadRefreshesAndRetriesBearer() {
        accountApi.refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        server.enqueue(MockResponse().setResponseCode(401).setBody("{}"))
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"items":[]}"""),
        )

        val result = runBlocking { repo.communityNearby(disease = null) }

        assertThat(result.isSuccess).isTrue()
        assertThat(accountApi.refreshCalls).isEqualTo(1)
        assertThat(server.takeRequest().getHeader("Authorization")).isEqualTo("Bearer access-1")
        assertThat(server.takeRequest().getHeader("Authorization")).isEqualTo("Bearer access-2")
    }

    @Test
    fun signedCommunityReadIsNeverFollowedToRedirectTarget() {
        val redirectTarget = MockWebServer()
        redirectTarget.start()
        try {
            redirectTarget.enqueue(
                MockResponse().setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("""{"items":[]}"""),
            )
            server.enqueue(
                MockResponse().setResponseCode(302)
                    .setHeader("Location", redirectTarget.url("/capture")),
            )

            val result = runBlocking { repo.communityNearby(disease = null) }

            assertThat(result.isFailure).isTrue()
            assertThat(server.takeRequest().getHeader("Authorization"))
                .isEqualTo("Bearer access-1")
            assertThat(redirectTarget.requestCount).isEqualTo(0)
        } finally {
            redirectTarget.shutdown()
        }
    }

    @Test
    fun requestSecondOpinion_returnsFlaggedFlag() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"flagged":true}"""),
        )
        val result = runBlocking { repo.requestSecondOpinion("sc-3") }
        assertThat(result.getOrNull()).isTrue()
        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/api/community/request-opinion")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer access-1")
        assertThat(request.body.readUtf8()).doesNotContain("user_pseudo_id")
    }

    @Test
    fun getCommunityScanCounts_success_parsesCountMap() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"counts":{"sc-1":{"opinions":2,"views":7}}}"""),
        )
        val counts = runBlocking { repo.getCommunityScanCounts(listOf("sc-1")) }
        assertThat(counts["sc-1"]?.opinions).isEqualTo(2)
        assertThat(counts["sc-1"]?.views).isEqualTo(7)
        val request = server.takeRequest()
        assertThat(request.path).isEqualTo("/api/community/scan-counts")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer access-1")
    }

    @Test
    fun getCommunityScanCounts_emptyInput_skipsNetwork() {
        val counts = runBlocking { repo.getCommunityScanCounts(emptyList()) }
        assertThat(counts).isEmpty()
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun recordCommunityView_firesPostWithBody() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("{}"))
        runBlocking { repo.recordCommunityView("sc-5") }
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/community/view")
        val body = req.body.readUtf8()
        assertThat(body).contains("\"scan_id\":\"sc-5\"")
        assertThat(body).doesNotContain("user_pseudo_id")
        assertThat(req.getHeader("Authorization")).isEqualTo("Bearer access-1")
    }

    @Test
    fun guestCommunityMutationFailsBeforeNetwork() {
        val guestAuth = AuthRepository(accountApi, FakeVault(), FakeInstallationId)
        runBlocking { guestAuth.restore() }
        val guestRepository = ScanRepository(
            FakeScanDao(),
            client,
            authRepository = guestAuth,
        )

        val result = runBlocking {
            guestRepository.submitCommunityOpinion(
                CommunityOpinionRequest("scan-public", "leaf_spot"),
            )
        }

        assertThat(result.exceptionOrNull())
            .isInstanceOf(AuthenticationRequiredException::class.java)
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun protectedCommunityCallRefreshesOnceAndRetriesWithNewBearer() {
        accountApi.refreshResult = FakeAccountApi.authenticated("access-2", "refresh-2")
        server.enqueue(MockResponse().setResponseCode(401).setBody("{}"))
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"scan_id":"s","ok":true,"opinions":[]}"""),
        )

        val result = runBlocking {
            repo.submitCommunityOpinion(CommunityOpinionRequest("s", "leaf_spot"))
        }

        assertThat(result.isSuccess).isTrue()
        assertThat(accountApi.refreshCalls).isEqualTo(1)
        assertThat(server.takeRequest().getHeader("Authorization")).isEqualTo("Bearer access-1")
        assertThat(server.takeRequest().getHeader("Authorization")).isEqualTo("Bearer access-2")
    }

    private class FakeVault : RefreshTokenVault {
        private var token: String? = null
        private var pendingRotationId: String? = null

        override fun read(): String? = token

        override fun prepareRefresh(): com.plantdiseases.app.auth.PreparedRefresh? = token?.let {
            val rotationId = pendingRotationId
                ?: "00000000-0000-4000-8000-000000000010".also { id -> pendingRotationId = id }
            com.plantdiseases.app.auth.PreparedRefresh(it, rotationId)
        }

        override fun write(token: String) {
            this.token = token
            pendingRotationId = null
        }

        override fun clear() {
            token = null
            pendingRotationId = null
        }
    }

    private object FakeInstallationId : InstallationIdProvider {
        override fun get(): String = "00000000-0000-4000-8000-000000000099"

        override fun rotate() = Unit
    }
}
