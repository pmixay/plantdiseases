package com.plantdiseases.app.design

import com.google.common.truth.Truth.assertWithMessage
import org.junit.Test
import java.io.File

/**
 * Every UI string exists in English and Russian, with the same format
 * placeholders. A string missing from values-ru silently shows English to a
 * Russian reader; a placeholder missing from one language crashes or prints
 * the wrong value at runtime. Strings marked translatable="false" need no
 * Russian copy.
 */
class StringParityTest {

    private val res = File("src/main/res")

    @Test
    fun everyTranslatableStringHasARussianCopy() {
        val en = strings("values")
        val ru = strings("values-ru")
        val missing = en.filterValues { it.translatable }.keys - ru.keys
        val orphaned = ru.keys - en.keys
        assertWithMessage("English strings with no Russian copy").that(missing).isEmpty()
        assertWithMessage("Russian strings with no English original").that(orphaned).isEmpty()
    }

    @Test
    fun bothLanguagesUseTheSamePlaceholders() {
        val ru = strings("values-ru")
        val mismatched = strings("values").mapNotNull { (name, en) ->
            val other = ru[name] ?: return@mapNotNull null
            if (!en.formatted || placeholders(en.text) == placeholders(other.text)) {
                null
            } else {
                "$name: ${placeholders(en.text)} vs ${placeholders(other.text)}"
            }
        }
        assertWithMessage("Strings whose placeholders differ between EN and RU").that(mismatched).isEmpty()
    }

    private class Entry(val text: String, val translatable: Boolean, val formatted: Boolean)

    private fun strings(dir: String): Map<String, Entry> =
        File(res, dir).listFiles { f -> f.name.startsWith("strings") && f.extension == "xml" }!!
            .flatMap { file ->
                STRING.findAll(file.readText().replace(COMMENT, "")).map { m ->
                    val attrs = m.groupValues[2]
                    m.groupValues[1] to Entry(
                        text = m.groupValues[3],
                        translatable = "translatable=\"false\"" !in attrs,
                        formatted = "formatted=\"false\"" !in attrs,
                    )
                }.toList()
            }
            .toMap()

    private fun placeholders(text: String): List<String> =
        PLACEHOLDER.findAll(text.replace("%%", "")).map { it.value }.sorted().toList()

    private companion object {
        val COMMENT = Regex("<!--.*?-->", RegexOption.DOT_MATCHES_ALL)
        val STRING = Regex("""<string\s+name="([^"]+)"([^>]*)>(.*?)</string>""", RegexOption.DOT_MATCHES_ALL)
        val PLACEHOLDER = Regex("""%(\d+\$)?[-#+ 0,(]*\d*(\.\d+)?[sdfxXc]""")
    }
}
