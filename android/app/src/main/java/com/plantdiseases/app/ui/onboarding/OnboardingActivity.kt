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
import com.plantdiseases.app.util.PrivacyConsentPrefs
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

/**
 * First-launch privacy choices.
 *
 * The user must acknowledge the page, but both optional data uses are separate
 * opt-ins and default off. Inference remains available with neither enabled.
 */
@AndroidEntryPoint
class OnboardingActivity : AppCompatActivity() {

    private lateinit var binding: ActivityOnboardingBinding

    @Inject
    lateinit var onboardingState: OnboardingStateStore

    @Inject
    lateinit var privacyConsentPrefs: PrivacyConsentPrefs

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
        binding.switchTrainingConsent.isChecked = privacyConsentPrefs.trainingEnabled
        binding.switchTelemetryConsent.isChecked = privacyConsentPrefs.telemetryEnabled
        binding.consentAgree.setOnClickListener { finishOnboarding() }
    }

    private fun finishOnboarding() {
        privacyConsentPrefs.setTrainingEnabled(binding.switchTrainingConsent.isChecked)
        privacyConsentPrefs.setTelemetryEnabled(binding.switchTelemetryConsent.isChecked)
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ONBOARDING_DONE, true)
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

        fun isOnboardingDone(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getBoolean(KEY_ONBOARDING_DONE, false)

    }
}
