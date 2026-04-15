package com.plantdiseases.app

import android.content.Context
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import androidx.appcompat.app.AppCompatActivity
import androidx.navigation.fragment.NavHostFragment
import androidx.navigation.ui.setupWithNavController
import com.plantdiseases.app.databinding.ActivityMainBinding
import com.plantdiseases.app.util.LocaleHelper

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayShowTitleEnabled(true)

        val navHostFragment = supportFragmentManager
            .findFragmentById(R.id.nav_host_fragment) as NavHostFragment
        val navController = navHostFragment.navController

        binding.bottomNavigation.setupWithNavController(navController)

        navController.addOnDestinationChangedListener { _, destination, _ ->
            when (destination.id) {
                R.id.cameraFragment -> supportActionBar?.title = getString(R.string.tab_scan)
                R.id.galleryFragment -> supportActionBar?.title = getString(R.string.tab_gallery)
                R.id.guideFragment -> supportActionBar?.title = getString(R.string.tab_guide)
                R.id.profileFragment -> supportActionBar?.title = getString(R.string.tab_profile)
            }
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.menu_main, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.action_language -> {
                LocaleHelper.toggleLanguage(this)
                recreate()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }
}
