package com.plantdiseases.app.data

import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.data.model.GuideItem
import com.plantdiseases.app.util.ServerConfig

object GuideDataProvider {

    private val byCategory: Map<GuideCategory, List<GuideItem>> by lazy { items.groupBy { it.category } }

    fun getGuideItems(): List<GuideItem> = items

    // The articles' text lives in res/values*/strings_guide.xml.
    private val items: List<GuideItem> = listOf(
        // Common diseases — plant-care guide articles

        GuideItem(
            id = "powdery_mildew",
            title = R.string.guide_powdery_mildew_title,
            description = R.string.guide_powdery_mildew_description,
            content = R.string.guide_powdery_mildew_content,
            iconRes = R.drawable.ic_disease_powdery_mildew,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "leaf_spot",
            title = R.string.guide_leaf_spot_title,
            description = R.string.guide_leaf_spot_description,
            content = R.string.guide_leaf_spot_content,
            iconRes = R.drawable.ic_disease_leaf_spot,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "blight",
            title = R.string.guide_blight_title,
            description = R.string.guide_blight_description,
            content = R.string.guide_blight_content,
            iconRes = R.drawable.ic_disease_blight,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "root_rot",
            title = R.string.guide_root_rot_title,
            description = R.string.guide_root_rot_description,
            content = R.string.guide_root_rot_content,
            iconRes = R.drawable.ic_disease_root_rot,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "leaf_mold",
            title = R.string.guide_leaf_mold_title,
            description = R.string.guide_leaf_mold_description,
            content = R.string.guide_leaf_mold_content,
            iconRes = R.drawable.ic_disease_leaf_mold,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "viral_disease",
            title = R.string.guide_viral_disease_title,
            description = R.string.guide_viral_disease_description,
            content = R.string.guide_viral_disease_content,
            iconRes = R.drawable.ic_disease_viral,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "rust",
            title = R.string.guide_rust_title,
            description = R.string.guide_rust_description,
            content = R.string.guide_rust_content,
            iconRes = R.drawable.ic_disease_rust,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "botrytis",
            title = R.string.guide_botrytis_title,
            description = R.string.guide_botrytis_description,
            content = R.string.guide_botrytis_content,
            iconRes = R.drawable.ic_disease_botrytis,
            category = GuideCategory.COMMON_DISEASES,
        ),

        // Pests

        GuideItem(
            id = "spider_mites",
            title = R.string.guide_spider_mites_title,
            description = R.string.guide_spider_mites_description,
            content = R.string.guide_spider_mites_content,
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS,
        ),

        GuideItem(
            id = "aphids",
            title = R.string.guide_aphids_title,
            description = R.string.guide_aphids_description,
            content = R.string.guide_aphids_content,
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS,
        ),

        GuideItem(
            id = "mealybugs",
            title = R.string.guide_mealybugs_title,
            description = R.string.guide_mealybugs_description,
            content = R.string.guide_mealybugs_content,
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS,
        ),

        GuideItem(
            id = "scale_insects",
            title = R.string.guide_scale_insects_title,
            description = R.string.guide_scale_insects_description,
            content = R.string.guide_scale_insects_content,
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS,
        ),

        // Watering

        GuideItem(
            id = "watering_basics",
            title = R.string.guide_watering_basics_title,
            description = R.string.guide_watering_basics_description,
            content = R.string.guide_watering_basics_content,
            iconRes = R.drawable.ic_watering,
            category = GuideCategory.WATERING,
        ),

        GuideItem(
            id = "humidity_guide",
            title = R.string.guide_humidity_guide_title,
            description = R.string.guide_humidity_guide_description,
            content = R.string.guide_humidity_guide_content,
            iconRes = R.drawable.ic_watering,
            category = GuideCategory.WATERING,
        ),

        // Lighting

        GuideItem(
            id = "light_guide",
            title = R.string.guide_light_guide_title,
            description = R.string.guide_light_guide_description,
            content = R.string.guide_light_guide_content,
            iconRes = R.drawable.ic_light,
            category = GuideCategory.CARE_TIPS,
        ),

        // Care tips

        GuideItem(
            id = "repotting",
            title = R.string.guide_repotting_title,
            description = R.string.guide_repotting_description,
            content = R.string.guide_repotting_content,
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS,
        ),

        GuideItem(
            id = "fertilizing",
            title = R.string.guide_fertilizing_title,
            description = R.string.guide_fertilizing_description,
            content = R.string.guide_fertilizing_content,
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS,
        ),

        GuideItem(
            id = "soil_guide",
            title = R.string.guide_soil_guide_title,
            description = R.string.guide_soil_guide_description,
            content = R.string.guide_soil_guide_content,
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS,
        ),

        GuideItem(
            id = "seasonal_care",
            title = R.string.guide_seasonal_care_title,
            description = R.string.guide_seasonal_care_description,
            content = R.string.guide_seasonal_care_content,
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS,
        ),

        // --- Articles for the remaining diagnosis classes, so every scan
        // verdict can deep-link to a real guide (see guideIdForDisease). ---

        GuideItem(
            id = "healthy",
            title = R.string.guide_healthy_title,
            description = R.string.guide_healthy_description,
            content = R.string.guide_healthy_content,
            iconRes = R.drawable.ic_healthy,
            category = GuideCategory.CARE_TIPS,
        ),

        GuideItem(
            id = "leaf_scorch_underwatering",
            title = R.string.guide_leaf_scorch_underwatering_title,
            description = R.string.guide_leaf_scorch_underwatering_description,
            content = R.string.guide_leaf_scorch_underwatering_content,
            iconRes = R.drawable.ic_watering,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "nutrient_deficiency",
            title = R.string.guide_nutrient_deficiency_title,
            description = R.string.guide_nutrient_deficiency_description,
            content = R.string.guide_nutrient_deficiency_content,
            iconRes = R.drawable.ic_care,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "edema",
            title = R.string.guide_edema_title,
            description = R.string.guide_edema_description,
            content = R.string.guide_edema_content,
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "sunburn_or_scorch",
            title = R.string.guide_sunburn_or_scorch_title,
            description = R.string.guide_sunburn_or_scorch_description,
            content = R.string.guide_sunburn_or_scorch_content,
            iconRes = R.drawable.ic_light,
            category = GuideCategory.COMMON_DISEASES,
        ),

        GuideItem(
            id = "pests",
            title = R.string.guide_pests_title,
            description = R.string.guide_pests_description,
            content = R.string.guide_pests_content,
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS,
        ),

        // ── App help — how to read what the scanner tells you. Moved here
        // from the Profile tips cards so all reference content lives in one
        // handbook. ─────────────────────────────────────────────────────
        GuideItem(
            id = "understanding_results",
            title = R.string.guide_understanding_results_title,
            description = R.string.guide_understanding_results_description,
            content = R.string.guide_understanding_results_content,
            iconRes = R.drawable.ic_scan,
            category = GuideCategory.HELP,
        )
    )

    fun getByCategory(category: GuideCategory): List<GuideItem> =
        byCategory[category].orEmpty()

    /**
     * Map a classifier class key to a guide article id, or null when there's
     * no article (e.g. not_a_plant). The guide ids were authored independently
     * of the model taxonomy, so a few need translating (gray_mold_botrytis →
     * botrytis, mealybug → mealybugs, scale → scale_insects).
     */
    fun guideIdForDisease(classKey: String): String? = when (classKey.trim().lowercase()) {
        "powdery_mildew" -> "powdery_mildew"
        "leaf_spot" -> "leaf_spot"
        "viral_disease" -> "viral_disease"
        "gray_mold_botrytis" -> "botrytis"
        "mealybug" -> "mealybugs"
        "scale" -> "scale_insects"
        "pests" -> "pests"
        "healthy" -> "healthy"
        "leaf_scorch_underwatering" -> "leaf_scorch_underwatering"
        "nutrient_deficiency" -> "nutrient_deficiency"
        "edema" -> "edema"
        "sunburn_or_scorch" -> "sunburn_or_scorch"
        else -> null
    }

    /**
     * Guide IDs that have a server-hosted example photo. These photos moved out
     * of the APK (server/static/guide/<key>.png, served by /api/guide/photo/)
     * to shrink the app; the client downloads + disk-caches them so they stay
     * available offline after first view. Pure care topics ship none. Keep this
     * set in sync with server/static/guide/.
     */
    private val EXAMPLE_PHOTO_KEYS = setOf(
        "powdery_mildew", "leaf_spot", "blight", "root_rot", "leaf_mold",
        "viral_disease", "rust", "botrytis", "spider_mites", "aphids",
        "mealybugs", "scale_insects", "leaf_scorch_underwatering",
        "nutrient_deficiency", "edema", "sunburn_or_scorch", "pests", "healthy",
    )

    /** The example-photo key for a guide article, or null where none applies.
     *  The key doubles as the server path segment (/api/guide/photo/<key>). */
    fun exampleImageKey(guideId: String): String? =
        guideId.takeIf { it in EXAMPLE_PHOTO_KEYS }

    /** The example-photo key for a classifier class, or null. */
    fun exampleImageKeyForDisease(classKey: String): String? =
        guideIdForDisease(classKey)?.let { exampleImageKey(it) }

    /** Absolute URL of the server-hosted example photo for a guide id, or null
     *  where no photo applies. Loaded via Glide (disk-cached for offline). */
    fun exampleImageUrl(guideId: String): String? =
        exampleImageKey(guideId)?.let { "${ServerConfig.appBaseUrl}api/guide/photo/$it" }

    /** Absolute URL of the server-hosted example photo for a classifier class. */
    fun exampleImageUrlForDisease(classKey: String): String? =
        exampleImageKeyForDisease(classKey)?.let { "${ServerConfig.appBaseUrl}api/guide/photo/$it" }

    /** Every server-hosted guide-photo URL — used to prefetch them into Glide's
     *  disk cache while online so the guide/result photos render offline. */
    fun allExamplePhotoUrls(): List<String> =
        EXAMPLE_PHOTO_KEYS.map { "${ServerConfig.appBaseUrl}api/guide/photo/$it" }
}
