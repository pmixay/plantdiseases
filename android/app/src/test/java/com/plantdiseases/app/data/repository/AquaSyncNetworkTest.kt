package com.plantdiseases.app.data.repository

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.testutil.FakeScanDao
import com.plantdiseases.app.util.AquaModelManager
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.security.MessageDigest

/**
 * Drives the Aqua model sync (manifest GET → streaming model GET → install)
 * against a real [MockWebServer] through the migrated Ktor path. Needs a
 * Robolectric [Context] because [AquaModelManager.install] writes the model +
 * classes sidecar to `filesDir` and records the installed sha in prefs.
 *
 * Verifies two migration-specific things: the manifest's `classes` field
 * (Gson [com.google.gson.JsonObject], read via GsonJsonObjectSerializer)
 * survives the kotlinx round-trip into the sidecar, and the multi-MB body is
 * streamed via `bodyAsChannel().toInputStream()`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AquaSyncNetworkTest {

    private lateinit var server: MockWebServer
    private lateinit var repo: ScanRepository
    private lateinit var context: Context

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        repo = ScanRepository(FakeScanDao(), PlantApiClient(server.url("/").toString()))
        context = ApplicationProvider.getApplicationContext()
    }

    @After
    fun teardown() {
        server.shutdown()
    }

    @Test
    fun syncAquaModel_downloadsAndInstalls_whenManifestFresh() {
        val modelBytes = ByteArray(2048) { (it % 256).toByte() }
        val sha = MessageDigest.getInstance("SHA-256")
            .digest(modelBytes)
            .joinToString("") { "%02x".format(it) }

        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"available":true,"version":"7","sha256":"$sha","size_bytes":2048,""" +
                        """"classes":{"classes":["healthy","leaf_spot"],"input_size":224}}""",
                ),
        )
        // Model bytes streamed back as an octet-stream body.
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/octet-stream")
                .setBody(Buffer().write(modelBytes)),
        )

        val result = runBlocking { repo.syncAquaModel(context) }

        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()).isTrue() // a new model was installed
        assertThat(AquaModelManager.isUpToDate(context, sha)).isTrue()
        assertThat(server.takeRequest().path).isEqualTo("/api/aqua/manifest")
        assertThat(server.takeRequest().path).isEqualTo("/api/aqua/model")
    }

    @Test
    fun syncAquaModel_noop_whenManifestUnavailable() {
        server.enqueue(
            MockResponse().setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"available":false}"""),
        )
        val result = runBlocking { repo.syncAquaModel(context) }
        assertThat(result.isSuccess).isTrue()
        assertThat(result.getOrNull()).isFalse() // nothing to install
    }
}
