package com.plantdiseases.app.ui.species

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.plantdiseases.app.data.model.SpeciesProfileResponse
import org.junit.Test

class SpeciesProfileCompatibilityTest {

    @Test
    fun legacyCachedProfileWithoutLocalizedTextIsSafe() {
        val profile = Gson().fromJson(
            """{"available":true,"flowering":{"monthly_intensity":[0,0,0,0,0,0,0,0,0,0,0,0]}}""",
            SpeciesProfileResponse::class.java,
        )

        assertThat(profile.localizedText.orEmpty()).isEmpty()
    }
}
