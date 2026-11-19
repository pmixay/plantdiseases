package com.plantdiseases.app.ui.common

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.SpeciesInfoResponse

/**
 * The attribution behind the "i" button wherever the app shows a Wikipedia
 * extract: [SpeciesInfoCardView] and the species screen.
 *
 * Wikipedia text is CC BY-SA 4.0, which requires naming the source and the
 * licence next to the reused text. Photos come from Wikimedia Commons under
 * per-file licences. When the server machine-translated the extract because
 * no article exists in the reader's language (`SpeciesInfoResponse.translated`),
 * the dialog says so.
 */
object WikipediaAttribution {

    const val LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
    private const val TAG = "WikipediaAttribution"

    /** The dialog body: source (with the article title when known), the
     *  licence, the photo note and, if flagged, the machine-translation note. */
    fun message(
        context: Context,
        articleTitle: String?,
        machineTranslated: Boolean,
        photoCredit: String? = null,
        photoLicense: String? = null,
    ): String {
        val title = articleTitle?.trim()?.takeIf { it.isNotEmpty() }
        return buildString {
            append(
                if (title != null) {
                    context.getString(R.string.wikipedia_attribution_body_article, title)
                } else {
                    context.getString(R.string.wikipedia_attribution_body)
                },
            )
            append("\n\n")
            append(context.getString(R.string.wikipedia_attribution_photos))
            photoCredit?.trim()?.takeIf { it.isNotEmpty() }?.let { credit ->
                append("\n\n")
                val licence = photoLicense?.trim()?.takeIf { it.isNotEmpty() }
                append(
                    if (licence != null) {
                        context.getString(R.string.wikipedia_attribution_photo_credit_licence, credit, licence)
                    } else {
                        context.getString(R.string.wikipedia_attribution_photo_credit, credit)
                    },
                )
            }
            if (machineTranslated) {
                append("\n\n")
                append(context.getString(R.string.wikipedia_attribution_translated))
            }
        }
    }

    /** The text to show for [info]: its extract, introduced by a one-line note
     *  when it describes the genus because no article exists for the species. */
    fun displayExtract(context: Context, info: SpeciesInfoResponse): String? {
        val extract = info.extract?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        if (info.level != "genus") return extract
        val genus = info.title?.trim()?.takeIf { it.isNotEmpty() }
        val note = if (genus != null) {
            context.getString(R.string.species_info_genus_note, genus)
        } else {
            context.getString(R.string.species_info_genus_note_plain)
        }
        return note + "\n\n" + extract
    }

    fun show(context: Context, info: SpeciesInfoResponse) {
        show(
            context,
            info.title,
            info.pageUrl,
            info.translated,
            info.imageCredit,
            info.imageLicense,
        )
    }

    fun show(
        context: Context,
        articleTitle: String?,
        pageUrl: String?,
        machineTranslated: Boolean,
        photoCredit: String? = null,
        photoLicense: String? = null,
    ) {
        val builder = MaterialAlertDialogBuilder(context)
            .setTitle(R.string.wikipedia_attribution_title)
            .setMessage(
                message(context, articleTitle, machineTranslated, photoCredit, photoLicense),
            )
            .setNegativeButton(R.string.wikipedia_attribution_close, null)
            .setNeutralButton(R.string.wikipedia_attribution_licence) { _, _ ->
                open(context, LICENCE_URL)
            }
        pageUrl?.trim()?.takeIf { it.startsWith("https://") }?.let { url ->
            builder.setPositiveButton(R.string.identify_wikipedia) { _, _ -> open(context, url) }
        }
        builder.show()
    }

    private fun open(context: Context, url: String) {
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "No app can open $url", e)
        }
    }
}
