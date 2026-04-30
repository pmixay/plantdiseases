package com.plantdiseases.app.data

/**
 * Local mirror of `server/diseases_data.py::PEST_INFO` and the
 * per-disease `possible_pest_causes` lists.
 *
 * Pests are not classifier outputs — they're surfaced under each
 * diagnosis as a hint section ("could the same look come from a
 * pest?"). The server returns the same data in
 * `AnalysisResponse.possible_pest_causes`; this local table makes the
 * UI work for cached/history scans where the JSON wasn't persisted.
 *
 * Keep this in sync with the server registry.
 */
object PestCauses {

    /** Bilingual display info for one pest key. */
    data class Pest(
        val key: String,
        val nameEn: String,
        val nameRu: String,
        val summaryEn: String,
        val summaryRu: String,
        val hintEn: String,
        val hintRu: String,
    )

    private val PESTS: Map<String, Pest> = mapOf(
        "spider_mites" to Pest(
            key = "spider_mites",
            nameEn = "Spider Mites",
            nameRu = "Паутинный клещ",
            summaryEn = "Tiny mites that cause fine webbing and stippled yellow spots on leaves; hide on the leaf underside.",
            summaryRu = "Мелкие клещи: тонкая паутина и светлые точки на листьях, прячутся снизу листа.",
            hintEn = "Look on leaf undersides with a magnifier — fine webs and red/brown specks.",
            hintRu = "Посмотрите на нижнюю сторону листа с лупой — тонкая паутина и красно-коричневые точки.",
        ),
        "aphids" to Pest(
            key = "aphids",
            nameEn = "Aphids",
            nameRu = "Тля",
            summaryEn = "Soft-bodied insects (1-3 mm) that cluster on young shoots and flower buds.",
            summaryRu = "Мягкотелые насекомые (1-3 мм), скапливаются на молодых побегах и бутонах.",
            hintEn = "Check soft new growth and the underside of new leaves for green/black clusters.",
            hintRu = "Осмотрите молодые побеги и обратную сторону свежих листьев — зелёные или чёрные скопления.",
        ),
        "mealybug" to Pest(
            key = "mealybug",
            nameEn = "Mealybugs",
            nameRu = "Мучнистый червец",
            summaryEn = "Soft-bodied insects covered in white cottony wax; hide in leaf axils and along stems.",
            summaryRu = "Мягкотелые насекомые в белом ватообразном воске; прячутся в пазухах листьев и на стеблях.",
            hintEn = "Look for white cottony tufts in leaf axils and where stems join.",
            hintRu = "Ищите белые ватные комочки в пазухах листьев и в местах соединения стеблей.",
        ),
        "scale_insect" to Pest(
            key = "scale_insect",
            nameEn = "Scale Insects",
            nameRu = "Щитовка",
            summaryEn = "Brown or yellowish hard or soft 'plaques' (2-5 mm) glued to stems and leaf veins.",
            summaryRu = "Коричневые или желтоватые «бляшки» (2-5 мм), приклеенные к стеблям и жилкам листьев.",
            hintEn = "Try scraping a brown 'spot' on a stem with your nail — if it lifts off, it's scale.",
            hintRu = "Попробуйте сковырнуть «пятно» на стебле ногтем — если отделяется, это щитовка.",
        ),
        "whitefly" to Pest(
            key = "whitefly",
            nameEn = "Whitefly",
            nameRu = "Белокрылка",
            summaryEn = "Small (~1.5 mm) white flying insects on leaf undersides; a cloud takes off when shaken.",
            summaryRu = "Мелкие (~1.5 мм) белые летающие насекомые на обратной стороне листьев; при встряхивании взлетают облачком.",
            hintEn = "Gently shake the plant — a cloud of tiny white moths is whitefly.",
            hintRu = "Слегка встряхните растение — если поднимается облачко белых «мошек», это белокрылка.",
        ),
    )

    /** disease class key → list of pest keys whose damage can be confused with it. */
    private val MAPPING: Map<String, List<String>> = mapOf(
        "anthracnose"         to listOf("spider_mites"),
        "bacterial_leaf_spot" to listOf("aphids"),
        "gray_mold"           to listOf("mealybug"),
        "iron_chlorosis"      to listOf("spider_mites", "aphids", "scale_insect", "mealybug", "whitefly"),
        "mosaic_virus"        to listOf("aphids"),
        "powdery_mildew"      to listOf("mealybug"),
        // Empty (no pest can be confused with these visually):
        "healthy"             to emptyList(),
        "late_blight"         to emptyList(),
        "not_a_plant"         to emptyList(),
        "sunburn"             to emptyList(),
    )

    /** Resolve a disease class to the bilingual pest cards. */
    fun forDisease(diseaseClass: String): List<Pest> {
        val keys = MAPPING[diseaseClass] ?: return emptyList()
        return keys.mapNotNull { PESTS[it] }
    }
}
