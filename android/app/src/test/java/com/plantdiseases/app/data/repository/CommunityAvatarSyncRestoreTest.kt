package com.plantdiseases.app.data.repository

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.PreparedRefresh
import com.plantdiseases.app.auth.RefreshTokenVault
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.remote.RetryInterceptor
import com.plantdiseases.app.testutil.FakeAccountApi
import com.plantdiseases.app.util.CommunityAvatarPrefs
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import com.plantdiseases.app.util.ProfileAvatarStore
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
import java.io.ByteArrayOutputStream

/**
 * After a reinstall the profile photo survives only on the server: a signed-in
 * member without a local photo gets the photo they show in Community back,
 * with "show in Community" on, and nothing is uploaded again.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class CommunityAvatarSyncRestoreTest {
    private lateinit var context: Context
    private lateinit var server: MockWebServer
    private lateinit var authRepository: AuthRepository
    private lateinit var store: ProfileAvatarStore
    private lateinit var sync: CommunityAvatarSync
    private lateinit var account: String

    @Before
    fun setup() {
        context = ApplicationProvider.getApplicationContext()
        server = MockWebServer().also { it.start() }
        authRepository = AuthRepository(FakeAccountApi(), FakeVault(), FakeInstallationId)
        runBlocking { authRepository.signIn(AuthProvider.YANDEX, "provider-token") }
        val client = PlantApiClient(
            server.url("/").toString(),
            retryInterceptor = RetryInterceptor(maxRetries = 0, initialDelayMs = 1, sleeper = {}),
        )
        store = ProfileAvatarStore(context, DefaultUploadImageSanitizer)
        sync = CommunityAvatarSync(context, authRepository, ForumRepository(client, authRepository), store)
        account = checkNotNull(sync.accountId())
    }

    @After
    fun teardown() {
        runBlocking { store.clear(account) }
        server.shutdown()
    }

    @Test
    fun serverPhotoIsRestoredShownAndNotUploadedAgain() = runBlocking {
        server.enqueue(me(avatarVersion = "1763560000123"))
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "image/jpeg").setBody(Buffer().write(jpeg())))

        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.RESTORED)

        assertThat(server.takeRequest().path).isEqualTo("/api/community/me/achievements")
        assertThat(server.takeRequest().path).isEqualTo("/api/community/avatars/ca0123456789abcdef0123456789abcd?v=1763560000123")
        assertThat(store.current(account)).isNotNull()
        assertThat(sync.isShown()).isTrue()
        assertThat(sync.shouldAsk()).isFalse()
        // The restored photo already is the published one: no upload, no withdrawal.
        assertThat(sync.sync()).isEqualTo(CommunityAvatarSync.Outcome.NOTHING)
        // Settled for this process: no further requests.
        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.NOTHING)
        assertThat(server.requestCount).isEqualTo(2)
    }

    @Test
    fun noServerPhotoLeavesEverythingAsItWas() = runBlocking {
        server.enqueue(me(avatarVersion = "null"))

        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.NOTHING)

        assertThat(store.current(account)).isNull()
        assertThat(CommunityAvatarPrefs(context).choice(account)).isNull()
        assertThat(sync.isShown()).isFalse()
        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.NOTHING)
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun aFailedRequestIsRetriedLater() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503).setBody("""{"detail":"unavailable"}"""))
        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.FAILED)
        assertThat(store.current(account)).isNull()

        server.enqueue(me(avatarVersion = "7"))
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "image/jpeg").setBody(Buffer().write(jpeg())))
        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.RESTORED)
    }

    @Test
    fun aPrivateChoiceOnThisDeviceIsRespected() = runBlocking {
        CommunityAvatarPrefs(context).setChoice(account, false, null)

        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.NOTHING)

        assertThat(server.requestCount).isEqualTo(0)
        assertThat(store.current(account)).isNull()
    }

    @Test
    fun guestsAreNotAsked() = runBlocking {
        authRepository.logout()

        assertThat(sync.restore()).isEqualTo(CommunityAvatarSync.RestoreOutcome.NOTHING)
        assertThat(server.requestCount).isEqualTo(0)
    }

    private fun me(avatarVersion: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(
            """{"author_token":"ca0123456789abcdef0123456789abcd","nickname":"Fern",
              "avatar_version":$avatarVersion,"level":1,"achievements":[]}""",
        )

    private fun jpeg(): ByteArray {
        val bitmap = Bitmap.createBitmap(256, 256, Bitmap.Config.ARGB_8888)
        return try {
            bitmap.eraseColor(Color.rgb(34, 139, 74))
            ByteArrayOutputStream().also { out ->
                check(bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out))
            }.toByteArray()
        } finally {
            bitmap.recycle()
        }
    }

    private class FakeVault : RefreshTokenVault {
        private var token: String? = null
        private var rotationId: String? = null

        override fun read(): String? = token

        override fun prepareRefresh(): PreparedRefresh? = token?.let { refresh ->
            PreparedRefresh(refresh, rotationId ?: "00000000-0000-4000-8000-000000000010".also { rotationId = it })
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
}
