package com.plantdiseases.app.util

/** Small, UI-independent rules for keeping free-text species edits from
 * silently retaining canonical names from an earlier identification. */
object SpeciesIdentity {
    fun canonicalDisplay(scientificName: String?, commonName: String?): String? =
        commonName.clean() ?: scientificName.clean()

    fun manualEditClearsCanonical(
        editedDisplay: String?,
        scientificName: String?,
        commonName: String?,
    ): Boolean {
        val validCanonicalDisplays = listOfNotNull(commonName.clean(), scientificName.clean())
        if (validCanonicalDisplays.isEmpty()) return false
        val edited = normalize(editedDisplay)
        return validCanonicalDisplays.none { normalize(it).equals(edited, ignoreCase = true) }
    }

    fun displayChanged(initialDisplay: String?, editedDisplay: String?): Boolean =
        normalize(initialDisplay) != normalize(editedDisplay)

    private fun normalize(value: String?): String = value.orEmpty().trim()

    private fun String?.clean(): String? = this?.trim()?.takeIf { it.isNotEmpty() }
}
