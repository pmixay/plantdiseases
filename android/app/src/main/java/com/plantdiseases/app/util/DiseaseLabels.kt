package com.plantdiseases.app.util

/**
 * Single source of truth for turning a model class key (e.g.
 * ``powdery_mildew``) into a localised display label. Shared by the
 * result screen, the nearby-scans feed, and the community opinion sheet
 * so the same diagnosis never reads differently across screens.
 *
 * Returns ``null`` for keys we don't recognise; callers fall back to the
 * raw key (or a prettified version) so a new server-side class never
 * renders as a blank.
 */
object DiseaseLabels {
    fun displayName(key: String, isRu: Boolean): String? = when (key) {
        // PlantVision v2.2 — 8 in-head classes + 2 deferred
        // (edema, sunburn_or_scorch) accepted on the feedback path.
        "healthy" -> if (isRu) "Здоровое растение" else "Healthy Plant"
        "powdery_mildew" -> if (isRu) "Мучнистая роса" else "Powdery Mildew"
        "gray_mold_botrytis" -> if (isRu) "Серая гниль (ботритис)" else "Gray Mold (Botrytis)"
        "leaf_spot" -> if (isRu) "Пятнистость листьев" else "Leaf Spot"
        "pests" -> if (isRu) "Сосущие вредители" else "Sap-sucking Pests"
        // legacy keys from pre-merge models / historical scans:
        "leaf_spot_fungal" -> if (isRu) "Грибковая пятнистость" else "Fungal Leaf Spot"
        "bacterial_leaf_spot" -> if (isRu) "Бактериальная пятнистость" else "Bacterial Leaf Spot"
        "mealybugs", "mealybug" -> if (isRu) "Мучнистые червецы" else "Mealybugs"
        "scale", "scale_insects" -> if (isRu) "Щитовки" else "Scale Insects"
        "viral_disease" -> if (isRu) "Вирусное заболевание" else "Viral Disease"
        "edema" -> if (isRu) "Эдема (отёчность)" else "Edema (Oedema)"
        "sunburn_or_scorch" -> if (isRu) "Солнечный ожог" else "Sunburn / Light Scorch"
        "leaf_scorch_underwatering" -> if (isRu) "Краевой ожог (недолив)" else "Leaf Scorch (Under-watering)"
        "nutrient_deficiency" -> if (isRu) "Дефицит питания" else "Nutrient Deficiency"
        "not_a_plant" -> if (isRu) "Не растение" else "Not a Plant"
        else -> null
    }
}
