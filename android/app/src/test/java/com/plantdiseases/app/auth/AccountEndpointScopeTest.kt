package com.plantdiseases.app.auth

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class AccountEndpointScopeTest {
    @Test
    fun equivalentBaseUrlsHaveOneCanonicalIssuer() {
        val first = AccountEndpointScope.from("HTTPS://Example.COM:443/a/../id")
        val second = AccountEndpointScope.from("https://example.com/id/")

        assertThat(first.issuer).isEqualTo("https://example.com/id/")
        assertThat(first.storageKey).isEqualTo(second.storageKey)
    }

    @Test
    fun ingressPathIsPartOfCredentialScope() {
        val production = AccountEndpointScope.from("https://example.com/id/")
        val staging = AccountEndpointScope.from("https://example.com/plantdisease-test/id/")

        assertThat(production.storageKey).isNotEqualTo(staging.storageKey)
        assertThat(production.issuer).isNotEqualTo(staging.issuer)
    }

    @Test
    fun productionBuildAlsoDrainsOnlyTheReviewedStagingIssuer() {
        val additional = AccountLogoutEndpointRegistry.additionalEndpoints(
            "https://production.example/id/",
        )

        assertThat(additional).containsExactly(
            AccountLogoutEndpointRegistry.REVIEWED_STAGING_ACCOUNT_API_BASE_URL,
        )
    }

    @Test
    fun stagingBuildDoesNotDuplicateItsCurrentIssuer() {
        val additional = AccountLogoutEndpointRegistry.additionalEndpoints(
            AccountLogoutEndpointRegistry.REVIEWED_STAGING_ACCOUNT_API_BASE_URL,
        )

        assertThat(additional).isEmpty()
    }

    @Test(expected = IllegalArgumentException::class)
    fun credentialBearingUrlFailsClosed() {
        AccountEndpointScope.from("https://user:secret@example.com/id/")
    }
}
