package com.plantdiseases.app

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.view.animation.OvershootInterpolator
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.setupWithNavController
import com.plantdiseases.app.databinding.ActivityMainBinding
import com.plantdiseases.app.ui.onboarding.OnboardingActivity
import com.plantdiseases.app.util.LocaleHelper

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    // Notification runtime permission (Android 13+). The result itself is
    // ignored — POST_NOTIFICATIONS is non-essential, the rest of the app
    // still works without it. We just don't want plant reminders to fail
    // silently in PlantReminderWorker because the permission was never
    // even requested.
    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* no-op; reminders gracefully skip notify() when denied */ }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)

        // Check onboarding
        if (!OnboardingActivity.isOnboardingDone(this)) {
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }

        maybeRequestNotificationPermission()

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

        // Push fragment content above the bottom navigation using its real height
        // (which now varies with the system gesture bar).
        binding.bottomNavigation.addOnLayoutChangeListener { v, _, _, _, _, _, _, _, _ ->
            val h = v.height
            if (binding.navHostFragment.paddingBottom != h) {
                binding.navHostFragment.updatePadding(bottom = h)
            }
        }

        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayShowTitleEnabled(true)

        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        val navController = navHostFragment.navController

        binding.bottomNavigation.setupWithNavController(navController)

        navController.addOnDestinationChangedListener { _, destination, _ ->
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
    }

    /**
     * Ask for POST_NOTIFICATIONS once on Android 13+. Without this prompt,
     * PlantReminderWorker silently no-ops every time it fires
     * (PlantReminderWorker.kt:31-36) and users see no watering reminders.
     */
    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) return
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    // Bounce animation on Scan tab icon
    private fun bounceScanIcon() {
        val menuView = binding.bottomNavigation.getChildAt(0) as? android.view.ViewGroup ?: return
        if (menuView.childCount > 0) {
            val scanItem = menuView.getChildAt(0) // First item = Scan
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

    /**
     * Handle locale (and dark-mode) changes in place instead of letting
     * the system tear down the activity. The AndroidManifest declares
     * ``configChanges="locale|layoutDirection|uiMode|fontScale"`` so
     * the OS calls this hook directly; we re-bind every visible label
     * by re-attaching the active Nav fragment, which inflates against
     * the freshly-localised resources without the white-flash you get
     * from ``Activity.recreate()``.
     */
    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // Update toolbar title for the currently-visible destination.
        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.nav_host_fragment) as? NavHostFragment ?: return
        val current = navHostFragment.navController.currentDestination
        when (current?.id) {
            R.id.cameraFragment -> supportActionBar?.title = getString(R.string.tab_scan)
            R.id.galleryFragment -> supportActionBar?.title = getString(R.string.tab_gallery)
            R.id.gardenFragment -> supportActionBar?.title = getString(R.string.tab_garden)
            R.id.guideFragment -> supportActionBar?.title = getString(R.string.tab_guide)
            R.id.profileFragment -> supportActionBar?.title = getString(R.string.tab_profile)
        }
        // Re-bind the visible fragment so its layout inflates against
        // the new locale's resources. detach+attach preserves the
        // fragment instance state (scroll position, view models) and
        // animates a soft fade instead of a hard flicker.
        val active = navHostFragment.childFragmentManager.primaryNavigationFragment
        if (active != null) {
            navHostFragment.childFragmentManager.beginTransaction()
                .setReorderingAllowed(true)
                .detach(active)
                .attach(active)
                .commitNowAllowingStateLoss()
        }
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

}
