package com.plantdiseases.app.data.repository

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.SpeciesDiseasesResponse
import com.plantdiseases.app.data.model.SpeciesInfoResponse
import com.plantdiseases.app.data.model.SpeciesProfileResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.util.ResponseCache
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/** Regression coverage of SpeciesRepository and its disk cache. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class SpeciesRepositoryNetworkTest {
    private lateinit var server: MockWebServer
    private lateinit var client: PlantApiClient
    private lateinit var repository: SpeciesRepository
    private lateinit var cacheDirectory: File

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        cacheDirectory = File(context.filesDir, "api_cache")
        cacheDirectory.deleteRecursively()
        ResponseCache.init(context)
        server = MockWebServer().apply { start() }
        client = PlantApiClient(server.url("/").toString())
        repository = SpeciesRepository(client)
    }

    @After
    fun tearDown() {
        client.httpClient.close()
        server.shutdown()
        cacheDirectory.deleteRecursively()
    }

    @Test
    fun speciesInfo_reusesExistingDiskCacheAcrossRepositoryInstances() = runBlocking {
        val cached = SpeciesInfoResponse(available = true, title = "Cached ficus")
        ResponseCache.put("species:v4:ficus elastica|en", cached, ResponseCache.TTL_SPECIES_MS)

        val result = SpeciesRepository(client).getSpeciesInfo(" Ficus elastica ", "en")

        assertThat(result.getOrThrow()).isEqualTo(cached)
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun speciesInfo_unavailableRetriesAndKeepsLocalesSeparate() = runBlocking {
        enqueueJson("""{"available":false}""")
        enqueueJson("""{"available":true,"title":"English ficus"}""")
        enqueueJson("""{"available":true,"title":"Russian ficus"}""")

        assertThat(repository.getSpeciesInfo("Ficus elastica", "en").getOrThrow().available).isFalse()
        assertThat(repository.getSpeciesInfo("Ficus elastica", "en").getOrThrow().title)
            .isEqualTo("English ficus")
        assertThat(repository.getSpeciesInfo("Ficus elastica", "ru").getOrThrow().title)
            .isEqualTo("Russian ficus")
        assertThat(server.requestCount).isEqualTo(3)
        assertThat(server.takeRequest().path).contains("locale=en")
        assertThat(server.takeRequest().path).contains("locale=en")
        assertThat(server.takeRequest().path).contains("locale=ru")
    }

    @Test
    fun speciesInfo_genusLevelOrPhotolessAnswerIsCachedOnlyBriefly() = runBlocking {
        enqueueJson("""{"available":true,"title":"Rosa","level":"genus","image_url":"api/plant-photo?u=x"}""")
        repository.getSpeciesInfo("Rosa abietina", "en").getOrThrow()
        assertThat(cacheDirectory.listFiles()!!.single().readText())
            .contains("\"ttlMs\":${ResponseCache.TTL_SPECIES_DEGRADED_MS}")
    }

    @Test
    fun speciesInfo_completeAnswerUsesLongTtl() = runBlocking {
        enqueueJson("""{"available":true,"title":"Rosa abietina","level":"species","image_url":"api/plant-photo?u=x"}""")
        repository.getSpeciesInfo("Rosa abietina", "en").getOrThrow()
        assertThat(cacheDirectory.listFiles()!!.single().readText())
            .contains("\"ttlMs\":${ResponseCache.TTL_SPECIES_MS}")
    }

    @Test
    fun speciesDiseases_cacheOnlyReadNeverFetchesAndRecognizesExistingKey() = runBlocking {
        assertThat(repository.cachedSpeciesDiseases("Ficus elastica", "en")).isNull()
        val cached = SpeciesDiseasesResponse(available = true, species = "Ficus elastica")
        ResponseCache.put("species_diseases:v3:ficus elastica|en", cached, ResponseCache.TTL_SPECIES_MS)

        assertThat(repository.cachedSpeciesDiseases(" Ficus elastica ", "en")).isEqualTo(cached)
        assertThat(repository.cachedSpeciesDiseases("Ficus elastica", "ru")).isNull()
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun speciesDiseases_unavailableUsesShortTtlAndSurvivesRepositoryRecreation() = runBlocking {
        enqueueJson("""{"available":false,"species":"Ficus elastica"}""")
        val response = repository.fetchSpeciesDiseases("Ficus elastica", "Rubber plant", "en").getOrThrow()

        assertThat(SpeciesRepository(client).cachedSpeciesDiseases("Ficus elastica", "en"))
            .isEqualTo(response)
        assertThat(cacheDirectory.listFiles()!!.single().readText())
            .contains("\"ttlMs\":${ResponseCache.TTL_SPECIES_MISS_MS}")
        assertThat(server.requestCount).isEqualTo(1)
        val request = server.takeRequest()
        assertThat(request.path).startsWith("/api/species-diseases?")
        assertThat(request.path).contains("common=Rubber")
    }

    @Test
    fun speciesProfile_reusesBilingualDiskCacheAfterLanguageSwitch() = runBlocking {
        val cached = SpeciesProfileResponse(available = true, species = "Ficus elastica")
        ResponseCache.put("species_profile:v5:ficus elastica", cached, ResponseCache.TTL_SPECIES_MS)

        assertThat(repository.fetchSpeciesProfile(" Ficus elastica ", null, "ru").getOrThrow())
            .isEqualTo(cached)
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test
    fun speciesProfile_httpFailureDoesNotCacheAndSuccessfulRetryUsesLongTtl() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(400))
        enqueueJson("""{"available":true,"species":"Ficus elastica"}""")

        assertThat(repository.fetchSpeciesProfile("Ficus elastica", null, "en").isFailure).isTrue()
        assertThat(cacheDirectory.listFiles()).isEmpty()
        assertThat(repository.fetchSpeciesProfile("Ficus elastica", null, "en").getOrThrow().available).isTrue()
        assertThat(cacheDirectory.listFiles()!!.single().readText())
            .contains("\"ttlMs\":${ResponseCache.TTL_SPECIES_MS}")
        assertThat(server.requestCount).isEqualTo(2)
        assertThat(server.takeRequest().path).contains("profile_version=4")
        assertThat(server.takeRequest().path).contains("profile_version=4")
    }

    private fun enqueueJson(body: String) {
        server.enqueue(
            MockResponse().setHeader("Content-Type", "application/json").setBody(body),
        )
    }
}
