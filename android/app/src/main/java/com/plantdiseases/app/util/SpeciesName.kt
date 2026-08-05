package com.plantdiseases.app.util

/**
 * Pick the species name to SHOW for the current UI locale.
 *
 * The rule mirrors the result-screen species card so every card stays
 * consistent: prefer the common name, fall back to the scientific (Latin)
 * binomial. The one locale guard: a Russian user must never be shown an
 * English-only common name, so when the common name carries no Cyrillic we
 * drop to the locale-neutral Latin binomial instead.
 *
 * This is the synchronous baseline used by the plant-detail hero, the garden
 * card and the care-plan card. The species card additionally upgrades to the
 * localized Wikipedia title because it already fetches that enrichment; this
 * helper deliberately makes no network call so it is safe in list rows.
 *
 * Returns null only when both inputs are blank.
 */
fun localizedSpeciesName(scientific: String?, common: String?, isRu: Boolean): String? {
    val sci = scientific?.trim()?.takeIf { it.isNotEmpty() }
    val com = common?.trim()?.takeIf { it.isNotEmpty() }
    return if (isRu && com != null && !hasCyrillic(com)) sci ?: com else com ?: sci
}

/** True when the string contains any Cyrillic character (U+0400–U+04FF). */
internal fun hasCyrillic(s: String): Boolean = s.any { it in 'Ѐ'..'ӿ' }
