package com.plantdiseases.app.util

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Robolectric coverage of [ResponseCache.peek] — the stale-while-revalidate
 * read that lets the home screen paint a stale bundle instantly on a slow
 * connection, then refresh only when [ResponseCache.Peek.fresh] is false.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ResponseCacheTest {

    data class Box(val v: Int)

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Before
    fun setUp() {
        ResponseCache.init(context)
    }

    @Test
    fun peek_returnsNull_onMiss() {
        assertThat(ResponseCache.peek("missing-key", Box::class.java)).isNull()
    }

    @Test
    fun peek_freshWithinTtl() {
        ResponseCache.put("k-fresh", Box(7), ttlMs = 60_000)
        val hit = ResponseCache.peek("k-fresh", Box::class.java)
        assertThat(hit).isNotNull()
        assertThat(hit!!.value).isEqualTo(Box(7))
        assertThat(hit.fresh).isTrue()
    }

    @Test
    fun peek_servesStaleButFlagsNotFresh_afterExpiry() {
        // ttl already in the past → expired, but peek must still return the
        // value (so the UI paints) with fresh=false (so the caller refetches).
        ResponseCache.put("k-stale", Box(3), ttlMs = 1)
        Thread.sleep(5)
        val hit = ResponseCache.peek("k-stale", Box::class.java)
        assertThat(hit).isNotNull()
        assertThat(hit!!.value).isEqualTo(Box(3))
        assertThat(hit.fresh).isFalse()
    }

    @Test
    fun peek_doesNotDeleteExpiredEntry() {
        // Unlike get(), peek leaves the stale file in place so repeated stale
        // reads keep working until a fresh fetch overwrites it.
        ResponseCache.put("k-keep", Box(9), ttlMs = 1)
        Thread.sleep(5)
        ResponseCache.peek("k-keep", Box::class.java)
        assertThat(ResponseCache.peek("k-keep", Box::class.java)).isNotNull()
    }
}
