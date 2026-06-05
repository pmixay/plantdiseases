package com.plantdiseases.app.util

import android.graphics.drawable.Drawable
import android.widget.ImageView
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DataSource
import com.bumptech.glide.load.engine.GlideException
import com.bumptech.glide.request.RequestListener
import com.bumptech.glide.request.target.Target
import com.facebook.shimmer.ShimmerFrameLayout
import com.plantdiseases.app.R

/**
 * Loads a community thumbnail into [image] while [shimmer] runs a skeleton
 * placeholder, stopping the shimmer once the image resolves (or fails).
 *
 * Replaces the old per-cell behaviour of flashing the red ``ic_disease``
 * triangle as the Glide placeholder — which read as a wall of error icons
 * for the second or two a feed took to fetch its thumbnails. A neutral
 * shimmer is shown instead, and the neutral leaf placeholder is the only
 * fallback when there's no thumbnail or the load fails.
 */
object ThumbLoader {

    fun load(shimmer: ShimmerFrameLayout, image: ImageView, url: String?) {
        if (url.isNullOrBlank()) {
            shimmer.hideShimmer()
            Glide.with(image).clear(image)
            image.setImageResource(R.drawable.ic_plant_placeholder)
            return
        }
        shimmer.showShimmer(true)
        Glide.with(image)
            .load(url)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .centerCrop()
            .listener(object : RequestListener<Drawable> {
                override fun onLoadFailed(
                    e: GlideException?,
                    model: Any?,
                    target: Target<Drawable>,
                    isFirstResource: Boolean,
                ): Boolean {
                    shimmer.hideShimmer()
                    return false
                }

                override fun onResourceReady(
                    resource: Drawable,
                    model: Any,
                    target: Target<Drawable>,
                    dataSource: DataSource,
                    isFirstResource: Boolean,
                ): Boolean {
                    shimmer.hideShimmer()
                    return false
                }
            })
            .into(image)
    }
}
