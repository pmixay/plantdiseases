package com.plantdiseases.app.util

import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.data.model.GuideItem
import java.util.Locale

/**
 * Bridges model/community disease *class keys* (e.g. ``gray_mold_botrytis``)
 * to the bundled bilingual [GuideItem] documentation, whose ids line up with
 * the class keys for all but a few legacy aliases. Used by the Home
 * carousels: "Popular diseases" maps trending class keys here to find the
 * icon + localized name + full doc, and "Disease guide" lists every disease
 * doc. Keeping the alias table here (not scattered) means a new server class
 * only needs one entry to light up across the carousels and detail screen.
 */
object DiseaseCatalog {

    // class key → guide id, only where they differ. Everything else maps
    // to an identically-named guide item (or null if there's no doc yet).
    private val aliases = mapOf(
        "gray_mold_botrytis" to "botrytis",
        "leaf_spot_fungal" to "leaf_spot",
        "bacterial_leaf_spot" to "leaf_spot",
        "mealybug" to "mealybugs",
        "scale" to "scale_insects",
    )

    /** The guide doc for a disease class key, or null when none is bundled. */
    fun guideItemForClass(key: String): GuideItem? {
        val id = aliases[key] ?: key
        return GuideDataProvider.getGuideItems().firstOrNull { it.id == id }
    }

    /**
     * The guide doc whose localized title matches a free-text disease *name*
     * (either language, case-insensitive). Used when the server gives us a
     * display name but no class key — so popular-species "common problems"
     * rows still pick up the same icon as the disease guide instead of the
     * generic placeholder.
     */
    fun guideItemForName(context: Context, name: String): GuideItem? {
        val needle = name.trim().lowercase()
        if (needle.isEmpty()) return null
        val languages = listOf(Locale.ENGLISH, Locale("ru")).map { resourcesIn(context, it) }
        return GuideDataProvider.getGuideItems().firstOrNull { item ->
            languages.any { it.getString(item.title).trim().lowercase() == needle }
        }
    }

    private fun resourcesIn(context: Context, locale: Locale): Resources {
        val config = Configuration(context.resources.configuration).apply { setLocale(locale) }
        return context.createConfigurationContext(config).resources
    }

    /** Every bundled disease/pest doc, in catalog order — the "Disease
     *  guide" documentation carousel. */
    fun diseaseGuideItems(): List<GuideItem> =
        GuideDataProvider.getGuideItems().filter {
            it.category == GuideCategory.COMMON_DISEASES || it.category == GuideCategory.PESTS
        }
}
