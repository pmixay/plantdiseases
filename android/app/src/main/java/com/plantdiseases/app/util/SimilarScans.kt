package com.plantdiseases.app.util

import com.plantdiseases.app.data.model.CommunityScanItem

/**
 * Similarity filtering for the "Similar scans near you" strip on the result
 * screen. The strip is meant to show OTHER people's scans of the *same
 * problem on the same kind of plant* — so we narrow the regional feed to the
 * current verdict's disease and, when known, the same species.
 *
 * Kept deliberately lenient so the strip isn't empty when neighbours lack
 * metadata:
 *   • disease must match exactly (a "leaf spot" strip never shows "rust"),
 *   • species only filters when BOTH the current scan and the candidate carry
 *     a scientific name — a candidate without a recorded species passes
 *     through rather than being dropped.
 *
 * This is a pure function (no Android deps) so the contract is unit-tested.
 */
object SimilarScans {

    fun filterSimilar(
        items: List<CommunityScanItem>,
        disease: String,
        species: String?,
    ): List<CommunityScanItem> {
        val wantSpecies = species?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
        return items.filter { item ->
            if (item.disease != disease) return@filter false
            val itemSpecies = item.species?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }
            // Lenient: only reject when both sides name a species and they differ.
            wantSpecies == null || itemSpecies == null || itemSpecies == wantSpecies
        }
    }
}
