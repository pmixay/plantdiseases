package com.plantdiseases.app.ui.shell

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.activity.OnBackPressedCallback
import androidx.annotation.IdRes
import androidx.fragment.app.Fragment
import androidx.viewpager2.adapter.FragmentStateAdapter
import androidx.viewpager2.widget.ViewPager2
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentShellBinding
import com.plantdiseases.app.ui.camera.CameraFragment
import com.plantdiseases.app.ui.gallery.GalleryFragment
import com.plantdiseases.app.ui.garden.GardenFragment
import com.plantdiseases.app.ui.home.HomeFragment
import com.plantdiseases.app.ui.profile.ProfileFragment

/**
 * The five bottom-nav tabs, laid out as pages on a single horizontal axis.
 *
 * Replaces the old one-tab-at-a-time destination swap. The tabs are pages of a
 * [ViewPager2], so a drag carries the neighbouring page in under the finger and
 * a release settles on whichever page is nearest; [ViewPager2.setOffscreenPageLimit]
 * of 1 keeps both neighbours built and laid out so there is never a blank panel
 * sliding in. The axis is finite — the first and last pages overscroll rather
 * than wrap.
 *
 * This fragment is the nav graph's start destination, so anything pushed *on
 * top* of the tabs (the disease guide, opened from Home's tools grid) still goes
 * through the NavController and keeps its own transition, up arrow and back
 * stack. That push destroys this fragment's view but not the fragment, and
 * [ViewPager2] saves/restores both the current page and its adapter's fragment
 * state through the normal view-state save, so a pop back lands on the page the
 * user left.
 *
 * The bottom bar is deliberately *not* here — [MainActivity] owns it (insets,
 * badge, splash animation) and it has to outlive this fragment's view for the
 * guide push. MainActivity calls [showTab] on a tap; this fragment calls back
 * into [MainActivity.applyTabChrome] as soon as the pager commits to a page.
 */
class ShellFragment : Fragment() {

    private var _binding: FragmentShellBinding? = null
    private val binding get() = _binding!!

    private val host: MainActivity? get() = activity as? MainActivity

    /** The menu id of the page currently showing. */
    @get:IdRes
    val currentTabId: Int
        get() = PAGES[_binding?.shellPager?.currentItem ?: 0]

    override fun onAttach(context: Context) {
        super.onAttach(context)
        // Set here rather than in onViewCreated: the guide push tears down this
        // fragment's view but keeps the instance on the back stack, and the bar
        // (which outlives the view) still needs a handle to page back to.
        (context as? MainActivity)?.shell = this
    }

    override fun onDetach() {
        // Only clear our own registration — a recreation can attach the new
        // instance before the old one detaches, and blindly nulling would drop
        // the live shell on the floor.
        (activity as? MainActivity)?.takeIf { it.shell === this }?.shell = null
        super.onDetach()
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentShellBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        binding.shellPager.adapter = ShellPagerAdapter(this)
        // Build both neighbours ahead of the touch. Without this the pager only
        // creates the incoming page once the drag starts, which is exactly when
        // there's no frame budget for it — the page would slide in blank and
        // populate late. 1 (not more) because every extra page is a live
        // fragment doing real work: keeping all five warm would drag in three
        // networked screens at launch.
        binding.shellPager.offscreenPageLimit = 1
        binding.shellPager.registerOnPageChangeCallback(pageChangeCallback)

        // Back from any tab returns to Home; back from Home exits. That used to
        // fall out of the nav back stack for free, but the tabs aren't
        // destinations any more, so there is nothing left to pop between them.
        // Scoped to viewLifecycleOwner so it steps aside while the guide is on
        // top — there, back has to pop the guide, which is the NavController's
        // callback's job.
        requireActivity().onBackPressedDispatcher
            .addCallback(viewLifecycleOwner, backToHomeCallback)

        // A tab tap that arrived before this view existed — a cold start via
        // EXTRA_OPEN_SCANNER, or a tap made while the guide was on top.
        host?.consumePendingTabId()?.let { tabId ->
            binding.shellPager.setCurrentItem(pageOf(tabId), false)
        }

        // Re-assert the bar + toolbar for whatever page we're on. Covers first
        // layout, a config-change restore, and the pop back from the guide —
        // onPageSelected only fires once the pager has laid out.
        syncBar()
        host?.applyTabChrome(currentTabId)
        backToHomeCallback.isEnabled = currentTabId != R.id.homeFragment
    }

    override fun onDestroyView() {
        super.onDestroyView()
        binding.shellPager.unregisterOnPageChangeCallback(pageChangeCallback)
        // The pager holds this fragment's childFragmentManager through its
        // adapter; drop it with the view so the recreated view builds a fresh
        // one against the restored state.
        binding.shellPager.adapter = null
        _binding = null
    }

    /**
     * Move to [tabId]'s page, animating across any pages in between.
     *
     * No-op-safe before the view exists: the request is parked on [MainActivity]
     * and picked up in [onViewCreated].
     */
    fun showTab(@IdRes tabId: Int, animate: Boolean) {
        val page = pageOf(tabId)
        if (page < 0) return
        val pager = _binding?.shellPager ?: run {
            host?.pendingTabId = tabId
            return
        }
        pager.setCurrentItem(page, animate)
    }

    private val backToHomeCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            _binding?.shellPager?.setCurrentItem(pageOf(R.id.homeFragment), true)
        }
    }

    private val pageChangeCallback = object : ViewPager2.OnPageChangeCallback() {
        override fun onPageSelected(position: Int) {
            // Light the tab and re-assert chrome as soon as the pager commits to
            // a target, so the bar leads the settle rather than lagging it. The
            // shared toolbar stays hidden on every page now (each page carries
            // its own title), so there's no bar resize to wait out any more.
            syncBar()
            host?.applyTabChrome(currentTabId)
            backToHomeCallback.isEnabled = position != pageOf(R.id.homeFragment)
        }
    }

    private fun syncBar() {
        host?.checkTabSilently(currentTabId)
    }

    private class ShellPagerAdapter(fragment: Fragment) : FragmentStateAdapter(fragment) {
        override fun getItemCount() = PAGES.size

        override fun createFragment(position: Int): Fragment = when (PAGES[position]) {
            R.id.homeFragment -> HomeFragment()
            R.id.cameraFragment -> CameraFragment()
            R.id.gardenFragment -> GardenFragment()
            R.id.galleryFragment -> GalleryFragment()
            R.id.profileFragment -> ProfileFragment()
            else -> error("No page for position $position")
        }
    }

    companion object {
        /**
         * Page order on the axis. Must stay in lockstep with `bottom_nav_menu.xml`
         * — the bar's item order is what the user reads as left-to-right, and the
         * ids double as this pager's page keys.
         */
        val PAGES = listOf(
            R.id.homeFragment,
            R.id.cameraFragment,
            R.id.gardenFragment,
            R.id.galleryFragment,
            R.id.profileFragment,
        )

        /** Page index for a bottom-nav menu id, or -1 if it isn't a tab. */
        fun pageOf(@IdRes tabId: Int): Int = PAGES.indexOf(tabId)
    }
}
