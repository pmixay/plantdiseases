package com.plantdiseases.app.ui.common

import android.app.Activity
import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
import android.graphics.drawable.InsetDrawable
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import androidx.appcompat.view.ContextThemeWrapper
import com.google.android.material.imageview.ShapeableImageView
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Guards the round profile avatar. ShapeableImageView's circle mask is built
 * from the view padding when the size changes, and the view folds XML content
 * padding into its padding on the first measure. Clearing the placeholder's
 * content padding before that measure (a stored photo rendered synchronously
 * after a language-switch recreate) left -36dp padding and a square photo.
 * The avatar must therefore keep zero padding in every state and order.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class AvatarImageTest {

    private val activity: Activity = Robolectric.buildActivity(Activity::class.java).setup().get()
    private val context: Context = ContextThemeWrapper(activity, R.style.Theme_PlantDiseases)

    private fun inflate(layout: Int, avatarId: Int): Pair<View, ShapeableImageView> {
        val root = LayoutInflater.from(context).inflate(layout, null)
        return root to root.findViewById(avatarId)
    }

    private fun attachAndLayout(root: View) {
        activity.setContentView(root)
        shadowOf(Looper.getMainLooper()).idle()
    }

    private fun relayout(view: View) {
        view.requestLayout()
        shadowOf(Looper.getMainLooper()).idle()
    }

    private fun photo(): Bitmap = Bitmap.createBitmap(64, 64, Bitmap.Config.ARGB_8888)

    @Test
    fun avatarLayouts_haveNoPaddingAndAFullCircleShape() {
        listOf(
            R.layout.fragment_profile to R.id.iv_profile_avatar,
            R.layout.activity_account to R.id.iv_avatar,
        ).forEach { (layout, id) ->
            val (_, avatar) = inflate(layout, id)
            assertNoPadding(avatar)
            assertCircular(avatar, size = 72.dp().toFloat())
            assertThat(avatar.drawable).isInstanceOf(InsetDrawable::class.java)
        }
    }

    @Test
    fun photoShownBeforeFirstMeasure_keepsAvatarRound() {
        // The language-switch path: the fragment view is recreated, the auth
        // state is already signed in and the stored photo is applied before
        // the view has ever been measured.
        val (root, avatar) = inflate(R.layout.fragment_profile, R.id.iv_profile_avatar)
        AvatarImage.prepareForPhoto(avatar)
        avatar.setImageBitmap(photo())

        attachAndLayout(root)

        assertThat(avatar.width).isEqualTo(72.dp())
        assertThat(avatar.height).isEqualTo(72.dp())
        assertNoPadding(avatar)
        assertCircular(avatar, size = avatar.width.toFloat())
        assertThat(avatar.imageTintList).isNull()
    }

    @Test
    fun placeholderThenPhotoAfterLayout_keepsAvatarRound() {
        // The cold-start path: placeholder laid out first, provider photo later.
        val (root, avatar) = inflate(R.layout.fragment_profile, R.id.iv_profile_avatar)
        AvatarImage.showPlaceholder(avatar)
        attachAndLayout(root)
        assertNoPadding(avatar)

        AvatarImage.prepareForPhoto(avatar)
        avatar.setImageBitmap(photo())
        relayout(avatar)
        assertNoPadding(avatar)

        // ...and back again, e.g. after signing out.
        AvatarImage.showPlaceholder(avatar)
        relayout(avatar)
        assertNoPadding(avatar)
        assertCircular(avatar, size = avatar.width.toFloat())
    }

    @Test
    fun placeholder_isATintedIconInsetByAQuarterOfTheCircle() {
        val (root, avatar) = inflate(R.layout.fragment_profile, R.id.iv_profile_avatar)
        AvatarImage.prepareForPhoto(avatar)
        AvatarImage.showPlaceholder(avatar)
        attachAndLayout(root)

        assertThat(avatar.imageTintList).isNotNull()
        val inset = avatar.drawable as InsetDrawable
        val outer = inset.bounds
        val icon = inset.drawable!!.bounds
        // 25% on each side: the icon fills the middle half, 18dp of 72dp.
        assertThat(icon.width() * 2).isWithin(2).of(outer.width())
        assertThat(icon.left - outer.left).isWithin(1).of(outer.width() / 4)
    }

    private fun assertNoPadding(avatar: ShapeableImageView) {
        assertThat(listOf(avatar.paddingLeft, avatar.paddingTop, avatar.paddingRight, avatar.paddingBottom))
            .containsExactly(0, 0, 0, 0)
        assertThat(
            listOf(
                avatar.contentPaddingLeft,
                avatar.contentPaddingTop,
                avatar.contentPaddingRight,
                avatar.contentPaddingBottom,
            ),
        ).containsExactly(0, 0, 0, 0)
    }

    private fun assertCircular(avatar: ShapeableImageView, size: Float) {
        val bounds = RectF(0f, 0f, size, size)
        val shape = avatar.shapeAppearanceModel
        listOf(
            shape.topLeftCornerSize,
            shape.topRightCornerSize,
            shape.bottomLeftCornerSize,
            shape.bottomRightCornerSize,
        ).forEach { corner ->
            assertThat(corner.getCornerSize(bounds)).isWithin(0.5f).of(size / 2f)
        }
    }

    private fun Int.dp(): Int = (this * context.resources.displayMetrics.density).toInt()
}
