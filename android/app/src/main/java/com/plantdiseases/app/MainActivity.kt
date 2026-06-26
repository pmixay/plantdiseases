package com.plantdiseases.app

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.animation.OvershootInterpolator
import androidx.appcompat.app.AppCompatActivity
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.AppBarConfiguration
import androidx.navigation.ui.navigateUp
import androidx.navigation.ui.setupActionBarWithNavController
import androidx.navigation.ui.setupWithNavController
import com.plantdiseases.app.databinding.ActivityMainBinding
import com.plantdiseases.app.ui.onboarding.OnboardingActivity
import com.plantdiseases.app.util.LocaleHelper

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var appBarConfiguration: AppBarConfiguration

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
        if (savedInstanceState == null) {
            installSplashScreen()
        } else {
            setTheme(R.style.Theme_PlantDiseases)
        }
        super.onCreate(savedInstanceState)

        // Check onboarding
        if (!OnboardingActivity.isOnboardingDone(this)) {
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }

        maybeShowConsentDialog()

        // Edge-to-edge display
        WindowCompat.setDecorFitsSystemWindows(window, false)

        binding = ActivityMainBinding.inflate(layoutInflater)
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
        // closed notifies now instead of at the next periodic tick.
        if (com.plantdiseases.app.util.OpinionPrefs.notificationsEnabled(this)) {
            (application as PlantDiseasesApp).apply {
                scheduleOpinionChecks()
                checkOpinionsNow()
            }
            com.plantdiseases.app.push.PushManager.registerIfEnabled(this)
        }
        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        val navController = navHostFragment.navController

        // setupWithNavController wires both the tab→destination navigation
        // and the destination→tab selection sync. We keep it for the latter,
        // then override the item-selected listener so tab switches get the
        // slide-in/fade transition (R.anim.frag_*) the sibling MetalRestrict
        // app uses. The destination-changed listener it installs is separate
        // and survives this override, so the checked tab still stays in sync
        // with programmatic navigations (e.g. the profile/gallery CTAs).
        // The five bottom-nav tabs are the top-level destinations (no Up
        // arrow). Anything reached *from* a tab — e.g. the disease guide,
        // now opened from the Home tools grid rather than its own tab —
        // is non-top-level, so setupActionBarWithNavController shows a back
        // arrow on the shared toolbar and onSupportNavigateUp pops it.
        appBarConfiguration = AppBarConfiguration(
            setOf(
                R.id.homeFragment,
                R.id.cameraFragment,
                R.id.gardenFragment,
                R.id.galleryFragment,
                R.id.profileFragment,
            ),
        )
        setupActionBarWithNavController(navController, appBarConfiguration)

        binding.bottomNavigation.setupWithNavController(navController)
        binding.bottomNavigation.setOnItemSelectedListener { item ->
            val navOptions = androidx.navigation.NavOptions.Builder()
                .setLaunchSingleTop(true)
                .setRestoreState(true)
                .setEnterAnim(R.anim.frag_enter)
                .setExitAnim(R.anim.frag_exit)
                .setPopEnterAnim(R.anim.frag_pop_enter)
                .setPopExitAnim(R.anim.frag_pop_exit)
                // Pop back to the start destination, saving state, so the
                // back stack and per-tab scroll state behave exactly as they
                // did under setupWithNavController.
                .setPopUpTo(
                    navController.graph.startDestinationId,
                    inclusive = false,
                    saveState = true,
                )
                .build()
            try {
                navController.navigate(item.itemId, null, navOptions)
                true
            } catch (e: IllegalArgumentException) {
                false
            }
        }

        navController.addOnDestinationChangedListener { _, destination, _ ->
            // Garden + Profile render their own full-bleed green hero as the
            // header, so the shared toolbar bar is hidden there — otherwise it
            // stacks a second title above the hero with a white gap (the
            // "cut off" look). Every other destination keeps the toolbar.
            binding.appBar.visibility = when (destination.id) {
                R.id.gardenFragment, R.id.profileFragment -> View.GONE
                else -> View.VISIBLE
            }
            when (destination.id) {
                R.id.cameraFragment -> supportActionBar?.title = getString(R.string.tab_scan)
                R.id.galleryFragment -> {
                    supportActionBar?.title = getString(R.string.tab_gallery)
                    // Clear gallery badge when opening gallery tab
                    clearGalleryBadge()
                }
                R.id.gardenFragment -> supportActionBar?.title = getString(R.string.tab_garden)
                R.id.guideFragment -> supportActionBar?.title = getString(R.string.tab_guide)
                R.id.profileFragment -> supportActionBar?.title = getString(R.string.tab_profile)
            }
        }

        // Subtle bounce on Scan icon after onboarding
        val prefs = getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
        val firstMainLaunch = !prefs.getBoolean("first_main_launched", false)
        if (firstMainLaunch) {
            prefs.edit().putBoolean("first_main_launched", true).apply()
            binding.bottomNavigation.post {
                bounceScanIcon()
            }
        }

        // A cold-started "New scan" (MainActivity wasn't already in memory)
        // routes through onCreate rather than onNewIntent.
        handleOpenTabIntent(intent)
    }

    /**
     * Do not request runtime permissions on the default Home screen. Camera is
     * requested by CameraFragment when the user opens Scan; location and
     * notifications remain opt-in at their feature entry points.
     */
    private fun maybeWarmStartupCaches() {
        // Only warm the region cache for users who already opted into
        // community — a no-op without a prior COARSE_LOCATION grant, and never
        // triggers a location read for someone who hasn't opted in.
        // No-op: country is now resolved server-side from request metadata.
    }

    /** Upgrade path for installs that completed onboarding before the consent
     *  page existed: a one-time BLOCKING dialog with the same disclosure +
     *  Agree button (no decline path — Play "prominent disclosure" pattern,
     *  same as the onboarding consent page new installs see). */
    private fun maybeShowConsentDialog() {
        if (OnboardingActivity.isDataConsentGiven(this)) return
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle(R.string.consent_title)
            .setMessage(R.string.consent_body)
            .setCancelable(false)
            .setPositiveButton(R.string.consent_agree) { _, _ ->
                OnboardingActivity.setDataConsentGiven(this)
            }
            .setNeutralButton(R.string.consent_privacy_link, null)
            .create()
            .apply {
                setOnShowListener {
                    // Keep the dialog open when the privacy link is tapped —
                    // the default neutral handler would dismiss it and let
                    // the user bypass Agree.
                    getButton(android.app.AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
                        startActivity(
                            Intent(
                                Intent.ACTION_VIEW,
                                android.net.Uri.parse(getString(R.string.consent_privacy_url)),
                            ),
                        )
                    }
                }
            }
            .show()
    }

    private fun warmRegionCache() {
        // No-op: kept only for binary/source compatibility inside this class.
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

    // Bounce animation on Scan tab icon
    private fun bounceScanIcon() {
        val menuView = binding.bottomNavigation.getChildAt(0) as? android.view.ViewGroup ?: return
        if (menuView.childCount > 1) {
            val scanItem = menuView.getChildAt(1) // Second item = Scan (Home is first)
            scanItem.scaleX = 0.8f
            scanItem.scaleY = 0.8f
            scanItem.animate()
                .scaleX(1f)
                .scaleY(1f)
                .setDuration(500)
                .setInterpolator(OvershootInterpolator(3f))
                .setStartDelay(300)
                .start()
        }
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
