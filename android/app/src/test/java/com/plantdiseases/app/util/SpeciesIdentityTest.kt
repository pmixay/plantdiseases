package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SpeciesIdentityTest {

    @Test
    fun manualCorrectionClearsCanonicalButEquivalentDraftDoesNot() {
        assertThat(
            SpeciesIdentity.manualEditClearsCanonical(
                editedDisplay = "Rubber plant",
                scientificName = "Ficus elastica",
                commonName = "Rubber plant",
            )
        ).isFalse()
        assertThat(
            SpeciesIdentity.manualEditClearsCanonical(
                editedDisplay = "Ficus elastica",
                scientificName = "Ficus elastica",
                commonName = "Rubber plant",
            )
        ).isFalse()
        assertThat(
            SpeciesIdentity.manualEditClearsCanonical(
                editedDisplay = "Monstera",
                scientificName = "Ficus elastica",
                commonName = "Rubber plant",
            )
        ).isTrue()
    }

    @Test
    fun restoredManualDraftWithNoCanonicalSelectionStaysManual() {
        assertThat(
            SpeciesIdentity.manualEditClearsCanonical(
                editedDisplay = "Monstera",
                scientificName = null,
                commonName = null,
            )
        ).isFalse()
        assertThat(SpeciesIdentity.canonicalDisplay(null, null)).isNull()
    }
}
