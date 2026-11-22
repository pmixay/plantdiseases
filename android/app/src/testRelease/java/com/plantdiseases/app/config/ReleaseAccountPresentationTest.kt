package com.plantdiseases.app.config

import android.content.Context
import android.view.ContextThemeWrapper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.PopupMenu
import androidx.test.core.app.ApplicationProvider
import com.google.android.material.button.MaterialButton
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ReleaseAccountPresentationTest {
    private val context = ContextThemeWrapper(
        ApplicationProvider.getApplicationContext<Context>(),
        R.style.Theme_PlantDiseases,
    )

    @Test
    fun releaseLayoutDefaultsToYandexAvailableAndVkHidden() {
        val root = LayoutInflater.from(context).inflate(R.layout.activity_account, null)
        val yandex = root.findViewById<MaterialButton>(R.id.btn_yandex)
        val vk = root.findViewById<MaterialButton>(R.id.btn_vk)

        assertThat(yandex.visibility).isEqualTo(View.VISIBLE)
        assertThat(yandex.isEnabled).isTrue()
        assertThat(yandex.icon).isNotNull()
        // A provider that isn't configured is not shown at all (no "coming
        // soon" state); AccountActivity reveals VK only when it is enabled.
        assertThat(vk.visibility).isEqualTo(View.GONE)
        assertThat(vk.isEnabled).isFalse()
        assertThat(vk.text.toString()).isEqualTo(context.getString(R.string.account_continue_vk))
    }

    @Test
    fun profileCreateAccountActionIsFullWidthAndProminent() {
        val root = LayoutInflater.from(context).inflate(R.layout.fragment_profile, null)
        val createAccount = root.findViewById<MaterialButton>(R.id.btn_manage_account)

        assertThat(createAccount.layoutParams.width).isEqualTo(ViewGroup.LayoutParams.MATCH_PARENT)
        assertThat(createAccount.minimumHeight).isAtLeast((56 * context.resources.displayMetrics.density).toInt())
    }

    @Test
    fun releaseCommunityDetailExposesReportAndBlockActions() {
        val root = LayoutInflater.from(context).inflate(R.layout.sheet_community_scan, null)

        assertThat(root.findViewById<MaterialButton>(R.id.btn_community_report_scan).visibility)
            .isEqualTo(View.VISIBLE)
        assertThat(root.findViewById<MaterialButton>(R.id.btn_community_block_scan_author).visibility)
            .isEqualTo(View.VISIBLE)
        assertThat(context.resources.getStringArray(R.array.community_report_reasons)).hasLength(7)
    }

    @Test
    fun releaseCommunityMenuExposesRulesModerationContactAndBlockManagement() {
        val anchor = View(context)
        val menu = PopupMenu(context, anchor)
        menu.menuInflater.inflate(R.menu.community_map_menu, menu.menu)

        assertThat(menu.menu.findItem(R.id.action_community_safety)).isNotNull()
        assertThat(context.getString(R.string.community_rules_body)).isNotEmpty()
        assertThat(context.getString(R.string.community_contact_moderation)).isNotEmpty()
        assertThat(context.getString(R.string.community_manage_blocks)).isNotEmpty()
        assertThat(context.getString(R.string.community_moderation_url)).startsWith("mailto:")
    }
}
