package com.plantdiseases.app.ui.legal

/** What a [PolicyBlock] is, which decides how the screen styles it. */
enum class PolicyBlockKind { TITLE, UPDATED, HEADING, PARAGRAPH, BULLET }

/**
 * One element of the policy with its text in both languages. [ru] and [en]
 * hold inline HTML (`<b>`, `<a href>`), ready for `HtmlCompat.fromHtml`.
 */
data class PolicyBlock(val kind: PolicyBlockKind, val ru: String, val en: String) {
    fun text(russian: Boolean): String = if (russian) ru else en
}

/**
 * Reads `assets/privacy_policy.html` into [PolicyBlock]s so the asset stays the
 * single source of the policy text.
 *
 * This is a purpose-built scanner, not a general HTML parser. The asset keeps
 * its translations in `data-ru` / `data-en` attributes whose values contain raw
 * `<b>` markup, which a stock XML parser rejects and a naive regex mis-splits.
 * The scanner therefore reads each opening tag quote-aware, then jumps to the
 * matching close tag.
 *
 * Limits: only `h1`, `h2`, `p` and `li` inside `<main>` are recognised (a `ul`
 * is flattened into bullets, no nesting); those elements may not contain each
 * other; a `p` with `class="updated"` becomes [PolicyBlockKind.UPDATED]; an
 * element without `data-ru`/`data-en` uses its inner HTML for both languages;
 * the common attribute entities are decoded, others are left as written.
 */
object PrivacyPolicyParser {

    private val TAGS = mapOf(
        "h1" to PolicyBlockKind.TITLE,
        "h2" to PolicyBlockKind.HEADING,
        "p" to PolicyBlockKind.PARAGRAPH,
        "li" to PolicyBlockKind.BULLET,
    )

    fun parse(html: String): List<PolicyBlock> {
        val start = html.indexOf("<main").takeIf { it >= 0 } ?: 0
        val end = html.indexOf("</main>", start).takeIf { it >= 0 } ?: html.length
        val body = html.substring(start, end)
        val blocks = mutableListOf<PolicyBlock>()
        var pos = 0
        while (pos < body.length) {
            val open = body.indexOf('<', pos)
            if (open < 0) break
            val name = tagNameAt(body, open)
            val kind = name?.let { TAGS[it] }
            if (name == null || kind == null) {
                pos = open + 1
                continue
            }
            val tag = readOpenTag(body, open + 1 + name.length)
            val close = body.indexOf("</$name>", tag.contentStart)
            val innerEnd = if (close < 0) body.length else close
            val inner = body.substring(tag.contentStart, innerEnd).trim()
            val ru = tag.attrs["data-ru"] ?: inner
            val en = tag.attrs["data-en"] ?: inner
            if (ru.isNotBlank() || en.isNotBlank()) {
                val isUpdated = kind == PolicyBlockKind.PARAGRAPH &&
                    "updated" in (tag.attrs["class"] ?: "").split(' ')
                blocks += PolicyBlock(if (isUpdated) PolicyBlockKind.UPDATED else kind, ru, en)
            }
            pos = if (close < 0) body.length else close + name.length + 3
        }
        return blocks
    }

    private fun tagNameAt(s: String, lt: Int): String? {
        var i = lt + 1
        while (i < s.length && s[i].isLetterOrDigit()) i++
        val name = s.substring(lt + 1, i).lowercase()
        val boundary = i >= s.length || s[i] == '>' || s[i] == '/' || s[i].isWhitespace()
        return name.takeIf { it.isNotEmpty() && boundary }
    }

    private class OpenTag(val attrs: Map<String, String>, val contentStart: Int)

    /** Reads attributes from [from] up to the closing `>`, honouring quotes. */
    private fun readOpenTag(s: String, from: Int): OpenTag {
        val attrs = mutableMapOf<String, String>()
        var i = from
        while (i < s.length && s[i] != '>') {
            if (s[i].isWhitespace() || s[i] == '/') {
                i++
                continue
            }
            val nameStart = i
            while (i < s.length && s[i] != '=' && s[i] != '>' && s[i] != '/' && !s[i].isWhitespace()) i++
            val attr = s.substring(nameStart, i).lowercase()
            if (i < s.length && s[i] == '=') {
                i++
                val quote = s.getOrNull(i)
                val value: String
                if (quote == '"' || quote == '\'') {
                    val close = s.indexOf(quote, i + 1).takeIf { it >= 0 } ?: s.length
                    value = s.substring(i + 1, close)
                    i = minOf(close + 1, s.length)
                } else {
                    val valueStart = i
                    while (i < s.length && s[i] != '>' && !s[i].isWhitespace()) i++
                    value = s.substring(valueStart, i)
                }
                attrs[attr] = decodeEntities(value)
            } else if (attr.isNotEmpty()) {
                attrs[attr] = ""
            }
        }
        return OpenTag(attrs, minOf(i + 1, s.length))
    }

    private fun decodeEntities(value: String): String = value
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
        .replace("&amp;", "&")
}
