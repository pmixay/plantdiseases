package com.plantdiseases.app.auth

import java.net.URI
import java.security.MessageDigest

/**
 * Canonical identity for the account issuer configured into this APK.
 *
 * The path is deliberately part of the identity: production and staging can
 * share a host while terminating under different ingress prefixes. Credential
 * stores use [storageKey] so a same-package update can never discover and send
 * a token issued for another configured account endpoint.
 */
@ConsistentCopyVisibility
data class AccountEndpointScope private constructor(
    val issuer: String,
    val storageKey: String,
) {
    companion object {
        fun from(rawBaseUrl: String): AccountEndpointScope {
            val parsed = URI(rawBaseUrl.trim()).normalize()
            val scheme = parsed.scheme?.lowercase()
            require(scheme == "https" || scheme == "http") {
                "Account API URL must use HTTP or HTTPS"
            }
            val host = parsed.host?.lowercase().orEmpty()
            require(host.isNotBlank()) { "Account API URL must have a host" }
            require(parsed.userInfo == null && parsed.query == null && parsed.fragment == null) {
                "Account API URL must not contain credentials, a query, or a fragment"
            }
            val port = when {
                parsed.port == 443 && scheme == "https" -> -1
                parsed.port == 80 && scheme == "http" -> -1
                else -> parsed.port
            }
            val normalizedPath = parsed.path.orEmpty()
                .ifBlank { "/" }
                .let { if (it.startsWith('/')) it else "/$it" }
                .let { if (it.endsWith('/')) it else "$it/" }
            val issuer = URI(
                scheme,
                null,
                host,
                port,
                normalizedPath,
                null,
                null,
            ).toASCIIString()
            val digest = MessageDigest.getInstance("SHA-256")
                .digest(issuer.toByteArray(Charsets.UTF_8))
                .take(16)
            val storageKey = buildString(digest.size * 2) {
                digest.forEach { byte ->
                    val value = byte.toInt() and 0xff
                    append(HEX[value ushr 4])
                    append(HEX[value and 0x0f])
                }
            }
            return AccountEndpointScope(issuer, storageKey)
        }

        private const val HEX = "0123456789abcdef"
    }
}

/**
 * Compile-time allowlist for cross-update logout cleanup. The current endpoint
 * comes from the reviewed BuildConfig; the only additional issuer is the
 * fixed German staging channel that can be replaced by the mandatory
 * production update. No persisted or server-provided URL is ever trusted.
 */
object AccountLogoutEndpointRegistry {
    const val REVIEWED_STAGING_ACCOUNT_API_BASE_URL =
        "https://pdis.arbuz.lol/plantdisease-test/id/"

    fun additionalEndpoints(currentBaseUrl: String): List<String> {
        val current = AccountEndpointScope.from(currentBaseUrl)
        return listOf(REVIEWED_STAGING_ACCOUNT_API_BASE_URL)
            .filter { AccountEndpointScope.from(it) != current }
    }
}
