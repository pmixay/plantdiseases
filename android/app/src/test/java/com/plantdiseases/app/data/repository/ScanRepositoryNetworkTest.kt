package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.ProgressionRequest
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
    private lateinit var client: PlantApiClient
    private lateinit var imageFile: File

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        client = PlantApiClient(server.url("/").toString())
        repo = ScanRepository(FakeScanDao(), client)
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

    // --- GET /api/community/trending (Ktor) ---

    @Test
    fun communityTrending_success_parsesTopListsAndQueries() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"scope":"country","top_species":[{"species":"Ficus","count":3}],""" +
                        """"top_diseases":[]}""",
                ),
        )
        val resp = runBlocking { client.communityTrending(country = "US", region = "CA") }
        assertThat(resp?.scope).isEqualTo("country")
        assertThat(resp?.topSpecies).hasSize(1)
        assertThat(resp?.topSpecies?.get(0)?.species).isEqualTo("Ficus")
        val req = server.takeRequest()
        assertThat(req.path).startsWith("/api/community/trending")
        assertThat(req.path).contains("country=US")
        assertThat(req.path).contains("region=CA")
        assertThat(req.path).contains("days=7")
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
    fun uploadFeedbackImage_success_postsScanIdAndImageParts() {
        server.enqueue(MockResponse().setResponseCode(200))
        val result = runBlocking { repo.uploadFeedbackImage("scan-7", imageFile) }

        assertThat(result.isSuccess).isTrue()
        val req = server.takeRequest()
        assertThat(req.method).isEqualTo("POST")
        assertThat(req.path).isEqualTo("/api/feedback/image")
        val body = req.body.readUtf8()
        // multipart carries the scan_id text part and the image part.
        // (Ktor's formData writes Content-Disposition names unquoted: name=scan_id.)
        assertThat(body).contains("name=scan_id")
        assertThat(body).contains("scan-7")
        assertThat(body).contains("name=image")
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
                userPseudoId = "u-1",
                country = "US",
                region = "", // blank → omitted
                disease = "leaf_spot",
                confidence = 0.8f,
                species = null, // null → omitted
            )
        }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()?.id).isEqualTo(99L)

        val req = server.takeRequest()
        assertThat(req.path).isEqualTo("/api/community/scan")
        val body = req.body.readUtf8()
        assertThat(body).contains("name=scan_id")
        assertThat(body).contains("name=country")
        assertThat(body).contains("name=confidence")
        // blank region / null species must NOT be sent.
        assertThat(body).doesNotContain("name=region")
        assertThat(body).doesNotContain("name=species")
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
                country = "US",
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
        assertThat(req.path).contains("country=US")
        assertThat(req.path).contains("user_pseudo_id=u1")
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
        runBlocking { repo.communityScansBySpecies("Ficus elastica", userPseudoId = "u9") }
        val req = server.takeRequest()
        assertThat(req.path).startsWith("/api/community/by-species")
        assertThat(req.path).contains("species=Ficus")
        assertThat(req.path).contains("user_pseudo_id=u9")
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
        assertThat(server.takeRequest().path).isEqualTo("/api/community/my-scan-opinions/user-42")
    }
}
