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
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.billing.EntitlementRepository
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.databinding.ActivityMainBinding
import com.plantdiseases.app.ui.community.CommunityJoin
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
import kotlinx.coroutines.launch

@AndroidEntryPoint
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    /** The tab pager, once the layout has created it. Set from
     *  [ShellFragment.onAttach]; its view can still be absent (cold start). */
    var shell: ShellFragment? = null

    /** A tab to land on as soon as the shell's pager exists: a tap that
     *  arrived before it did (cold start via [EXTRA_OPEN_SCANNER]). */
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

    @Inject
    lateinit var notificationRepository: NotificationRepository

    private lateinit var onboardingCoordinator: OnboardingCoordinator

    /** Joining the community from a tab (Home's popular section). Built here
     *  because it registers a permission launcher, which only an activity
     *  being created may do. */
    val communityJoin = CommunityJoin(this)

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    /** One-time question about minimal error reports: an icon card with Yes / No
     *  at the bottom. Yes is the positive (default) action. */
    private fun maybeAskTelemetryChoice() {
        val prefs = PrivacyConsentPrefs(this)
        if (!prefs.telemetryChoicePending) return
        MaterialAlertDialogBuilder(this)
            .setView(R.layout.dialog_telemetry_consent)
            .setCancelable(false)
            .setPositiveButton(R.string.telemetry_prompt_yes) { _, _ -> prefs.setTelemetryEnabled(true) }
            .setNegativeButton(R.string.telemetry_prompt_no) { _, _ -> prefs.setTelemetryEnabled(false) }
            .show()
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
        if (savedInstanceState == null) maybeAskTelemetryChoice()

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

        // Answers on the user's community posts: polled instead of pushed
        // (no FCM in production), scheduled from a UI context rather than
        // Application.onCreate, and off when the user turned them off.
        (application as PlantDiseasesApp).scheduleForumAnswerChecks()
        // The retired second-opinion poll: remove its work from installs that
        // still have it queued, or WorkManager keeps a job for a deleted class.
        androidx.work.WorkManager.getInstance(this).apply {
            cancelUniqueWork("opinion_check")
            cancelUniqueWork("opinion_check_now")
        }
        com.plantdiseases.app.push.PushManager.registerIfEnabled(this)
        // Warm the guide/disease example photos into Glide's disk cache so they
        // render offline (they're server-hosted, not bundled). Independent of
        // notifications — always worth doing on a connected launch.
        (application as PlantDiseasesApp).prefetchGuidePhotos()
        // A tab tap pages the shell's pager. The bar stays here (rather than
        // moving into the shell) because the insets, badge and splash
        // animation above are all activity-scoped.
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
        binding.shellHost.alpha = 0f
        binding.shellHost.translationY = 24f * density
        binding.shellHost.animate()
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
        val pager = shell?.takeIf { it.view != null }
        if (pager != null) {
            pager.showTab(tabId, animate = animate)
        } else {
            // The shell hasn't been created yet (cold start). Park the
            // request; the shell picks it up when its view is created.
            pendingTabId = tabId
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

    /** Toolbar state for the shell's pager, driven by the shell.
     *
     *  The shared toolbar stays hidden on every tab: it used to carry each
     *  page's name, but that title lived outside the pager and only updated
     *  once a swipe settled, so mid-drag it read as a static label floating
     *  disconnected from the page sliding underneath it. Every tab
     *  now renders its own title as part of its own page (Garden/Profile
     *  already did this with their hero card), so it slides with the swipe
     *  like the rest of the page content. */
    fun applyTabChrome(@IdRes tabId: Int) {
        binding.appBar.visibility = View.GONE
        // Pages are plain light surfaces with dark bar icons; the camera tab
        // draws its black preview under the status bar and needs light ones.
        val nightMask = resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK
        val night = nightMask == android.content.res.Configuration.UI_MODE_NIGHT_YES
        val darkIcons = !night && tabId != R.id.cameraFragment
        WindowCompat.getInsetsController(window, binding.root).isAppearanceLightStatusBars = darkIcons
        if (tabId == R.id.profileFragment) clearGalleryBadge()
        // The Community tab has no guided tour of its own: the existing
        // Community chapter is anchored in the Nearby screen, which runs it.
        onboardingCoordinator.setContext(
            when (tabId) {
                R.id.homeFragment -> OnboardingContext.HOME
                R.id.cameraFragment -> OnboardingContext.CAMERA
                R.id.gardenFragment -> OnboardingContext.GARDEN
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
            OnboardingContext.PROFILE -> R.id.profileFragment
            // My scans now opens from Profile, outside the tab bar.
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

    // New-result badge. My scans lives under Profile now, so the badge does too.
    fun showGalleryBadge() {
        val badge = binding.bottomNavigation.getOrCreateBadge(R.id.profileFragment)
        badge.isVisible = true
        badge.number = 1
    }

    private fun clearGalleryBadge() {
        binding.bottomNavigation.removeBadge(R.id.profileFragment)
    }

    /** Unread community activity (answers, replies, accepted answers). */
    /**
     * A red dot on the Главная tab, Android notification-dot style, while the
     * notification centre has unread rows. The count itself is on Home's bell.
     */
    fun refreshHomeBadge() {
        lifecycleScope.launch {
            val unread = notificationRepository.getUnreadCount()
            if (!::binding.isInitialized) return@launch
            if (unread <= 0) {
                binding.bottomNavigation.removeBadge(R.id.homeFragment)
            } else {
                binding.bottomNavigation.getOrCreateBadge(R.id.homeFragment).apply {
                    clearNumber()
                    isVisible = true
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refreshHomeBadge()
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
