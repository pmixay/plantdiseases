package com.plantdiseases.app

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.animation.OvershootInterpolator
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.os.LocaleListCompat
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
    private var isFirstLaunch = false

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)

        // Check onboarding
        if (!OnboardingActivity.isOnboardingDone(this)) {
            isFirstLaunch = true
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }

        // Edge-to-edge display
        WindowCompat.setDecorFitsSystemWindows(window, false)

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Handle insets for edge-to-edge
        ViewCompat.setOnApplyWindowInsetsListener(binding.toolbar) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(top = systemBars.top)
            insets
        }
        ViewCompat.setOnApplyWindowInsetsListener(binding.bottomNavigation) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.updatePadding(bottom = systemBars.bottom)
            insets
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
                    // Clear gallery badge when opening gallery tab (2.10)
                    clearGalleryBadge()
                }
                R.id.guideFragment -> supportActionBar?.title = getString(R.string.tab_guide)
                R.id.profileFragment -> supportActionBar?.title = getString(R.string.tab_profile)
            }
        }

        // Subtle bounce on Scan icon after onboarding (2.10)
        val prefs = getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
        val firstMainLaunch = !prefs.getBoolean("first_main_launched", false)
        if (firstMainLaunch) {
            prefs.edit().putBoolean("first_main_launched", true).apply()
            binding.bottomNavigation.post {
                bounceScanIcon()
            }
        }
    }

    // Bounce animation on Scan tab icon (2.10)
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

    // Gallery badge for new results (2.10)
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
        // Check if there's a new scan result to show badge (2.10)
        val prefs = getSharedPreferences("plantdiseases_prefs", MODE_PRIVATE)
        if (prefs.getBoolean("new_scan_result", false)) {
            prefs.edit().putBoolean("new_scan_result", false).apply()
            showGalleryBadge()
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.menu_main, menu)
        return true
    }

    // Smooth language change (2.12)
    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.action_language -> {
                val newLang = LocaleHelper.toggleLanguage(this)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    // API 33+: smooth locale change without recreate
                    val localeList = LocaleListCompat.forLanguageTags(newLang)
                    AppCompatDelegate.setApplicationLocales(localeList)
                } else {
                    // Fallback for older APIs
                    recreate()
                }
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }
}
