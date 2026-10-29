package com.plantdiseases.app.ui.account

import android.content.Context
import android.view.Gravity
import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import androidx.appcompat.view.ContextThemeWrapper
import androidx.test.core.app.ApplicationProvider
import com.google.android.material.button.MaterialButton
import com.google.android.material.imageview.ShapeableImageView
import com.google.android.material.textfield.TextInputEditText
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AccountLayoutTest {
    private val context: Context
        get() = ContextThemeWrapper(
            ApplicationProvider.getApplicationContext(),
            R.style.Theme_PlantDiseases,
        )

    @Test
    fun guestActionsAreWideAndKeepLaterAsAQuietChoice() {
        val root = LayoutInflater.from(context).inflate(R.layout.activity_account, null)
        val avatar = root.findViewById<ShapeableImageView>(R.id.iv_avatar)

        assertThat(root.findViewById<FrameLayout>(R.id.avatar_picker)).isNotNull()
        assertThat(avatar.layoutParams.width).isEqualTo(144.dp())
        assertThat(avatar.layoutParams.height).isEqualTo(144.dp())
        assertThat(root.findViewById<MaterialButton>(R.id.btn_yandex).layoutParams.width)
            .isEqualTo(ViewGroup.LayoutParams.MATCH_PARENT)
        assertThat(root.findViewById<MaterialButton>(R.id.btn_vk).layoutParams.width)
            .isEqualTo(ViewGroup.LayoutParams.MATCH_PARENT)
        assertThat(root.findViewById<MaterialButton>(R.id.btn_email_sign_in).layoutParams.width)
            .isEqualTo(ViewGroup.LayoutParams.MATCH_PARENT)
        assertThat(root.findViewById<MaterialButton>(R.id.btn_email_create).layoutParams.width)
            .isEqualTo(ViewGroup.LayoutParams.MATCH_PARENT)
        assertThat(root.findViewById<TextInputEditText>(R.id.et_password).isSaveEnabled).isFalse()
        assertThat(root.findViewById<MaterialButton>(R.id.btn_later).text.toString())
            .isEqualTo(context.getString(R.string.account_later))
        assertThat(context.getString(R.string.account_guest_body).lowercase())
            .doesNotContain("session")
        assertThat(context.getString(R.string.account_email_beta_warning).lowercase())
            .contains("recovery")
    }

    @Test
    fun accountActionsUseFamiliarVisualIcons() {
        val root = LayoutInflater.from(context).inflate(R.layout.activity_account, null)

        assertThat(root.findViewById<MaterialButton>(R.id.btn_logout).icon).isNotNull()
        assertThat(root.findViewById<MaterialButton>(R.id.btn_delete_account).icon).isNotNull()
        assertThat(root.findViewById<ViewGroup>(R.id.sessions_container)).isNotNull()
        assertThat(context.getString(R.string.account_delete_warning).lowercase())
            .doesNotContain("session")
    }

    @Test
    fun deviceSignOutIconIsCenteredInsideItsCircle() {
        val row = LayoutInflater.from(context).inflate(R.layout.item_account_device, null)
        val button = row.findViewById<FrameLayout>(R.id.btn_device_logout)
        val icon = row.findViewById<ImageView>(R.id.iv_device_logout)
        val iconParams = icon.layoutParams as FrameLayout.LayoutParams

        assertThat(button.layoutParams.width).isEqualTo(48.dp())
        assertThat(button.layoutParams.height).isEqualTo(48.dp())
        assertThat(iconParams.gravity).isEqualTo(Gravity.CENTER)
        assertThat(icon.layoutParams.width).isEqualTo(22.dp())
        assertThat(icon.layoutParams.height).isEqualTo(22.dp())
    }

    @Test
    fun profileHeroHasAnAvatarAndWideAccountAction() {
        val root = LayoutInflater.from(context).inflate(R.layout.fragment_profile, null)
        val avatar = root.findViewById<ShapeableImageView>(R.id.iv_profile_avatar)

        assertThat(root.findViewById<FrameLayout>(R.id.profile_avatar_picker)).isNotNull()
        assertThat(avatar.layoutParams.width).isEqualTo(144.dp())
        assertThat(avatar.layoutParams.height).isEqualTo(144.dp())
        assertThat(root.findViewById<MaterialButton>(R.id.btn_manage_account).layoutParams.width)
            .isEqualTo(ViewGroup.LayoutParams.MATCH_PARENT)
        val proChevron = root.findViewById<ImageView>(R.id.pro_chevron)
        assertThat(proChevron.contentDescription).isNull()
        assertThat(proChevron.isClickable).isFalse()
    }

    private fun Int.dp(): Int = (this * context.resources.displayMetrics.density).toInt()
}
