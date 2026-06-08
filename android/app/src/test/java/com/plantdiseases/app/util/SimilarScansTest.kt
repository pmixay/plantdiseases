package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.CommunityScanItem
import org.junit.Test

/**
 * Pure-JVM coverage of [SimilarScans.filterSimilar] — the "similar scans
 * near you" gate. The contract that matters: the strip never shows a
 * different disease, only filters species when both sides know it, and is
 * lenient about candidates that lack species metadata so the strip isn't
 * needlessly empty.
 */
class SimilarScansTest {

    private fun item(
        scanId: String,
        disease: String,
        species: String? = null,
    ) = CommunityScanItem(scanId = scanId, disease = disease, species = species)

    private val feed = listOf(
        item("a", "leaf_spot", "Ficus elastica"),
        item("b", "leaf_spot", "Monstera deliciosa"),
        item("c", "leaf_spot", null),
        item("d", "rust", "Ficus elastica"),
        item("e", "healthy", null),
    )

    @Test
    fun keepsOnlyMatchingDisease() {
        val out = SimilarScans.filterSimilar(feed, disease = "leaf_spot", species = null)
        assertThat(out.map { it.scanId }).containsExactly("a", "b", "c").inOrder()
    }

    @Test
    fun neverLeaksOtherDiseasesEvenWhenSpeciesMatches() {
        // "rust on Ficus" must not appear in a "leaf_spot on Ficus" strip.
        val out = SimilarScans.filterSimilar(feed, disease = "leaf_spot", species = "Ficus elastica")
        assertThat(out.map { it.scanId }).doesNotContain("d")
    }

    @Test
    fun speciesNarrowsButKeepsUnknownSpeciesCandidates() {
        // Same species (a) and species-less candidates (c) pass; a different
        // known species (b) is dropped. Leniency keeps c so the strip fills.
        val out = SimilarScans.filterSimilar(feed, disease = "leaf_spot", species = "Ficus elastica")
        assertThat(out.map { it.scanId }).containsExactly("a", "c").inOrder()
    }

    @Test
    fun speciesMatchIsCaseAndWhitespaceInsensitive() {
        val out = SimilarScans.filterSimilar(feed, disease = "leaf_spot", species = "  ficus ELASTICA  ")
        assertThat(out.map { it.scanId }).containsExactly("a", "c").inOrder()
    }

    @Test
    fun blankSpeciesBehavesLikeNoSpeciesFilter() {
        val out = SimilarScans.filterSimilar(feed, disease = "leaf_spot", species = "   ")
        assertThat(out.map { it.scanId }).containsExactly("a", "b", "c").inOrder()
    }

    @Test
    fun emptyFeedYieldsEmpty() {
        assertThat(SimilarScans.filterSimilar(emptyList(), "leaf_spot", "Ficus elastica")).isEmpty()
    }
}
