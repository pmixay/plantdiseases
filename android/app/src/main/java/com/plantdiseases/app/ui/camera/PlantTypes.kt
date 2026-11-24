package com.plantdiseases.app.ui.camera

/**
 * The five plant types the Light Meter (light band) and the Water Calculator
 * (watering) share. The values index both screens' tables, in this order.
 */
object PlantTypes {
    const val GENERAL = 0
    const val SUCCULENT = 1
    const val TROPICAL = 2
    const val FERN = 3
    const val VEGETABLE = 4

    /** Heuristic genus→bucket mapping. Lower-cases the genus
     *  (first whitespace-separated token of the binomial) and
     *  routes the popular indoor genera into the right bucket;
     *  anything else falls through to "general". Adding more
     *  genera here is the right place to grow coverage. */
    fun fromName(scientificName: String): Int {
        val genus = scientificName.trim().substringBefore(' ').lowercase()
        return when (genus) {
            // Common indoor succulents.
            "echeveria", "sedum", "crassula", "aloe", "haworthia",
            "kalanchoe", "sansevieria", "agave", "lithops",
            "euphorbia" -> SUCCULENT
            // Tropical aroids + foliage favourites.
            "monstera", "philodendron", "epipremnum", "pothos",
            "calathea", "maranta", "spathiphyllum", "anthurium",
            "ficus", "syngonium", "alocasia", "dieffenbachia",
            "dracaena", "schefflera", "begonia", "peperomia",
            "saintpaulia" -> TROPICAL
            // Ferns.
            "nephrolepis", "adiantum", "asplenium", "platycerium",
            "pteris", "davallia", "polystichum" -> FERN
            // Edible / vegetable seedlings often started indoors.
            "solanum", "capsicum", "lactuca", "ocimum", "mentha",
            "petroselinum" -> VEGETABLE
            else -> GENERAL
        }
    }
}
