package com.plantdiseases.app.ui.community

import android.content.Context
import android.view.View
import com.plantdiseases.app.data.model.CommunityScanItem
import com.plantdiseases.app.ui.garden.PhotoViewerActivity
import com.plantdiseases.app.util.ServerConfig

/** The shared scan's photo on the server, or null when it was stored without one. */
fun CommunityScanItem.photoUrl(): String? =
    thumbPath?.takeIf { it.isNotBlank() }
        // ServerConfig.appBaseUrl ends with `/`; relative path appended.
        ?.let { "${ServerConfig.appBaseUrl}api/community/thumb/$it" }

/**
 * A shared scan has no detail screen: a tap expands its photo full screen,
 * with a cross to close it. [from] is the tapped thumbnail the viewer grows
 * out of. Returns false (nothing opens) for a scan stored without a photo.
 */
fun openScanPhoto(context: Context, item: CommunityScanItem, from: View? = null): Boolean {
    val url = item.photoUrl() ?: return false
    PhotoViewerActivity.open(context, listOf(url), from = from)
    return true
}
