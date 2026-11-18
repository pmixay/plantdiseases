package com.plantdiseases.app.ui.gallery

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.commit
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityMyScansBinding
import com.plantdiseases.app.ui.community.forum.ForumInsets
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint

/**
 * My scans: the scan history that used to be the Gallery tab. The tab's place
 * in the bottom bar went to Community; the same [GalleryFragment] now opens
 * from Profile.
 */
@AndroidEntryPoint
class MyScansActivity : AppCompatActivity() {

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityMyScansBinding.inflate(layoutInflater)
        setContentView(binding.root)
        ForumInsets.apply(this, binding.appBar, binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        if (savedInstanceState == null) {
            supportFragmentManager.commit {
                replace(R.id.fragment_container, GalleryFragment())
            }
        }
    }

    companion object {
        fun intent(context: Context): Intent = Intent(context, MyScansActivity::class.java)
    }
}
