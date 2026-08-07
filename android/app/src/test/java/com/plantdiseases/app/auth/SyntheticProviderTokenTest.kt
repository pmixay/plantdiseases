package com.plantdiseases.app.auth

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.account.AuthProvider
import org.junit.Test
import org.junit.Assert.assertThrows

class SyntheticProviderTokenTest {
    @Test
    fun tokenMatchesMockProviderContractAndIsStablePerInstallation() {
        val installationId = "01234567-89ab-cdef-0123-456789abcdef"

        assertThat(SyntheticProviderToken.forInstallation(AuthProvider.YANDEX, installationId))
            .isEqualTo("mock-yandex-generated-0123456789abcdef0123456789abcdef")
        assertThat(SyntheticProviderToken.forInstallation(AuthProvider.VK, installationId))
            .isEqualTo("mock-vk-generated-0123456789abcdef0123456789abcdef")
    }

    @Test
    fun malformedInstallationIdFailsClosed() {
        assertThrows(IllegalArgumentException::class.java) {
            SyntheticProviderToken.forInstallation(AuthProvider.YANDEX, "not-an-installation-id")
        }
    }
}
