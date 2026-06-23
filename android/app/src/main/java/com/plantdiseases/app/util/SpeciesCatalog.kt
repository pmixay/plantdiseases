package com.plantdiseases.app.util

/**
 * Bundled list of popular houseplants, used as the always-available fallback
 * for the Home "species" carousel when the community-trending feed has no
 * nearby data (a fresh install, or a sparsely-scanned region). Mirrors how the
 * Disease guide carousel is backed by a bundled catalog so the row is never
 * empty.
 *
 * Each entry keys its photo + detail lookup off the [scientific] name (which
 * /api/species-info and SpeciesDetailActivity both resolve against Wikipedia /
 * the care databases), while [commonEn] / [commonRu] are what the card shows.
 */
object SpeciesCatalog {

    data class PopularSpecies(
        val scientific: String,
        val commonEn: String,
        val commonRu: String,
    )

    /** A spread of the most common, easily-recognised houseplants. Scientific
     *  names are chosen to resolve cleanly to a Wikipedia lead photo. */
    val popular: List<PopularSpecies> = listOf(
        PopularSpecies("Monstera deliciosa", "Swiss cheese plant", "Монстера"),
        PopularSpecies("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
        PopularSpecies("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularSpecies("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularSpecies("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
        PopularSpecies("Ficus lyrata", "Fiddle-leaf fig", "Фикус лировидный"),
        PopularSpecies("Ficus elastica", "Rubber plant", "Фикус каучуконосный"),
        PopularSpecies("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
        PopularSpecies("Aloe vera", "Aloe vera", "Алоэ вера"),
        PopularSpecies("Dracaena marginata", "Dragon tree", "Драцена окаймлённая"),
    )
}
