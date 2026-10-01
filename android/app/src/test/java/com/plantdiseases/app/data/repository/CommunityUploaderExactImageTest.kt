package com.plantdiseases.app.data.repository

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.PreparedRefresh
import com.plantdiseases.app.auth.RefreshTokenVault
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.testutil.FakeAccountApi
import com.plantdiseases.app.testutil.FakeScanDao
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.SanitizedImage
import com.plantdiseases.app.util.UploadImageSanitizer
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertThrows
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CommunityUploaderExactImageTest {
    private lateinit var context: Context
    private lateinit var server: MockWebServer
    private lateinit var repository: ScanRepository
    private lateinit var scanDao: FakeScanDao
    private lateinit var uploader: CommunityUploader
    private lateinit var source: File
    private lateinit var sanitizer: RecordingSanitizer

    @Before
    fun setup() {
        context = ApplicationProvider.getApplicationContext()
        CommunityPrefs.setUploadsEnabled(context, true)
        server = MockWebServer().also { it.start() }
        sanitizer = RecordingSanitizer()
        val authRepository = AuthRepository(
            FakeAccountApi(),
            FakeVault(),
            FakeInstallationId,
        )
        runBlocking {
            authRepository.signIn(AuthProvider.YANDEX, "provider-token")
        }
        scanDao = FakeScanDao()
        repository = ScanRepository(
            scanDao,
            PlantApiClient(server.url("/").toString()),
            imageSanitizer = sanitizer,
            authRepository = authRepository,
        )
        uploader = CommunityUploader(context, repository, authRepository, sanitizer)
        source = File.createTempFile("community-source-", ".jpg", context.cacheDir)
            .apply { writeText("private-unsanitized-source") }
    }

    @After
    fun teardown() {
        runBlocking { repository.clearAll() }
        source.delete()
        CommunityPrefs.setUploadsEnabled(context, false)
        server.shutdown()
    }

    @Test
    fun uploadUsesExactAnalyzeBytesWithoutSecondEncode() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(analysisBody("server-scan-1")),
        )
        val analysis = runBlocking { repository.analyzeImage(source) }.getOrThrow()
        val analyzeRequest = server.takeRequest()
        val rowId = runBlocking { repository.saveScan(source.absolutePath, analysis) }
        val scan = runBlocking { repository.getScanById(rowId) }!!

        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"ok":true,"id":9}"""),
        )
        val outcome = runBlocking { uploader.uploadIfEnabled(scan, analysis) }

        assertThat(outcome).isEqualTo(CommunityUploader.UploadOutcome.Uploaded)
        assertThat(sanitizer.calls).containsExactly(
            SanitizerCall(source, maxDimension = 1_024, quality = 85),
        )
        val analyzeBody = analyzeRequest.body.readUtf8()
        val communityBody = server.takeRequest().body.readUtf8()
        assertThat(analyzeBody).contains(EXACT_ANALYSIS_BYTES.decodeToString())
        assertThat(communityBody).contains(EXACT_ANALYSIS_BYTES.decodeToString())
        assertThat(communityBody).doesNotContain("private-unsanitized-source")
    }

    @Test
    fun missingRetainedAnalyzeBytesFailsClosedBeforeNetwork() {
        val scan = scan("unretained-server-scan")

        val outcome = runBlocking {
            uploader.uploadIfEnabled(
                scan,
                com.plantdiseases.app.data.model.AnalysisResponse(
                    scanId = scan.clientScanId,
                    classKey = "leaf_spot",
                    diseaseName = "Leaf Spot",
                ),
            )
        }

        assertThat(outcome).isEqualTo(CommunityUploader.UploadOutcome.MissingAnalysisImage)
        assertThat(server.requestCount).isEqualTo(0)
        assertThat(sanitizer.calls).isEmpty()
    }

    @Test
    fun saveFailureDiscardsPendingBytesWithoutWritingAnOrphan() {
        server.enqueue(analysisResponse("failed-save-scan"))
        val failingDao = FailingFirstInsertScanDao()
        val failedRepository = ScanRepository(
            failingDao,
            PlantApiClient(server.url("/").toString()),
            imageSanitizer = sanitizer,
        )
        val analysis = runBlocking { failedRepository.analyzeImage(source) }.getOrThrow()
        val probe = scan("failed-save-scan")
        assertThat(failedRepository.retainedAnalysisImage(probe)).isNull()

        assertThrows(IllegalStateException::class.java) {
            runBlocking { failedRepository.saveScan(source.absolutePath, analysis) }
        }

        val retryRowId = runBlocking { failedRepository.saveScan(source.absolutePath, analysis) }
        val retryRow = runBlocking { failedRepository.getScanById(retryRowId) }!!
        assertThat(failedRepository.retainedAnalysisImage(retryRow)).isNull()
        runBlocking { failedRepository.clearAll() }
    }

    @Test
    fun processDeathBetweenAnalyzeAndSaveLeavesNoDiskSidecar() {
        server.enqueue(analysisResponse("process-death-scan"))
        val analysis = runBlocking { repository.analyzeImage(source) }.getOrThrow()
        assertThat(repository.retainedAnalysisImage(scan("process-death-scan"))).isNull()
        val restartedRepository = ScanRepository(
            scanDao,
            PlantApiClient(server.url("/").toString()),
            imageSanitizer = sanitizer,
        )

        val rowId = runBlocking { restartedRepository.saveScan(source.absolutePath, analysis) }
        val row = runBlocking { restartedRepository.getScanById(rowId) }!!

        assertThat(restartedRepository.retainedAnalysisImage(row)).isNull()
    }

    @Test
    fun startupPruneDeletesOnlyExpiredRetainedAnalyzeBytes() {
        val nowMs = 2 * ScanRepository.RETAINED_ANALYSIS_IMAGE_TTL_MS
        val expired = analyzeAndSave("expired-scan")
            .copy(timestamp = nowMs - ScanRepository.RETAINED_ANALYSIS_IMAGE_TTL_MS)
        val current = analyzeAndSave("current-scan").copy(timestamp = nowMs)
        scanDao.items.indices.forEach { index ->
            scanDao.items[index] = when (scanDao.items[index].clientScanId) {
                expired.clientScanId -> expired
                current.clientScanId -> current
                else -> scanDao.items[index]
            }
        }
        assertThat(repository.retainedAnalysisImage(expired)).isNotNull()
        assertThat(repository.retainedAnalysisImage(current)).isNotNull()

        val purged = runBlocking {
            repository.purgeExpiredRetainedAnalysisImages(nowMs = nowMs)
        }

        assertThat(purged).isEqualTo(1)
        assertThat(repository.retainedAnalysisImage(expired)).isNull()
        assertThat(repository.retainedAnalysisImage(current)).isNotNull()
        assertThat(
            runBlocking { repository.purgeExpiredRetainedAnalysisImages(nowMs = nowMs) },
        ).isEqualTo(0)
    }

    @Test
    fun pendingAnalyzeImagesAreStrictlyBounded() {
        val ids = (0..ScanRepository.MAX_PENDING_ANALYSIS_IMAGES).map { "bounded-scan-$it" }
        val analyses = ids.map { scanId ->
            server.enqueue(analysisResponse(scanId))
            runBlocking { repository.analyzeImage(source) }.getOrThrow()
        }

        val rows = analyses.map { analysis ->
            val rowId = runBlocking { repository.saveScan(source.absolutePath, analysis) }
            runBlocking { repository.getScanById(rowId) }!!
        }

        assertThat(repository.retainedAnalysisImage(rows.first())).isNull()
        rows.takeLast(ScanRepository.MAX_PENDING_ANALYSIS_IMAGES).forEach { row ->
            assertThat(repository.retainedAnalysisImage(row)).isNotNull()
        }
    }

    @Test
    fun failedSidecarDeletionPreservesOwningDatabaseRow() {
        val row = analyzeAndSave("undeletable-sidecar")
        val sidecar = checkNotNull(
            source.parentFile?.listFiles()?.singleOrNull { file ->
                file.name.startsWith(".${source.name}.analysis-")
            },
        )
        check(sidecar.delete())
        check(sidecar.mkdir())
        val blocker = File(sidecar, "blocker").apply { writeText("keep directory non-empty") }
        try {
            assertThrows(IllegalStateException::class.java) {
                runBlocking { repository.deleteScan(row.id) }
            }
            assertThat(runBlocking { repository.getScanById(row.id) }).isNotNull()
        } finally {
            blocker.delete()
            sidecar.delete()
        }
        runBlocking { repository.deleteScan(row.id) }
    }

    private fun analyzeAndSave(scanId: String): ScanEntity {
        server.enqueue(analysisResponse(scanId))
        val analysis = runBlocking { repository.analyzeImage(source) }.getOrThrow()
        assertThat(analysis.scanId).isEqualTo(scanId)
        val rowId = runBlocking { repository.saveScan(source.absolutePath, analysis) }
        return runBlocking { repository.getScanById(rowId) }!!
    }

    private fun analysisResponse(scanId: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(analysisBody(scanId))

    // A complete nine-class diagnostic: the repository rejects partial bodies.
    private fun analysisBody(scanId: String) =
        """
        {"scan_id":"$scanId","class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":0.9,
         "all_probs":{"gray_mold_botrytis":0,"healthy":0.1,"leaf_scorch_underwatering":0,
          "leaf_spot":0.9,"not_a_plant":0,"nutrient_deficiency":0,"pests":0,
          "powdery_mildew":0,"viral_disease":0},
         "top_k":[{"class":"leaf_spot","confidence":0.9},{"class":"healthy","confidence":0.1}],
         "abstained":false,"verdict_status":"accepted"}
        """.trimIndent()

    private fun scan(clientScanId: String) = ScanEntity(
        clientScanId = clientScanId,
        imagePath = source.absolutePath,
        diseaseName = "Leaf Spot",
        diseaseNameRu = "",
        confidence = 0.9f,
        description = "",
        descriptionRu = "",
        treatment = "[]",
        treatmentRu = "[]",
        prevention = "[]",
        preventionRu = "[]",
        isHealthy = false,
        displayedClassKey = "leaf_spot",
    )

    private class RecordingSanitizer : UploadImageSanitizer {
        val calls = mutableListOf<SanitizerCall>()

        override fun sanitize(source: File, maxDimension: Int, quality: Int): SanitizedImage {
            calls += SanitizerCall(source, maxDimension, quality)
            return SanitizedImage(EXACT_ANALYSIS_BYTES, width = 12, height = 8)
        }
    }

    private class FailingFirstInsertScanDao(
        private val delegate: FakeScanDao = FakeScanDao(),
    ) : ScanDao by delegate {
        private var failNext = true

        override suspend fun insert(scan: ScanEntity): Long {
            if (failNext) {
                failNext = false
                error("injected insert failure")
            }
            return delegate.insert(scan)
        }
    }

    private data class SanitizerCall(
        val source: File,
        val maxDimension: Int,
        val quality: Int,
    )

    private class FakeVault : RefreshTokenVault {
        private var token: String? = null
        private var rotationId: String? = null

        override fun read(): String? = token

        override fun prepareRefresh(): PreparedRefresh? = token?.let { refresh ->
            PreparedRefresh(
                refresh,
                rotationId ?: "00000000-0000-4000-8000-000000000010"
                    .also { rotationId = it },
            )
        }

        override fun write(token: String) {
            this.token = token
            rotationId = null
        }

        override fun clear() {
            token = null
            rotationId = null
        }
    }

    private object FakeInstallationId : InstallationIdProvider {
        override fun get(): String = "00000000-0000-4000-8000-000000000099"

        override fun rotate() = Unit
    }

    private companion object {
        val EXACT_ANALYSIS_BYTES = "exact-default-analysis-jpeg".encodeToByteArray()
    }
}
