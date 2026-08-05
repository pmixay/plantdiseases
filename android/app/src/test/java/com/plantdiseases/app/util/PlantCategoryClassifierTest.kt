package com.plantdiseases.app.util

import org.junit.Assert.assertEquals
import org.junit.Test

class PlantCategoryClassifierTest {

    @Test
    fun `tomato classifies as fruiting via genus and via russian common name`() {
        assertEquals(
            PlantCategory.FRUITING,
            PlantCategoryClassifier.classify("Solanum lycopersicum", "Tomato"),
        )
        assertEquals(
            PlantCategory.FRUITING,
            PlantCategoryClassifier.classify("Solanum lycopersicum", "Томат"),
        )
    }

    @Test
    fun `potato overrides its fruiting genus to root`() {
        assertEquals(
            PlantCategory.ROOT,
            PlantCategoryClassifier.classify("Solanum tuberosum", "Potato"),
        )
    }

    @Test
    fun `aloe vera and snake plant classify as succulent`() {
        assertEquals(PlantCategory.SUCCULENT, PlantCategoryClassifier.classify("Aloe vera", "Aloe vera"))
        assertEquals(
            PlantCategory.SUCCULENT,
            PlantCategoryClassifier.classify("Dracaena trifasciata", "Сансевиерия"),
        )
    }

    @Test
    fun `peace lily classifies as flowering not foliage despite foliage-heavy genus map`() {
        assertEquals(
            PlantCategory.FLOWERING,
            PlantCategoryClassifier.classify("Spathiphyllum wallisii", "Peace lily"),
        )
    }

    @Test
    fun `monstera classifies as foliage`() {
        assertEquals(
            PlantCategory.FOLIAGE,
            PlantCategoryClassifier.classify("Monstera deliciosa", "Swiss cheese plant"),
        )
    }

    @Test
    fun `basil classifies as herb in english and russian`() {
        assertEquals(PlantCategory.HERB, PlantCategoryClassifier.classify("Ocimum basilicum", "Basil"))
        assertEquals(PlantCategory.HERB, PlantCategoryClassifier.classify("Ocimum basilicum", "Базилик"))
    }

    @Test
    fun `unknown species and null inputs default to foliage`() {
        assertEquals(PlantCategory.FOLIAGE, PlantCategoryClassifier.classify("Zzyzx nonexistus", "Mystery plant"))
        assertEquals(PlantCategory.FOLIAGE, PlantCategoryClassifier.classify(null, null))
    }
}
