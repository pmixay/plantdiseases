package com.plantdiseases.app.data.remote

import com.google.common.truth.Truth.assertThat
import okhttp3.Call
import okhttp3.Connection
import okhttp3.Interceptor
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Pure-JVM tests for [RetryInterceptor] using a scripted fake
 * [Interceptor.Chain]. This pins the documented retry contract:
 *  - GETs retry transient transport failures and 429/503;
 *  - POSTs are NEVER retried on an IOException (the server may already
 *    have processed the non-idempotent body) but DO retry once on an
 *    explicit 429/503;
 *  - the loop stops after the configured number of attempts.
 */
class RetryInterceptorTest {

    private sealed interface Outcome {
        data class Status(val code: Int, val retryAfter: String? = null) : Outcome
        object Fail : Outcome
    }

    private class FakeChain(
        private val req: Request,
        private val script: MutableList<Outcome>,
    ) : Interceptor.Chain {
        var proceedCount = 0
            private set

        override fun request(): Request = req

        override fun proceed(request: Request): Response {
            proceedCount++
            return when (val outcome = script.removeAt(0)) {
                is Outcome.Fail -> throw IOException("simulated transport failure")
                is Outcome.Status -> Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(outcome.code)
                    .message("status ${outcome.code}")
                    .apply { if (outcome.retryAfter != null) header("Retry-After", outcome.retryAfter) }
                    .body("".toResponseBody(null))
                    .build()
            }
        }

        override fun connection(): Connection? = null
        override fun call(): Call = throw UnsupportedOperationException()
        override fun connectTimeoutMillis(): Int = 0
        override fun withConnectTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
        override fun readTimeoutMillis(): Int = 0
        override fun withReadTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
        override fun writeTimeoutMillis(): Int = 0
        override fun withWriteTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
    }

    private fun get() = Request.Builder().url("http://localhost/").get().build()
    private fun post() =
        Request.Builder().url("http://localhost/").post("x".toRequestBody(null)).build()

    // Tiny delays so the backoff sleeps don't slow the suite.
    private fun interceptor() = RetryInterceptor(maxRetries = 3, initialDelayMs = 1, maxDelayMs = 4)

    @Test
    fun get_success_firstTry_noRetry() {
        val chain = FakeChain(get(), mutableListOf(Outcome.Status(200)))
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(200)
        assertThat(chain.proceedCount).isEqualTo(1)
    }

    @Test
    fun get_retriesOn503_thenSucceeds() {
        val chain = FakeChain(get(), mutableListOf(Outcome.Status(503), Outcome.Status(200)))
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(200)
        assertThat(chain.proceedCount).isEqualTo(2)
    }

    @Test
    fun get_exhaustsRetriesOn503() {
        // maxRetries=3 -> attempts 0..3 == 4 proceeds, final 503 returned.
        val chain = FakeChain(
            get(),
            mutableListOf(Outcome.Status(503), Outcome.Status(503), Outcome.Status(503), Outcome.Status(503)),
        )
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(503)
        assertThat(chain.proceedCount).isEqualTo(4)
    }

    @Test
    fun get_retriesOnIOException_thenSucceeds() {
        val chain = FakeChain(get(), mutableListOf(Outcome.Fail, Outcome.Status(200)))
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(200)
        assertThat(chain.proceedCount).isEqualTo(2)
    }

    @Test
    fun post_doesNotRetryOnIOException() {
        val chain = FakeChain(post(), mutableListOf(Outcome.Fail, Outcome.Status(200)))
        assertThrows(IOException::class.java) { interceptor().intercept(chain) }
        // The second (200) outcome must never be consumed.
        assertThat(chain.proceedCount).isEqualTo(1)
    }

    @Test
    fun post_retriesOnceOn503_thenSucceeds() {
        val chain = FakeChain(post(), mutableListOf(Outcome.Status(503), Outcome.Status(200)))
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(200)
        assertThat(chain.proceedCount).isEqualTo(2)
    }

    @Test
    fun post_stopsAfterOneRetryOn503() {
        // effectiveRetries for POST == min(3,1) == 1 -> 2 proceeds max.
        val chain = FakeChain(
            post(),
            mutableListOf(Outcome.Status(503), Outcome.Status(503), Outcome.Status(200)),
        )
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(503)
        assertThat(chain.proceedCount).isEqualTo(2)
    }

    @Test
    fun honorsRetryAfterHeader_andStillSucceeds() {
        val chain = FakeChain(
            get(),
            mutableListOf(Outcome.Status(503, retryAfter = "0"), Outcome.Status(200)),
        )
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(200)
        assertThat(chain.proceedCount).isEqualTo(2)
    }

    @Test
    fun nonRetryableErrorCode_passesThroughImmediately() {
        // 400/404/500 are not retried -> single proceed, returned as-is.
        val chain = FakeChain(get(), mutableListOf(Outcome.Status(400)))
        val response = interceptor().intercept(chain)
        assertThat(response.code).isEqualTo(400)
        assertThat(chain.proceedCount).isEqualTo(1)
    }
}
