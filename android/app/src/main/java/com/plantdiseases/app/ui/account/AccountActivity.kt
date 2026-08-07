package com.plantdiseases.app.ui.account

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.bumptech.glide.Glide
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.SyntheticProviderToken
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.account.AccountProfile
import com.plantdiseases.app.data.account.AccountSession
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.databinding.ActivityAccountBinding
import com.plantdiseases.app.util.LocaleHelper
import com.vk.id.AccessToken
import com.vk.id.VKID
import com.vk.id.VKIDAuthFail
import com.vk.id.auth.VKIDAuthCallback
import com.vk.id.auth.VKIDAuthParams
import com.yandex.authsdk.YandexAuthLoginOptions
import com.yandex.authsdk.YandexAuthOptions
import com.yandex.authsdk.YandexAuthResult
import com.yandex.authsdk.YandexAuthSdk
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import javax.inject.Inject

/** Account-beta identity/profile surface. Production builds never expose its entry point. */
@AndroidEntryPoint
class AccountActivity : AppCompatActivity() {
    @Inject
    lateinit var authRepository: AuthRepository

    @Inject
    lateinit var installationId: InstallationIdProvider

    private lateinit var binding: ActivityAccountBinding
    private var yandexSdk: YandexAuthSdk? = null
    private var oauthAction = OAuthAction.SIGN_IN
    private var linkTarget: AuthProvider? = null
    private var linkReauthToken: String? = null
    private var privateProfile: AccountProfile? = null
    private var displayedSessionId: String? = null
    private var loadingOperations = 0

    private val yandexLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { activityResult ->
        val sdk = getOrCreateYandexSdk() ?: return@registerForActivityResult
        when (val result = sdk.contract.parseResult(activityResult.resultCode, activityResult.data)) {
            is YandexAuthResult.Success -> acceptProviderToken(AuthProvider.YANDEX, result.token.value)
            is YandexAuthResult.Failure -> providerFailed()
            YandexAuthResult.Cancelled -> providerCancelled()
        }
    }

    private val vkCallback = object : VKIDAuthCallback {
        override fun onAuth(accessToken: AccessToken) {
            acceptProviderToken(AuthProvider.VK, accessToken.token)
        }

        override fun onFail(fail: VKIDAuthFail) {
            if (fail is VKIDAuthFail.Canceled) providerCancelled() else providerFailed()
        }
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!BuildConfig.ACCOUNT_UI_ENABLED) {
            finish()
            return
        }
        oauthAction = savedInstanceState?.getString(STATE_OAUTH_ACTION)
            ?.let { stored -> OAuthAction.entries.firstOrNull { it.name == stored } }
            ?: OAuthAction.SIGN_IN
        linkTarget = savedInstanceState?.getString(STATE_LINK_TARGET)
            ?.let { stored -> AuthProvider.entries.firstOrNull { it.name == stored } }
        binding = ActivityAccountBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.tvSyntheticNotice.isVisible = BuildConfig.SYNTHETIC_AUTH_ENABLED
        binding.toolbar.setNavigationOnClickListener { handleBack() }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() = handleBack()
            },
        )

        binding.btnYandex.setOnClickListener { launchOAuth(AuthProvider.YANDEX, OAuthAction.SIGN_IN) }
        binding.btnVk.setOnClickListener { launchOAuth(AuthProvider.VK, OAuthAction.SIGN_IN) }
        binding.btnCompleteNickname.setOnClickListener { completeNickname() }
        binding.btnCancelNickname.setOnClickListener {
            lifecycleScope.launch {
                setLoading(true)
                try {
                    authRepository.cancelProfile()
                } catch (failure: Exception) {
                    if (failure is CancellationException) throw failure
                } finally {
                    setLoading(false)
                }
            }
        }
        binding.btnSaveNickname.setOnClickListener { changeNickname() }
        binding.btnLinkYandex.setOnClickListener { beginLink(AuthProvider.YANDEX) }
        binding.btnLinkVk.setOnClickListener { beginLink(AuthProvider.VK) }
        binding.btnLogout.setOnClickListener { logout() }
        binding.btnDeleteAccount.setOnClickListener { confirmDeletion() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                authRepository.state.collect(::renderState)
            }
        }
    }

    private fun renderState(state: AuthState) {
        binding.guestGroup.isVisible = state is AuthState.Guest
        binding.nicknameGroup.isVisible = state is AuthState.ProfileRequired
        binding.accountGroup.isVisible = state is AuthState.Authenticated
        binding.tvState.text = when (state) {
            AuthState.Guest -> getString(R.string.account_guest)
            is AuthState.ProfileRequired -> getString(R.string.account_finish_profile)
            is AuthState.Authenticated -> state.profile.nickname
        }
        if (state is AuthState.Authenticated) {
            if (displayedSessionId != state.session.sessionId) {
                clearPrivateProfileUi()
                displayedSessionId = state.session.sessionId
            }
            loadPrivateProfile(state.session.sessionId)
        } else {
            displayedSessionId = null
            clearPrivateProfileUi()
        }
    }

    private fun clearPrivateProfileUi() {
        privateProfile = null
        binding.tvProviderName.text = ""
        binding.tvLinkedProviders.text = ""
        binding.etNickname.text?.clear()
        binding.sessionsContainer.removeAllViews()
        binding.btnLinkYandex.isEnabled = false
        binding.btnLinkVk.isEnabled = false
        binding.btnSaveNickname.isEnabled = false
        binding.btnDeleteAccount.isEnabled = false
        Glide.with(this).clear(binding.ivAvatar)
        binding.ivAvatar.setImageResource(R.drawable.ic_profile)
    }

    private fun completeNickname() {
        val nickname = binding.etSignupNickname.text?.toString()?.trim().orEmpty()
        if (nickname.isBlank()) {
            binding.signupNicknameLayout.error = getString(R.string.account_nickname_required)
            return
        }
        binding.signupNicknameLayout.error = null
        lifecycleScope.launch {
            setLoading(true)
            try {
                authRepository.completeProfile(nickname)
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                showError(failure)
            } finally {
                setLoading(false)
            }
        }
    }

    private fun loadPrivateProfile(expectedSessionId: String? = currentSessionId()) {
        if (expectedSessionId == null) return
        lifecycleScope.launch {
            val profile = authRepository.me().getOrElse {
                if (currentSessionId() == expectedSessionId) showError(it)
                return@launch
            }
            if (currentSessionId() != expectedSessionId) return@launch
            privateProfile = profile
            val providerLabel = when (profile.primaryProvider.provider) {
                AuthProvider.YANDEX.wireName -> getString(R.string.account_provider_yandex)
                AuthProvider.VK.wireName -> getString(R.string.account_provider_vk)
                else -> getString(R.string.account_provider_unknown)
            }
            binding.tvProviderName.text = getString(
                R.string.account_provider_name,
                providerLabel,
                profile.primaryProvider.displayName,
            )
            binding.etNickname.setText(profile.nickname)
            val linked = profile.linkedProviders.map { it.provider }.toSet() +
                profile.primaryProvider.provider
            binding.tvLinkedProviders.text = linked
                .sorted()
                .joinToString(", ") { provider ->
                    when (provider) {
                        AuthProvider.YANDEX.wireName -> getString(R.string.account_provider_yandex)
                        AuthProvider.VK.wireName -> getString(R.string.account_provider_vk)
                        else -> provider
                    }
                }
            binding.btnLinkYandex.isVisible = "yandex" !in linked
            binding.btnLinkVk.isVisible = "vk" !in linked
            binding.btnLinkYandex.isEnabled = true
            binding.btnLinkVk.isEnabled = true
            binding.btnSaveNickname.isEnabled = true
            binding.btnDeleteAccount.isEnabled = true
            Glide.with(this@AccountActivity).clear(binding.ivAvatar)
            binding.ivAvatar.setImageResource(R.drawable.ic_profile)
            profile.primaryProvider.avatarUrl?.takeIf { it.startsWith("https://") }?.let { url ->
                Glide.with(this@AccountActivity).load(url).circleCrop().into(binding.ivAvatar)
            }
            authRepository.sessions()
                .onSuccess { sessions ->
                    if (currentSessionId() == expectedSessionId) renderSessions(sessions)
                }
                .onFailure { failure ->
                    if (currentSessionId() == expectedSessionId) showError(failure)
                }
        }
    }

    private fun renderSessions(sessions: List<AccountSession>) {
        binding.sessionsContainer.removeAllViews()
        sessions.forEach { session ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
            }
            row.addView(
                TextView(this).apply {
                    text = if (session.current) {
                        getString(R.string.account_current_session, AccountSession.GENERIC_DEVICE_LABEL)
                    } else {
                        AccountSession.GENERIC_DEVICE_LABEL
                    }
                    layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                },
            )
            if (!session.current) {
                row.addView(
                    Button(this).apply {
                        text = getString(R.string.account_revoke_session)
                        setOnClickListener { revokeSession(session.sessionId) }
                    },
                )
            }
            binding.sessionsContainer.addView(row)
        }
    }

    private fun revokeSession(sessionId: String) {
        lifecycleScope.launch {
            setLoading(true)
            try {
                authRepository.revokeSession(sessionId)
                    .onSuccess { loadPrivateProfile() }
                    .onFailure(::showError)
            } finally {
                setLoading(false)
            }
        }
    }

    private fun changeNickname() {
        val nickname = binding.etNickname.text?.toString()?.trim().orEmpty()
        if (nickname.isBlank()) return
        lifecycleScope.launch {
            setLoading(true)
            try {
                authRepository.changeNickname(nickname)
                    .onSuccess { loadPrivateProfile() }
                    .onFailure(::showError)
            } finally {
                setLoading(false)
            }
        }
    }

    private fun beginLink(target: AuthProvider) {
        val primary = privateProfile?.primaryProvider?.provider.toProvider() ?: return
        linkTarget = target
        launchOAuth(primary, OAuthAction.REAUTH_LINK)
    }

    private fun confirmDeletion() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.account_delete)
            .setMessage(R.string.account_delete_warning)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.account_delete_confirm) { _, _ ->
                val primary = privateProfile?.primaryProvider?.provider.toProvider()
                    ?: return@setPositiveButton
                launchOAuth(primary, OAuthAction.REAUTH_DELETE)
            }
            .show()
    }

    private fun launchOAuth(provider: AuthProvider, action: OAuthAction) {
        oauthAction = action
        if (BuildConfig.SYNTHETIC_AUTH_ENABLED) {
            acceptProviderToken(
                provider,
                SyntheticProviderToken.forInstallation(provider, installationId.get()),
            )
            return
        }
        val configured = when (provider) {
            AuthProvider.YANDEX -> BuildConfig.YANDEX_OAUTH_CONFIGURED
            AuthProvider.VK -> BuildConfig.VK_OAUTH_CONFIGURED
        }
        if (!configured) {
            Toast.makeText(this, R.string.account_provider_unconfigured, Toast.LENGTH_LONG).show()
            return
        }
        when (provider) {
            AuthProvider.YANDEX -> {
                val sdk = getOrCreateYandexSdk() ?: return
                yandexLauncher.launch(sdk.contract.createIntent(this, YandexAuthLoginOptions()))
            }
            AuthProvider.VK -> VKID.instance.authorize(
                lifecycleOwner = this,
                callback = vkCallback,
                params = VKIDAuthParams { scopes = emptySet() },
            )
        }
    }

    private fun acceptProviderToken(provider: AuthProvider, token: String) {
        lifecycleScope.launch {
            setLoading(true)
            try {
                when (oauthAction) {
                    OAuthAction.SIGN_IN -> try {
                        authRepository.signIn(provider, token)
                        resetOAuthFlow()
                    } catch (failure: Exception) {
                        if (failure is CancellationException) throw failure
                        showError(failure)
                    }
                    OAuthAction.REAUTH_LINK -> {
                        val target = linkTarget ?: return@launch resetBrokenOAuthFlow()
                        authRepository.reauthenticate(
                            provider,
                            token,
                            "link_${target.wireName}",
                        ).onSuccess { reauth ->
                            linkReauthToken = reauth.reauthToken
                            launchOAuth(target, OAuthAction.LINK_TARGET)
                        }.onFailure(::showError)
                    }
                    OAuthAction.LINK_TARGET -> {
                        val reauth = linkReauthToken ?: return@launch resetBrokenOAuthFlow()
                        authRepository.linkProvider(provider, token, reauth)
                            .onSuccess {
                                resetOAuthFlow()
                                loadPrivateProfile()
                            }
                            .onFailure(::showError)
                        linkReauthToken = null
                    }
                    OAuthAction.REAUTH_DELETE -> authRepository.reauthenticate(
                        provider,
                        token,
                        "deletion",
                    ).onSuccess { reauth ->
                        authRepository.deleteAccount(reauth.reauthToken)
                            .onSuccess {
                                resetOAuthFlow()
                                stopAccountNotifications()
                            }
                            .onFailure(::showError)
                    }.onFailure(::showError)
                }
            } finally {
                setLoading(false)
            }
        }
    }

    private fun providerCancelled() {
        resetOAuthFlow()
        Toast.makeText(this, R.string.account_oauth_cancelled, Toast.LENGTH_SHORT).show()
    }

    private fun providerFailed() {
        resetOAuthFlow()
        showError(null)
    }

    private fun resetBrokenOAuthFlow() {
        resetOAuthFlow()
        showError(null)
    }

    private fun resetOAuthFlow() {
        linkTarget = null
        linkReauthToken = null
        oauthAction = OAuthAction.SIGN_IN
    }

    private fun getOrCreateYandexSdk(): YandexAuthSdk? {
        if (!BuildConfig.YANDEX_OAUTH_CONFIGURED || BuildConfig.SYNTHETIC_AUTH_ENABLED) return null
        return yandexSdk ?: YandexAuthSdk.create(
            YandexAuthOptions(this, loggingEnabled = false),
        ).also { yandexSdk = it }
    }

    private fun currentSessionId(): String? =
        (authRepository.state.value as? AuthState.Authenticated)?.session?.sessionId

    private fun logout() {
        lifecycleScope.launch {
            setLoading(true)
            try {
                authRepository.logout()
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
            } finally {
                stopAccountNotifications()
                setLoading(false)
            }
        }
    }

    private fun stopAccountNotifications() {
        com.plantdiseases.app.util.OpinionPrefs.setNotificationsEnabled(this, false)
        androidx.work.WorkManager.getInstance(this).apply {
            cancelUniqueWork("opinion_check")
            cancelUniqueWork("opinion_check_now")
        }
    }

    private fun handleBack() {
        if (authRepository.state.value is AuthState.ProfileRequired) {
            lifecycleScope.launch {
                setLoading(true)
                try {
                    authRepository.cancelProfile()
                } catch (failure: Exception) {
                    if (failure is CancellationException) throw failure
                } finally {
                    setLoading(false)
                }
            }
        } else {
            finish()
        }
    }

    private fun setLoading(loading: Boolean) {
        loadingOperations = (loadingOperations + if (loading) 1 else -1).coerceAtLeast(0)
        val active = loadingOperations > 0
        binding.progress.isVisible = active
        setEnabledRecursively(binding.content, !active)
        if (!active && privateProfile == null) {
            binding.btnLinkYandex.isEnabled = false
            binding.btnLinkVk.isEnabled = false
            binding.btnSaveNickname.isEnabled = false
            binding.btnDeleteAccount.isEnabled = false
        }
    }

    private fun setEnabledRecursively(view: android.view.View, enabled: Boolean) {
        view.isEnabled = enabled
        if (view is android.view.ViewGroup) {
            for (index in 0 until view.childCount) {
                setEnabledRecursively(view.getChildAt(index), enabled)
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString(STATE_OAUTH_ACTION, oauthAction.name)
        linkTarget?.let { outState.putString(STATE_LINK_TARGET, it.name) }
        super.onSaveInstanceState(outState)
    }

    private fun showError(failure: Throwable?) {
        val message = when ((failure as? AccountApiException)?.errorCode) {
            "nickname_taken" -> R.string.account_error_nickname_taken
            "nickname_cooldown" -> R.string.account_error_nickname_cooldown
            "provider_unavailable" -> R.string.account_error_provider_unavailable
            "identity_already_linked" -> R.string.account_error_identity_linked
            else -> R.string.account_error_generic
        }
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    private fun String?.toProvider(): AuthProvider? =
        AuthProvider.entries.firstOrNull { it.wireName == this }

    private enum class OAuthAction {
        SIGN_IN,
        REAUTH_LINK,
        LINK_TARGET,
        REAUTH_DELETE,
    }

    companion object {
        private const val STATE_OAUTH_ACTION = "account.oauth.action"
        private const val STATE_LINK_TARGET = "account.oauth.link_target"

        fun intent(context: Context): Intent = Intent(context, AccountActivity::class.java)
    }
}
