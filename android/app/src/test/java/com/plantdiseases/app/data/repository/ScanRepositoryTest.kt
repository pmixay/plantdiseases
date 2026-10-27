package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.Detection
import com.plantdiseases.app.data.model.DetectionRegion
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.testutil.FakeScanDao
import com.plantdiseases.app.util.FeedbackReceiptStore
import kotlinx.coroutines.runBlocking
import org.junit.Before
import org.junit.Test

/**
 * Pure-JVM coverage of the [ScanRepository] logic that needs no network:
 * the JSON parse helpers, the AnalysisResponse → ScanEntity mapping in
 * saveScan, the not-a-plant / healthy verdict rules, and the stats
 * rollup. Uses an in-memory [FakeScanDao] so no Robolectric/Room/device
 * is required.
 */
class ScanRepositoryTest {

    private lateinit var dao: FakeScanDao
    private lateinit var repo: ScanRepository
    private lateinit var receiptStore: FakeReceiptStore

    @Before
    fun setup() {
        dao = FakeScanDao()
        receiptStore = FakeReceiptStore()
        repo = ScanRepository(
            dao,
            PlantApiClient("http://localhost/"),
            feedbackReceiptStore = receiptStore,
        )
    }

    private fun scan(
        diseaseName: String = "Leaf Spot",
        diseaseNameRu: String = "Пятнистость",
        confidence: Float = 0.8f,
        isHealthy: Boolean = false,
        allProbs: String? = null,
        displayedClassKey: String? = null,
        warnings: String? = null,
        severity: String = "low",
        timestamp: Long = System.currentTimeMillis(),
    ) = ScanEntity(
        imagePath = "/tmp/x.jpg",
        diseaseName = diseaseName,
        diseaseNameRu = diseaseNameRu,
        confidence = confidence,
        description = "d",
        descriptionRu = "д",
        treatment = "[]",
        treatmentRu = "[]",
        prevention = "[]",
        preventionRu = "[]",
        isHealthy = isHealthy,
        allProbs = allProbs,
        displayedClassKey = displayedClassKey,
        warnings = warnings,
        severity = severity,
        timestamp = timestamp,
    )

    // ─── JSON parse helpers ──────────────────────────────────────────────

    @Test
    fun parseStringList_validInvalidEmpty() {
        assertThat(repo.parseStringList("""["a","b"]""")).containsExactly("a", "b").inOrder()
        assertThat(repo.parseStringList("not-json")).isEmpty()
        assertThat(repo.parseStringList("[]")).isEmpty()
    }

    @Test
    fun parseAllProbs_validInvalidNull() {
        val probs = repo.parseAllProbs("""{"leaf_spot":0.7,"healthy":0.3}""")
        assertThat(probs).hasSize(2)
        assertThat(probs["leaf_spot"]!!).isWithin(1e-4f).of(0.7f)
        assertThat(repo.parseAllProbs("garbage")).isEmpty()
        assertThat(repo.parseAllProbs(null)).isEmpty()
    }

    @Test
    fun parseWarnings_validAndNull() {
        assertThat(repo.parseWarnings("""["low_confidence","blurry_image"]"""))
            .containsExactly("low_confidence", "blurry_image")
        assertThat(repo.parseWarnings(null)).isEmpty()
    }

    @Test
    fun parseRegions_roundTrips() {
        val regions = repo.parseRegions("""[{"x":1,"y":2,"width":3,"height":4,"class":"leaf_spot"}]""")
        assertThat(regions).hasSize(1)
        assertThat(regions[0].x).isEqualTo(1)
        assertThat(regions[0].width).isEqualTo(3)
        assertThat(repo.parseRegions(null)).isEmpty()
        assertThat(repo.parseRegions("oops")).isEmpty()
    }

    // ─── Verdict rules ───────────────────────────────────────────────────

    @Test
    fun topClassKey_picksArgmax() {
        assertThat(repo.topClassKey(scan(allProbs = """{"leaf_spot":0.7,"healthy":0.3}""")))
            .isEqualTo("leaf_spot")
        assertThat(repo.topClassKey(scan(allProbs = null))).isNull()
    }

    @Test
    fun isNotAPlantScan_detectsAllThreeSignals() {
        // 1. canonical class key from all_probs
        assertThat(repo.isNotAPlantScan(scan(allProbs = """{"not_a_plant":0.9,"healthy":0.1}""")))
            .isTrue()
        // 2. localised English display name
        assertThat(repo.isNotAPlantScan(scan(diseaseName = "Not a Plant"))).isTrue()
        // 3. localised Russian display name
        assertThat(repo.isNotAPlantScan(scan(diseaseName = "X", diseaseNameRu = "Не растение")))
            .isTrue()
        // a real diagnosis is not NAP
        assertThat(repo.isNotAPlantScan(scan(diseaseName = "Leaf Spot"))).isFalse()
        // Legacy inconsistent rows prefer their explicitly persisted class key.
        assertThat(
            repo.isNotAPlantScan(
                scan(
                    allProbs = """{"not_a_plant":0.9,"leaf_spot":0.1}""",
                    displayedClassKey = "leaf_spot",
                )
            )
        ).isFalse()
    }

    @Test
    fun scanToProgressionPayload_buildsServerShape() {
        val payload = repo.scanToProgressionPayload(
            scan(
                allProbs = """{"leaf_spot":0.7}""",
                confidence = 0.7f,
                isHealthy = false,
                severity = "medium",
                warnings = """["low_confidence"]""",
            ),
        )
        assertThat(payload["class_name"]).isEqualTo("leaf_spot")
        assertThat(payload["is_healthy"]).isEqualTo(false)
        assertThat(payload["severity"]).isEqualTo("medium")
        assertThat(payload["abstained"]).isEqualTo(false)
        assertThat(payload["abstention_reason"]).isNull()
        // confidence is Float -> Double widened, so 0.7f != 0.7 exactly.
        assertThat(payload["confidence"] as Double).isWithin(1e-6).of(0.7)
        @Suppress("UNCHECKED_CAST")
        assertThat(payload["warnings"] as List<String>).containsExactly("low_confidence")
    }

    // ─── saveScan mapping ────────────────────────────────────────────────

    @Test
    fun saveScan_mapsAnalysisResponseFields() = runBlocking {
        val response = AnalysisResponse(
            classKey = "leaf_spot",
            diseaseName = "Leaf Spot",
            diseaseNameRu = "Пятнистость",
            confidence = 0.83f,
            isHealthy = false,
            severity = "high",
            scanId = "srv-1",
            allProbs = mapOf("leaf_spot" to 0.83f, "healthy" to 0.1f),
            detection = Detection(
                isDiseased = true,
                imageWidth = 800,
                imageHeight = 600,
                regions = listOf(DetectionRegion(10, 20, 30, 40, "leaf_spot", 0.9f)),
                primaryRegion = DetectionRegion(10, 20, 30, 40, "leaf_spot", 0.9f),
            ),
        )

        val id = repo.saveScan("/path/to/image.jpg", response)
        assertThat(id).isGreaterThan(0L)

        val stored = dao.lastInserted!!
        assertThat(stored.clientScanId).isEqualTo("srv-1")
        assertThat(stored.displayedClassKey).isEqualTo("leaf_spot")
        assertThat(stored.imagePath).isEqualTo("/path/to/image.jpg")
        assertThat(stored.diseaseName).isEqualTo("Leaf Spot")
        assertThat(stored.confidence).isWithin(1e-5f).of(0.83f)
        assertThat(stored.isHealthy).isFalse()
        assertThat(stored.severity).isEqualTo("high")
        assertThat(stored.detectionImageWidth).isEqualTo(800)
        assertThat(stored.detectionImageHeight).isEqualTo(600)
        assertThat(stored.primaryRegionIndex).isEqualTo(0)
        assertThat(stored.allProbs).contains("leaf_spot")
    }

    @Test
    fun saveScan_fallsBackToRandomScanIdWhenServerOmitsIt() = runBlocking {
        repo.saveScan("/p.jpg", AnalysisResponse(diseaseName = "Leaf Spot", scanId = null))
        // A local opaque capability slot remains available for legacy/community code.
        assertThat(dao.lastInserted!!.clientScanId).isNotEmpty()
    }

    @Test
    fun feedbackReceiptIsStoredByLocalRowAndConsumedOnce() = runBlocking {
        repo.saveScan(
            "/p.jpg",
            AnalysisResponse(
                diseaseName = "Leaf Spot",
                scanId = "community-capability",
                feedbackReceipt = "r".repeat(43),
            ),
        )
        val scan = requireNotNull(dao.lastInserted)

        assertThat(receiptStore.values.keys).containsExactly(scan.id.toString())
        assertThat(receiptStore.values.keys).doesNotContain("community-capability")
        assertThat(repo.feedbackReceipt(scan)).isEqualTo("r".repeat(43))

        repo.consumeFeedbackReceipt(scan)
        assertThat(repo.feedbackReceipt(scan)).isNull()
    }

    @Test
    fun deletingLocalHistoryAlsoDeletesFeedbackCapabilities() = runBlocking {
        repo.saveScan(
            "/p.jpg",
            AnalysisResponse(diseaseName = "Leaf Spot", feedbackReceipt = "a".repeat(43)),
        )
        val first = requireNotNull(dao.lastInserted)
        repo.deleteScan(first.id)
        assertThat(receiptStore.values).isEmpty()

        repo.saveScan(
            "/q.jpg",
            AnalysisResponse(diseaseName = "Healthy", feedbackReceipt = "b".repeat(43)),
        )
        assertThat(receiptStore.values).isNotEmpty()
        repo.clearAll()
        assertThat(receiptStore.values).isEmpty()
    }

    @Test
    fun saveScan_persistsExplicitAbstentionWithoutRoomMigration(): Unit = runBlocking {
        repo.saveScan(
            "/p.jpg",
            AnalysisResponse(
                classKey = "leaf_spot",
                diseaseName = "Unidentified Condition",
                confidence = 0.72f,
                warnings = listOf("nap_ood_gated"),
                abstained = true,
                abstentionReason = "ood",
                treatment = listOf("Disease-specific action must not persist"),
                toxicToPets = true,
                toxicitySource = "db",
            ),
        )
        val stored = dao.lastInserted!!
        assertThat(repo.topClassKey(stored)).isNull() // no fabricated distribution
        assertThat(repo.verdictClassKey(stored)).isEqualTo("leaf_spot")
        assertThat(repo.isAbstainedScan(stored)).isTrue()
        assertThat(repo.isInconclusiveScan(stored)).isTrue()
        assertThat(repo.isNotAPlantScan(stored)).isFalse()
        assertThat(repo.parseStringList(stored.treatment)).isEmpty()
        assertThat(stored.toxicToPets).isFalse()
        assertThat(stored.toxicitySource).isEqualTo("unknown")
        assertThat(repo.parseWarnings(stored.warnings)).containsAtLeast(
            "nap_ood_gated",
            "abstained",
            "abstention_reason:ood",
        )
    }

    @Test
    fun legacyWarnings_distinguishInconclusiveFromNaturalNap() {
        val ood = scan(
            displayedClassKey = "leaf_spot",
            warnings = """["nap_ood_gated"]""",
        )
        val blur = scan(
            displayedClassKey = "not_a_plant",
            warnings = """["blurry_image"]""",
        )
        val naturalNap = scan(displayedClassKey = "not_a_plant")

        assertThat(repo.abstentionReason(ood)).isEqualTo("ood")
        assertThat(repo.isInconclusiveScan(ood)).isTrue()
        assertThat(repo.abstentionReason(blur)).isEqualTo("image_quality")
        assertThat(repo.isNotAPlantScan(blur)).isFalse()
        assertThat(repo.isNotAPlantScan(naturalNap)).isTrue()
    }

    // ─── Stats rollup ────────────────────────────────────────────────────

    @Test
    fun getStats_separatesHealthyDiseasedAndNotAPlant() = runBlocking {
        dao.insert(scan(diseaseName = "Healthy", isHealthy = true))
        dao.insert(scan(diseaseName = "Leaf Spot", isHealthy = false))
        dao.insert(scan(diseaseName = "Leaf Spot", isHealthy = false))
        dao.insert(scan(diseaseName = "Not a Plant", isHealthy = false))
        dao.insert(
            scan(
                diseaseName = "Leaf Spot",
                displayedClassKey = "leaf_spot",
                warnings = """["nap_ood_gated"]""",
            )
        )

        val stats = repo.getStats()
        assertThat(stats.totalScans).isEqualTo(3) // healthy + diseased, excludes NAP
        assertThat(stats.healthyCount).isEqualTo(1)
        assertThat(stats.diseasedCount).isEqualTo(2)
        assertThat(stats.notAPlantCount).isEqualTo(1)
        assertThat(stats.mostCommonDisease).isEqualTo("Leaf Spot")
    }

    @Test
    fun getAllScans_hidesInconclusiveArgmaxButKeepsAcceptedRows() = runBlocking {
        dao.insert(scan(diseaseName = "Healthy", isHealthy = true))
        dao.insert(
            scan(
                diseaseName = "Leaf Spot",
                displayedClassKey = "leaf_spot",
                warnings = """["abstention_reason:ood"]""",
            )
        )

        val history = repo.getAllScans()
        assertThat(history).hasSize(1)
        assertThat(history.single().diseaseName).isEqualTo("Healthy")
    }

    private class FakeReceiptStore : FeedbackReceiptStore {
        val values = mutableMapOf<String, String>()

        override fun put(scanToken: String, receipt: String) {
            values[scanToken] = receipt
        }

        override fun get(scanToken: String): String? = values[scanToken]

        override fun remove(scanToken: String) {
            values.remove(scanToken)
        }

        override fun clear() {
            values.clear()
        }
    }
}
