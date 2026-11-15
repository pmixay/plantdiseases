package com.plantdiseases.app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import androidx.annotation.IdRes
import androidx.appcompat.app.AppCompatActivity
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.navigation.NavController
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.AppBarConfiguration
import androidx.navigation.ui.navigateUp
import androidx.navigation.ui.setupActionBarWithNavController
import com.plantdiseases.app.billing.EntitlementRepository
import com.plantdiseases.app.databinding.ActivityMainBinding
import com.plantdiseases.app.ui.onboarding.OnboardingActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingChapter
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingContext
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingCoordinator
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStepId
import com.plantdiseases.app.ui.shell.ShellFragment
import com.plantdiseases.app.util.InAppUpdateManager
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.PrivacyConsentPrefs
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStateStore
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var appBarConfiguration: AppBarConfiguration
    private lateinit var navController: NavController

    /** The tab pager, once the nav host has created it. Set from
     *  [ShellFragment.onAttach], so it stays valid while the guide is pushed
     *  over the shell (that destroys the shell's view, not the fragment). */
    var shell: ShellFragment? = null

    /** A tab to land on as soon as the shell's pager exists — a tap that
     *  arrived before it did (cold start via [EXTRA_OPEN_SCANNER]) or while the
     *  guide was on top and the shell's view was torn down. */
    @IdRes
    var pendingTabId: Int? = null

    fun consumePendingTabId(): Int? = pendingTabId.also { pendingTabId = null }

    /** True while [checkTabSilently] is driving the bar, so the item-selected
     *  listener can tell the pager's echo apart from a real tap. */
    private var syncingBar = false

    /** RuStore in-app updates (flexible flow). Non-null between onCreate and
     *  onDestroy on the post-onboarding path; a no-op on installs that didn't
     *  come from RuStore. */
    private var inAppUpdateManager: InAppUpdateManager? = null

    @Inject
    lateinit var onboardingState: OnboardingStateStore

    @Inject
    lateinit var entitlementRepository: EntitlementRepository

    private lateinit var onboardingCoordinator: OnboardingCoordinator

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        // Only show the splash on a genuine cold start. A language change
        // calls AppCompatDelegate.setApplicationLocales(), which restarts
        // this activity and re-enters onCreate with a non-null
        // savedInstanceState. Re-running installSplashScreen() there
        // re-displayed the splash window (an 800ms icon animation over the
        // green/black splash background) on every toggle — the "app turns
        // black" the user saw. Theme changes use an in-process recreate
        // that never re-triggered the splash, which is why only language
        // switching flashed. On a recreate we skip the splash but still
        // swap the launch splash theme out for the real app theme — that
        // is what installSplashScreen() normally does via
        // postSplashScreenTheme.
        val splashScreen = if (savedInstanceState == null) {
            installSplashScreen()
        } else {
            setTheme(R.style.Theme_PlantDiseases)
            null
        }
        super.onCreate(savedInstanceState)

        // Check onboarding
        if (!OnboardingActivity.isOnboardingDone(this) || !PrivacyConsentPrefs(this).trainingEnabled) {
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }

        // Animated app enter (cold start only): the splash logo zooms toward
        // the viewer and dissolves while the app content rises in beneath it
        // and the bottom bar slides up. Attached only after the onboarding
        // early-return so the listener can never touch an uninitialised
        // binding.
        splashScreen?.setOnExitAnimationListener { provider -> animateSplashExit(provider) }

        // Edge-to-edge display
        WindowCompat.setDecorFitsSystemWindows(window, false)

        binding = ActivityMainBinding.inflate(layoutInflater)
        onboardingCoordinator = OnboardingCoordinator(
            host = this,
            store = onboardingState,
            undimmedViews = { activeContext ->
                if (activeContext == OnboardingContext.CAMERA && ::binding.isInitialized) {
                    listOf(binding.bottomNavigation)
                } else {
                    emptyList()
                }
            },
            navigateToContext = ::navigateOnboardingTo,
        )
        setContentView(binding.root)

        // Single root inset listener — dispatches status-bar + nav-bar + cutout insets
        // to the toolbar (top) and the bottom navigation (bottom/sides).
        // Using clipToPadding=false on the bottom nav means this padding expands
        // the view instead of squeezing icon+label into a fixed 80dp area, which
        // was the root cause of the overlapping menu on Samsung/gesture-nav devices.
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            // Padding on the AppBarLayout (wrap_content) — keeps the toolbar's own
            // height at actionBarSize, and extends the gradient behind the status bar.
            // Side insets also prevent toolbar action icons from being clipped by a
            // display cutout / rounded corner on edge-to-edge devices.
            binding.appBar.updatePadding(top = bars.top)
            // System-bar / cutout insets are physical (left/right are
            // unaffected by layoutDirection), so plain updatePadding is
            // the right call here — setPaddingRelative would actually
            // mirror the cutout away from the notch in RTL.
            binding.toolbar.updatePadding(left = bars.left, right = bars.right)
            binding.bottomNavigation.updatePadding(
                bottom = bars.bottom,
                left = bars.left,
                right = bars.right,
            )
            insets
        }

        // NB: the nav host no longer needs bottom padding to clear the bottom
        // navigation. The shell is a vertical LinearLayout (app bar / nav host /
        // bottom nav), so the weight-1 nav host already ends exactly at the
        // bottom nav's top edge. The old CoordinatorLayout drew the nav host
        // *behind* the bottom nav, which is why it used to pad the nav host by
        // the bar height — doing that here just adds an empty gap above the bar.

        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayShowTitleEnabled(true)

        // Kick off the periodic second-opinion poll from a UI context (not
        // Application.onCreate, which would touch WorkManager too early), plus
        // an immediate catch-up check so a reply that arrived while the app was
        // closed records now instead of at the next periodic tick. The poll
        // runs even when popup notifications are off because it also drives
        // Community V2's in-app notification centre.
        val opinionAction = opinionWorkAction(
            BuildConfig.ACCOUNT_UI_ENABLED,
            com.plantdiseases.app.util.OpinionPrefs.notificationsEnabled(this),
        )
        when (opinionAction) {
            OpinionWorkAction.CANCEL -> {
                // Recovery/account-disabled builds must remove work inherited
                // from v19 before it can touch private account endpoints.
                androidx.work.WorkManager.getInstance(this).apply {
                    cancelUniqueWork("opinion_check")
                    cancelUniqueWork("opinion_check_now")
                }
            }

            OpinionWorkAction.SCHEDULE -> {
                (application as PlantDiseasesApp).apply {
                    scheduleOpinionChecks()
                    checkOpinionsNow()
                }
            }

            OpinionWorkAction.NONE -> Unit
        }
        com.plantdiseases.app.push.PushManager.registerIfEnabled(this)
        // Warm the guide/disease example photos into Glide's disk cache so they
        // render offline (they're server-hosted, not bundled). Independent of
        // notifications — always worth doing on a connected launch.
        (application as PlantDiseasesApp).prefetchGuidePhotos()
        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        navController = navHostFragment.navController

        // The tabs are no longer destinations — they're pages of the shell's
        // pager — so the graph is down to two entries and the shell is the only
        // top-level one (no Up arrow). Anything pushed over it, i.e. the disease
        // guide from Home's tools grid, is non-top-level, so this shows a back
        // arrow on the shared toolbar and onSupportNavigateUp pops it.
        appBarConfiguration = AppBarConfiguration(setOf(R.id.shellFragment))
        setupActionBarWithNavController(navController, appBarConfiguration)

        // setupWithNavController is gone with the tab destinations: it existed
        // to turn a tap into a navigate() and to check the tab on arrival, and
        // both of those are now the pager's job. The bar stays here (rather than
        // moving into the shell) because it has to outlive the shell's view when
        // the guide is pushed, and because the insets/badge/splash animation
        // above are all activity-scoped.
        binding.bottomNavigation.setOnItemSelectedListener { item ->
            if (syncingBar) true else selectTab(item.itemId)
        }
        binding.bottomNavigation.findViewById<View>(R.id.cameraFragment)?.let { scanItem ->
            onboardingCoordinator.registerAnchor(
                key = OnboardingAnchorKey.BOTTOM_NAV_SCAN,
                view = scanItem,
                pauseAfterTarget = true,
            )
        }

        navController.addOnDestinationChangedListener { _, destination, _ ->
            // Pushed destinations always keep the toolbar — that's where their
            // back arrow lives — and their title comes from android:label via
            // setupActionBarWithNavController. The shell's chrome is not set
            // here: ShellFragment applies it per page as swipes settle, and
            // re-applies it in onViewCreated, which covers the pop back to here.
            if (destination.id != R.id.shellFragment) {
                binding.appBar.visibility = View.VISIBLE
            }
        }

        // A cold-started "New scan" (MainActivity wasn't already in memory)
        // routes through onCreate rather than onNewIntent.
        handleOpenTabIntent(intent)

        // RuStore in-app updates: when this install came from RuStore and a
        // newer build is published there, offer the store's flexible-update
        // dialog (at most once a day) and an install snackbar once the new APK
        // has downloaded. Anywhere else the check fails inside the SDK and the
        // launch continues untouched. Anchored above the bottom bar so the
        // snackbar doesn't cover the tabs.
        inAppUpdateManager = InAppUpdateManager(
            context = this,
            hostView = binding.root,
            snackbarAnchor = binding.bottomNavigation,
        ).also { it.checkForUpdates() }
    }

    override fun onDestroy() {
        inAppUpdateManager?.release()
        inAppUpdateManager = null
        super.onDestroy()
    }

    // Up arrow on non-top-level destinations (e.g. the disease guide opened
    // from Home) pops the nav back stack via the shared toolbar.
    override fun onSupportNavigateUp(): Boolean {
        val navController = (
            supportFragmentManager
                .findFragmentById(R.id.nav_host_fragment) as NavHostFragment
            ).navController
        return navController.navigateUp(appBarConfiguration) || super.onSupportNavigateUp()
    }

    /** Splash → app hand-off. Runs once per cold start, when the first frame
     *  is ready behind the splash window: the logo scales up and fades (a
     *  "zoom through" exit), the green splash veil dissolves, and the app
     *  builds itself in a quick stagger — page content fades in while rising
     *  a few dp, then the bottom bar slides up from below the screen edge. */
    private fun animateSplashExit(
        provider: androidx.core.splashscreen.SplashScreenViewProvider,
    ) {
        val density = resources.displayMetrics.density

        provider.iconView.animate()
            .scaleX(1.35f).scaleY(1.35f).alpha(0f)
            .setDuration(280L)
            .setInterpolator(android.view.animation.AccelerateInterpolator())
            .start()
        provider.view.animate()
            .alpha(0f)
            .setDuration(320L)
            .setStartDelay(80L)
            .withEndAction { provider.remove() }
            .start()

        val decelerate = android.view.animation.DecelerateInterpolator(2f)
        binding.navHostFragment.alpha = 0f
        binding.navHostFragment.translationY = 24f * density
        binding.navHostFragment.animate()
            .alpha(1f).translationY(0f)
            .setDuration(360L).setStartDelay(120L)
            .setInterpolator(decelerate)
            .start()

        binding.appBar.alpha = 0f
        binding.appBar.animate()
            .alpha(1f)
            .setDuration(300L).setStartDelay(120L)
            .start()

        val navHeight = binding.bottomNavigation.height.toFloat()
            .takeIf { it > 0f } ?: (80f * density)
        binding.bottomNavigation.translationY = navHeight
        binding.bottomNavigation.animate()
            .translationY(0f)
            .setDuration(360L).setStartDelay(160L)
            .setInterpolator(decelerate)
            .start()
    }

    /**
     * Handle a bottom-bar tap (or a programmatic `selectedItemId =`, which the
     * Home/Gallery/Profile "scan" CTAs use) by paging to that tab.
     *
     * Returns false for an id that isn't a tab, which leaves the bar's selection
     * where it was.
     */
    private fun selectTab(@IdRes tabId: Int, animate: Boolean = true): Boolean {
        if (ShellFragment.pageOf(tabId) < 0) return false
        val onShell = navController.currentDestination?.id == R.id.shellFragment
        val pager = shell?.takeIf { it.view != null && onShell }
        if (pager != null) {
            pager.showTab(tabId, animate = animate)
        } else {
            // Either the shell hasn't been created yet (cold start), or the
            // guide is on top of it. Park the request; the shell picks it up
            // when its view is (re)created.
            pendingTabId = tabId
            if (!onShell) navController.popBackStack(R.id.shellFragment, false)
        }
        return true
    }

    /**
     * Check a tab in the bar because the *pager* moved there.
     *
     * setSelectedItemId dispatches through the item-selected listener
     * synchronously, so it is gated: without the flag, reporting the landing
     * page would bounce straight back into [selectTab] and page on top of a
     * page change already in flight. (Poking `menu.findItem(id).isChecked`
     * instead would dodge the listener but only unchecks the previous tab if
     * the menu group is exclusive-checkable, and bottom_nav_menu.xml declares
     * no group — that route can leave two tabs lit.)
     */
    fun checkTabSilently(@IdRes tabId: Int) {
        if (binding.bottomNavigation.selectedItemId == tabId) return
        syncingBar = true
        try {
            binding.bottomNavigation.selectedItemId = tabId
        } finally {
            syncingBar = false
        }
    }

    /** Toolbar state for the shell's pager. Driven by the shell, not by the
     *  NavController, since the tabs aren't destinations any more.
     *
     *  The shared toolbar stays hidden on every tab: it used to carry each
     *  page's name, but that title lived outside the pager and only updated
     *  once a swipe settled, so mid-drag it read as a static label floating
     *  disconnected from the page sliding underneath it. Every tab
     *  now renders its own title as part of its own page (Garden/Profile
     *  already did this with their hero card), so it slides with the swipe
     *  like the rest of the page content. The bar itself only reappears for a
     *  pushed destination (the guide), which needs it for the title + back
     *  arrow — see the [NavController] destination listener in [onCreate]. */
    fun applyTabChrome(@IdRes tabId: Int) {
        binding.appBar.visibility = View.GONE
        if (tabId == R.id.galleryFragment) clearGalleryBadge()
        onboardingCoordinator.setContext(
            when (tabId) {
                R.id.homeFragment -> OnboardingContext.HOME
                R.id.cameraFragment -> OnboardingContext.CAMERA
                R.id.gardenFragment -> OnboardingContext.GARDEN
                R.id.galleryFragment -> OnboardingContext.GALLERY
                R.id.profileFragment -> OnboardingContext.PROFILE
                else -> null
            },
        )
    }

    private fun navigateOnboardingTo(target: OnboardingContext): Boolean {
        val tabId = when (target) {
            OnboardingContext.HOME -> R.id.homeFragment
            OnboardingContext.CAMERA -> R.id.cameraFragment
            OnboardingContext.GARDEN -> R.id.gardenFragment
            OnboardingContext.GALLERY -> R.id.galleryFragment
            OnboardingContext.PROFILE -> R.id.profileFragment
            else -> return false
        }
        binding.bottomNavigation.selectedItemId = tabId
        return true
    }

    fun registerOnboardingAnchor(
        key: OnboardingAnchorKey,
        view: View,
        available: () -> Boolean = { view.isShown },
        pauseAfterTarget: Boolean = false,
        onTarget: () -> Unit = { view.performClick() },
        onInlineAction: (() -> Unit)? = null,
    ) {
        onboardingCoordinator.registerAnchor(
            key,
            view,
            available,
            pauseAfterTarget,
            onTarget,
            onInlineAction,
        )
    }

    fun unregisterOnboardingAnchor(key: OnboardingAnchorKey, view: View? = null) {
        onboardingCoordinator.unregisterAnchor(key, view)
    }

    fun resumeOnboardingAfterExternalUi() = onboardingCoordinator.resumeAfterExternalUi()

    fun moveOnboardingTo(step: OnboardingStepId) = onboardingCoordinator.moveTo(step)

    /** Garden's inline guidance link: remember the Result continuation, then open Scanner. */
    fun startGardenAttachScanGuidance() {
        onboardingState.completeChapter(OnboardingChapter.GARDEN)
        onboardingState.activateChapter(OnboardingChapter.GARDEN_SCAN_ATTACH)
        binding.bottomNavigation.selectedItemId = R.id.cameraFragment
    }

    /** Profile's manual entry point; consent and normal preferences are untouched. */
    fun replayAppGuidance() {
        onboardingCoordinator.replay()
        selectTab(R.id.homeFragment, animate = false)
        applyTabChrome(R.id.homeFragment)
    }

    // Gallery badge for new results
    fun showGalleryBadge() {
        val badge = binding.bottomNavigation.getOrCreateBadge(R.id.galleryFragment)
        badge.isVisible = true
        badge.number = 1
    }

    private fun clearGalleryBadge() {
        binding.bottomNavigation.removeBadge(R.id.galleryFragment)
    }

    override fun onResume() {
        super.onResume()
        (application as PlantDiseasesApp).retryPendingAccountLogouts()
        // RuStore callbacks, renewals, cancellations and grace/hold changes
        // can happen while this process stays alive. Refresh from the account
        // server on foreground; the repository coalesces concurrent resumes.
        entitlementRepository.refreshInBackground()
        // Check if there's a new scan result to show badge
        val prefs = getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
        if (prefs.getBoolean("new_scan_result", false)) {
            prefs.edit().putBoolean("new_scan_result", false).apply()
            showGalleryBadge()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleOpenTabIntent(intent)
    }

    /** Honor an explicit "open this tab" request (e.g. the result screen's
     *  "New scan" button, which can be reached from the gallery tab and must
     *  still land the user back on the scanner). Selecting the item drives the
     *  same nav transition as a manual tab tap. */
    private fun handleOpenTabIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_OPEN_SCANNER, false) == true) {
            intent.removeExtra(EXTRA_OPEN_SCANNER)
            binding.bottomNavigation.selectedItemId = R.id.cameraFragment
        }
    }

    companion object {
        /** Intent extra: bring MainActivity to front and switch to the scanner tab. */
        const val EXTRA_OPEN_SCANNER = "extra_open_scanner"
    }
}
