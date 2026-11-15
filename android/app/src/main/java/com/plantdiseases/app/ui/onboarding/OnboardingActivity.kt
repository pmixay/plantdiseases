package com.plantdiseases.app.ui.onboarding

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.databinding.ActivityOnboardingBinding
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.ui.legal.PrivacyPolicyActivity
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStateStore
import com.plantdiseases.app.util.PrivacyConsentPrefs
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

/**
 * First-launch privacy choices.
 *
 * The user cannot continue without accepting model improvement (de-identified
 * images feed the training store); there is no switch for it. Error reports stay
 * a separate opt-in that defaults off.
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
            startActivity(PrivacyPolicyActivity.intent(this))
        }
        binding.switchTelemetryConsent.isChecked = privacyConsentPrefs.telemetryEnabled
        binding.consentAgree.setOnClickListener { finishOnboarding() }
    }

    private fun finishOnboarding() {
        privacyConsentPrefs.setTrainingEnabled(true)
        privacyConsentPrefs.setTelemetryEnabled(binding.switchTelemetryConsent.isChecked)
        val wasOnboarded = isOnboardingDone(this)
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ONBOARDING_DONE, true)
            .apply()
        // This explicit call is what distinguishes a genuinely new install
        // from an upgrade whose versioned guidance store does not exist yet.
        // An already-onboarded install only re-accepts the new terms.
        if (!wasOnboarded) onboardingState.startForNewInstall()
        val next = if (!wasOnboarded && BuildConfig.ACCOUNT_UI_ENABLED) {
            AccountActivity.onboardingIntent(this)
        } else {
            Intent(this, MainActivity::class.java)
        }
        startActivity(next)
        finish()
    }

    companion object {
        const val PREFS_NAME = "plantdiseases_prefs"
        const val KEY_ONBOARDING_DONE = "onboarding_done"

        fun isOnboardingDone(context: Context): Boolean = context.getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getBoolean(KEY_ONBOARDING_DONE, false)
    }
}
