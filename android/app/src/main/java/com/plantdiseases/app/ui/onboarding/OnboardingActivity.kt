package com.plantdiseases.app.ui.onboarding

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityOnboardingBinding
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.OnboardingStateStore
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

/**
 * First-launch prominent disclosure.
 *
 * This is deliberately a single blocking page: the user can read the canonical
 * data-use disclosure and privacy policy, but [MainActivity] is reachable only
 * through Agree. Feature discovery continues later as small, once-only coach
 * marks in context instead of a swipe tour the user has to remember.
 */
@AndroidEntryPoint
class OnboardingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityOnboardingBinding

    @Inject
    lateinit var onboardingState: OnboardingStateStore

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityOnboardingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.consentPrivacyLink.setOnClickListener {
            startActivity(
                Intent(
                    Intent.ACTION_VIEW,
                    Uri.parse(getString(R.string.consent_privacy_url)),
                ),
            )
        }
        binding.consentAgree.setOnClickListener { finishOnboarding() }
    }

    private fun finishOnboarding() {
        // These flags remain one transaction: no route may mark the tour done
        // without also recording explicit consent.
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ONBOARDING_DONE, true)
            .putBoolean(KEY_DATA_CONSENT, true)
            .apply()
        // This explicit call is what distinguishes a genuinely new install
        // from an upgrade whose versioned guidance store does not exist yet.
        onboardingState.startForNewInstall()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    companion object {
        const val PREFS_NAME = "plantdiseases_prefs"
        const val KEY_ONBOARDING_DONE = "onboarding_done"
        const val KEY_DATA_CONSENT = "data_consent_given"

        fun isOnboardingDone(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getBoolean(KEY_ONBOARDING_DONE, false)

        fun isDataConsentGiven(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getBoolean(KEY_DATA_CONSENT, false)

        fun setDataConsentGiven(context: Context) {
            context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_DATA_CONSENT, true)
                .apply()
        }
    }
}
