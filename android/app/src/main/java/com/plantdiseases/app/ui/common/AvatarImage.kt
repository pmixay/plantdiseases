package com.plantdiseases.app.ui.common

import android.content.res.ColorStateList
import androidx.appcompat.content.res.AppCompatResources
import androidx.core.content.ContextCompat
import com.bumptech.glide.RequestManager
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.google.android.material.imageview.ShapeableImageView
import com.plantdiseases.app.R

/**
 * The two display states of a round profile avatar (Profile hero, Account
 * screen): the tinted placeholder icon and the user's photo.
 *
 * The circle comes from the [ShapeableImageView]'s shape mask, and that mask
 * only stays a full circle while the view's padding never changes:
 * - the mask is built from the view padding in `onSizeChanged` only, so a
 *   padding change after layout leaves a stale (smaller) circle; and
 * - ShapeableImageView folds its XML content padding into the view padding on
 *   its first measure, so `setContentPadding` before that measure subtracts a
 *   padding that was never added. After a language switch the activity is
 *   recreated, the stored photo renders synchronously before the first
 *   measure, and clearing a 36dp content padding there left the view at
 *   -36dp padding: the "circle" grew past the view's corners and the photo
 *   drew as a square.
 *
 * Both states therefore leave padding alone (zero in the layouts) and keep the
 * placeholder's inset inside its drawable, `ic_avatar_placeholder`.
 */
object AvatarImage {

    fun showPlaceholder(avatar: ShapeableImageView) {
        avatar.imageTintList = ColorStateList.valueOf(
            ContextCompat.getColor(avatar.context, R.color.on_hero),
        )
        avatar.setImageResource(R.drawable.ic_avatar_placeholder)
    }

    /** Untints the view for a photo; padding is deliberately left untouched. */
    fun prepareForPhoto(avatar: ShapeableImageView) {
        avatar.imageTintList = null
    }

    /**
     * Loads a local avatar file or provider URL into [avatar]. The view's shape
     * mask makes it round; `circleCrop` only keeps the bitmap's own corners
     * transparent as well.
     */
    fun loadPhoto(glide: RequestManager, avatar: ShapeableImageView, source: Any) {
        prepareForPhoto(avatar)
        // The view's tint is off for photos, so the error fallback carries its own.
        val fallback = AppCompatResources.getDrawable(avatar.context, R.drawable.ic_avatar_placeholder)
            ?.mutate()
            ?.apply { setTint(ContextCompat.getColor(avatar.context, R.color.on_hero)) }
        glide.load(source)
            .diskCacheStrategy(DiskCacheStrategy.NONE)
            .skipMemoryCache(true)
            .circleCrop()
            .error(fallback)
            .into(avatar)
    }
}
