package com.plantdiseases.app.ui.shell

import android.content.Context
import androidx.appcompat.view.ContextThemeWrapper
import androidx.test.core.app.ApplicationProvider
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Guards the two contracts the swipe shell rests on that nothing else can catch:
 * the page axis agreeing with the bottom bar, and the bar's own checked-item
 * behaviour that [com.plantdiseases.app.MainActivity.checkTabSilently] assumes.
 *
 * The gesture itself (drag → neighbour tracks the finger → release snaps to the
 * nearest page) is ViewPager2's, and the guide push/pop round-trip needs a real
 * FragmentManager driving real pages, so both are exercised on-device rather
 * than here.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ShellFragmentTest {

    private val context: Context
        get() = ContextThemeWrapper(
            ApplicationProvider.getApplicationContext(),
            R.style.Theme_PlantDiseases,
        )

    private fun bar() = BottomNavigationView(context).apply {
        inflateMenu(R.menu.bottom_nav_menu)
    }

    private fun barTabIds(): List<Int> = bar().menu.let { menu ->
        (0 until menu.size()).map { menu.getItem(it).itemId }
    }

    @Test
    fun pageOrderMatchesBottomBarOrder() {
        // The bar is the map the user navigates by: its left-to-right order is
        // what they expect the swipe axis to be, and its ids double as the
        // pager's page keys. Nothing in the type system ties the two together,
        // so reordering the menu alone would silently mis-wire every tab.
        assertThat(ShellFragment.PAGES).isEqualTo(barTabIds())
    }

    @Test
    fun everyTabMapsToItsPageIndex() {
        ShellFragment.PAGES.forEachIndexed { index, tabId ->
            assertThat(ShellFragment.pageOf(tabId)).isEqualTo(index)
        }
    }

    @Test
    fun nonTabDestinationHasNoPage() {
        // The guide is pushed over the shell, never paged to. selectTab has to
        // be able to tell, or a tap would try to page to index -1.
        assertThat(ShellFragment.pageOf(R.id.guideFragment)).isEqualTo(-1)
    }

    @Test
    fun selectingATabUnchecksThePreviousOne() {
        // checkTabSilently drives the bar through setSelectedItemId rather than
        // poking MenuItem.isChecked, because the latter only unchecks the
        // previous item if the menu group is exclusive-checkable and
        // bottom_nav_menu.xml declares no group. This pins the behaviour that
        // choice depends on — two lit tabs is the failure it avoids.
        val bar = bar()
        bar.selectedItemId = R.id.homeFragment
        bar.selectedItemId = R.id.gardenFragment

        assertThat(bar.selectedItemId).isEqualTo(R.id.gardenFragment)
        val checked = bar.menu.let { menu ->
            (0 until menu.size()).map { menu.getItem(it) }.filter { it.isChecked }
        }
        assertThat(checked.map { it.itemId }).containsExactly(R.id.gardenFragment)
    }

    @Test
    fun settingSelectedItemIdFiresTheListener() {
        // The other half of checkTabSilently's design: because this dispatch is
        // synchronous, a re-entry flag around it is enough to tell the pager's
        // echo apart from a real tap. If this ever stopped firing, the bar's
        // taps would go dead instead.
        val bar = bar()
        val fired = mutableListOf<Int>()
        bar.setOnItemSelectedListener { item ->
            fired += item.itemId
            true
        }
        bar.selectedItemId = R.id.galleryFragment

        assertThat(fired).containsExactly(R.id.galleryFragment)
    }
}
