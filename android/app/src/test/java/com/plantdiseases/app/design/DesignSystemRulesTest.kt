package com.plantdiseases.app.design

import com.google.common.truth.Truth.assertWithMessage
import org.junit.Test
import java.io.File

/**
 * Typography rules of the design system, checked on the resource sources:
 *
 * - UI text has no middle dot and no em/en dash; a plain "-" (or a comma)
 *   takes their place.
 * - Text never uses the muted ink (`on_surface_secondary`, which also backs
 *   `colorOnSurfaceVariant`): small text uses `on_surface`, and the muted
 *   ink is for icon tints only.
 * - The caption size, the smallest text step, stays at 12sp or more.
 */
class DesignSystemRulesTest {

    private val res = File("src/main/res")

    @Test
    fun stringResourcesUsePlainPunctuation() {
        val offenders = res.listFiles { f -> f.isDirectory && f.name.startsWith("values") }!!
            .flatMap { dir -> dir.listFiles { f -> f.name.startsWith("strings") || f.name.startsWith("arrays") }!!.toList() }
            .flatMap { file ->
                VALUE.findAll(stripComments(file.readText()))
                    .filter { m -> FORBIDDEN.any { it in m.groupValues[2] } }
                    .map { m -> "${file.parentFile.name}/${file.name}: ${m.groupValues[2].take(80)}" }
                    .toList()
            }
        assertWithMessage("Use '-' or ',' instead of a middle dot or long dash").that(offenders).isEmpty()
    }

    @Test
    fun layoutTextUsesPlainPunctuationAndFullInk() {
        val punctuation = mutableListOf<String>()
        val muted = mutableListOf<String>()
        layoutFiles().forEach { file ->
            TAG.findAll(stripComments(file.readText())).forEach { tag ->
                val name = tag.groupValues[1]
                val attrs = tag.groupValues[2]
                ATTR.findAll(attrs).forEach { a ->
                    if (FORBIDDEN.any { it in a.groupValues[2] }) punctuation += "${file.name}: ${a.value.take(80)}"
                }
                val color = TEXT_COLOR.find(attrs)?.groupValues?.get(1)
                if (TEXT_VIEW.containsMatchIn(name) && color in MUTED) muted += "${file.name}: $name $color"
            }
        }
        assertWithMessage("Use '-' or ',' instead of a middle dot or long dash").that(punctuation).isEmpty()
        assertWithMessage("Text uses @color/on_surface; the muted ink is for icons").that(muted).isEmpty()
    }

    @Test
    fun stylesNeverColourTextWithTheMutedInk() {
        val offenders = File(res, "values").listFiles { f -> f.extension == "xml" }!!.flatMap { file ->
            STYLE_TEXT_COLOR.findAll(file.readText())
                .filter { it.groupValues[1] in MUTED }
                .map { "${file.name}: ${it.value}" }
                .toList()
        }
        assertWithMessage("Text styles use @color/on_surface").that(offenders).isEmpty()
    }

    @Test
    fun captionIsAtLeast12sp() {
        val dimens = File(res, "values/dimens.xml").readText()
        val caption = Regex("""<dimen name="text_caption">(\d+)sp</dimen>""").find(dimens)!!.groupValues[1].toInt()
        assertWithMessage("text_caption").that(caption).isAtLeast(12)
    }

    private fun layoutFiles(): List<File> =
        listOf("layout", "menu", "navigation").flatMap { dir ->
            File(res, dir).listFiles { f -> f.extension == "xml" }?.toList().orEmpty()
        }

    private fun stripComments(xml: String) = COMMENT.replace(xml, "")

    private companion object {
        val FORBIDDEN = listOf("\u00b7", "\u2014", "\u2013")
        val MUTED = setOf(
            "@color/on_surface_secondary",
            "?attr/colorOnSurfaceVariant",
            "?colorOnSurfaceVariant",
            "?android:attr/textColorSecondary",
            "?android:textColorSecondary",
        )
        val COMMENT = Regex("<!--.*?-->", RegexOption.DOT_MATCHES_ALL)
        val VALUE = Regex("""<(string|item)\b[^>]*>(.*?)</\1>""", RegexOption.DOT_MATCHES_ALL)
        val TAG = Regex("""<([\w.]+)((?:\s+[\w:.]+\s*=\s*"[^"]*")*)\s*/?>""")
        val ATTR = Regex("""([\w:.]+)\s*=\s*"([^"]*)"""")
        val TEXT_COLOR = Regex("""android:textColor\s*=\s*"([^"]*)"""")
        val TEXT_VIEW = Regex("(TextView|Chip|Button|EditText|CheckBox|RadioButton)$")
        val STYLE_TEXT_COLOR = Regex("""<item name="android:textColor">([^<]*)</item>""")
    }
}
