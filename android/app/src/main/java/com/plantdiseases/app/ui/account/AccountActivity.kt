package com.plantdiseases.app.ui.account

import android.content.res.ColorStateList
import android.content.Context
import android.content.DialogInterface
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.LogoutNotDurableException
import com.plantdiseases.app.auth.SignupAlreadyConfirmedException
import com.plantdiseases.app.auth.SyntheticProviderToken
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.account.AccountProfile
import com.plantdiseases.app.data.account.AccountSession
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.databinding.ActivityAccountBinding
import com.plantdiseases.app.ui.crop.CropContract
import com.plantdiseases.app.util.AccountDisclosurePrefs
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ProfileAvatarStore
import com.vk.id.AccessToken
import com.vk.id.VKID
import com.vk.id.VKIDAuthFail
import com.vk.id.auth.Prompt
import com.vk.id.auth.VKIDAuthCallback
import com.vk.id.auth.VKIDAuthParams
import com.yandex.authsdk.YandexAuthLoginOptions
import com.yandex.authsdk.YandexAuthOptions
import com.yandex.authsdk.YandexAuthResult
import com.yandex.authsdk.YandexAuthSdk
import java.io.File
import java.io.IOException
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.time.Duration
import java.time.Instant
import java.util.Date
import javax.inject.Inject

/** PlantDiseases 1.9 identity and profile surface. */
@AndroidEntryPoint
class AccountActivity : AppCompatActivity() {
    @Inject
    lateinit var authRepository: AuthRepository

    @Inject
    lateinit var installationId: InstallationIdProvider

    @Inject
    lateinit var accountDisclosurePrefs: AccountDisclosurePrefs

    @Inject
    lateinit var profileAvatarStore: ProfileAvatarStore

    private lateinit var binding: ActivityAccountBinding
    private var yandexSdk: YandexAuthSdk? = null
    private var oauthAction = OAuthAction.SIGN_IN
    private var linkTarget: AuthProvider? = null
    private var linkReauthToken: String? = null
    private var privateProfile: AccountProfile? = null
    private var displayedSessionId: String? = null
    private var loadingOperations = 0
    private var onboardingNavigationStarted = false
    private var pendingAvatarAccountId: String? = null
    private var pendingAvatarSourceFile: File? = null
    private var pendingAvatarOutputFile: File? = null
    private val onboardingEntry: Boolean
        get() = intent.getBooleanExtra(EXTRA_ONBOARDING_ENTRY, false)

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

    private val avatarPicker = registerForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        if (uri != null) openAvatarEditor(uri)
    }

    private val avatarCropLauncher = registerForActivityResult(CropContract()) { result ->
        completeAvatarEdit(result)
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
            if (intent.getBooleanExtra(EXTRA_ONBOARDING_ENTRY, false)) {
                startActivity(Intent(this, MainActivity::class.java))
            }
            finish()
            return
        }
        val restoredAction = savedInstanceState?.getString(STATE_OAUTH_ACTION)
            ?.let { stored -> OAuthAction.entries.firstOrNull { it.name == stored } }
            ?: OAuthAction.SIGN_IN
        val interruptedTargetExchange = restoredAction == OAuthAction.LINK_TARGET ||
            restoredAction == OAuthAction.DISCARD_LINK_RESULT
        oauthAction = if (interruptedTargetExchange) OAuthAction.DISCARD_LINK_RESULT else restoredAction
        linkTarget = if (!interruptedTargetExchange && restoredAction == OAuthAction.REAUTH_LINK) {
            savedInstanceState?.getString(STATE_LINK_TARGET)
                ?.let { stored -> AuthProvider.entries.firstOrNull { it.name == stored } }
        } else {
            null
        }
        binding = ActivityAccountBinding.inflate(layoutInflater)
        setContentView(binding.root)
        pendingAvatarAccountId = savedInstanceState?.getString(STATE_AVATAR_ACCOUNT_ID)
        pendingAvatarSourceFile = savedInstanceState?.getString(STATE_AVATAR_SOURCE)?.let(::File)
        pendingAvatarOutputFile = savedInstanceState?.getString(STATE_AVATAR_OUTPUT)?.let(::File)
        if (interruptedTargetExchange) {
            Toast.makeText(this, R.string.account_link_restart_required, Toast.LENGTH_LONG).show()
        }
        binding.tvSyntheticNotice.isVisible = BuildConfig.SYNTHETIC_AUTH_ENABLED
        binding.toolbar.isVisible = !onboardingEntry
        configureSignInProviders()
        binding.btnLinkYandex.isVisible = isProviderAvailable(AuthProvider.YANDEX)
        binding.btnLinkVk.isVisible = isProviderAvailable(AuthProvider.VK)
        binding.toolbar.setNavigationOnClickListener { handleBack() }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() = handleBack()
            },
        )

        binding.btnYandex.setOnClickListener { beginSignIn(AuthProvider.YANDEX) }
        binding.btnVk.setOnClickListener { beginSignIn(AuthProvider.VK) }
        binding.btnCompleteNickname.setOnClickListener { completeNickname() }
        binding.btnCancelNickname.setOnClickListener {
            lifecycleScope.launch {
                setLoading(true)
                try {
                    authRepository.cancelProfile()
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    showError(failure)
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
        binding.btnRetryAccount.setOnClickListener { retryAccountRestore() }
        binding.btnLater.setOnClickListener { continueAfterOnboarding() }
        binding.avatarPicker.setOnClickListener {
            if (privateProfile != null) {
                avatarPicker.launch(
                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                )
            }
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                authRepository.state.collect(::renderState)
            }
        }
    }

    private fun renderState(state: AuthState) {
        val guestVisible = state is AuthState.Guest ||
            (state is AuthState.Unavailable && state.lastAuthenticated == null)
        binding.guestGroup.isVisible = guestVisible
        binding.btnLater.isVisible = onboardingEntry && guestVisible
        binding.nicknameGroup.isVisible = state is AuthState.ProfileRequired
        binding.accountGroup.isVisible = state is AuthState.Authenticated
        binding.btnRetryAccount.isVisible = state is AuthState.Unavailable
        if (state is AuthState.ProfileRequired) {
            state.pendingNickname?.let { durableNickname ->
                if (binding.etSignupNickname.text?.toString() != durableNickname) {
                    binding.etSignupNickname.setText(durableNickname)
                }
            }
        }
        applySignupNicknameEditingPolicy(state)
        binding.tvState.text = when (state) {
            AuthState.Guest -> getString(R.string.account_guest)
            AuthState.Restoring -> getString(R.string.account_restoring)
            is AuthState.Unavailable -> getString(R.string.account_temporarily_unavailable)
            is AuthState.ProfileRequired -> getString(R.string.account_finish_profile)
            is AuthState.Authenticated -> state.profile.nickname
        }
        if (state is AuthState.Authenticated) {
            if (onboardingEntry) {
                if (loadingOperations == 0) continueAfterOnboarding()
                return
            }
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
        binding.tvProviderName.isVisible = false
        binding.tvLinkedProviders.text = ""
        binding.etNickname.text?.clear()
        binding.nicknameLayout.helperText = null
        binding.etNickname.isEnabled = false
        binding.sessionsContainer.removeAllViews()
        binding.btnLinkYandex.isEnabled = false
        binding.btnLinkVk.isEnabled = false
        binding.btnSaveNickname.isEnabled = false
        binding.btnDeleteAccount.isEnabled = false
        binding.avatarPicker.isEnabled = false
        showAvatarPlaceholder()
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
                com.plantdiseases.app.push.PushManager.registerIfEnabled(this@AccountActivity)
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
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
            binding.tvProviderName.isVisible = true
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
            binding.btnLinkYandex.isVisible =
                isProviderAvailable(AuthProvider.YANDEX) &&
                "yandex" !in linked
            binding.btnLinkVk.isVisible =
                isProviderAvailable(AuthProvider.VK) &&
                "vk" !in linked
            binding.btnLinkYandex.isEnabled = binding.btnLinkYandex.isVisible
            binding.btnLinkVk.isEnabled = binding.btnLinkVk.isVisible
            binding.btnDeleteAccount.isEnabled = true
            binding.avatarPicker.isEnabled = true
            applyProfileNicknameEditingPolicy()
            showProfileAvatar(profile.communityActorId, profile.primaryProvider.avatarUrl)
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
            val row = layoutInflater.inflate(
                R.layout.item_account_device,
                binding.sessionsContainer,
                false,
            )
            row.findViewById<TextView>(R.id.tv_device_title).setText(
                if (session.current) R.string.account_device_current else R.string.account_device_android,
            )
            row.findViewById<TextView>(R.id.tv_device_subtitle).text = if (session.current) {
                getString(R.string.account_device_active_now)
            } else {
                getString(
                    R.string.account_device_last_active,
                    formatSessionTimestamp(session.lastUsedAt),
                )
            }
            row.findViewById<View>(R.id.btn_device_logout).apply {
                isVisible = !session.current
                setOnClickListener { revokeSession(session.sessionId) }
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
        val profile = privateProfile ?: return
        if (!nicknameCanChange(profile, Instant.now())) return
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
        linkTarget = target
        chooseReauthProvider(OAuthAction.REAUTH_LINK)
    }

    private fun beginSignIn(provider: AuthProvider) {
        if (accountDisclosurePrefs.acknowledged) {
            launchOAuth(provider, OAuthAction.SIGN_IN)
            return
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.account_disclosure_title)
            .setMessage(R.string.account_disclosure_body)
            .setNegativeButton(android.R.string.cancel, null)
            .setNeutralButton(R.string.account_disclosure_privacy, null)
            .setPositiveButton(R.string.account_disclosure_acknowledge) { _, _ ->
                val acknowledgementSaved = try {
                    accountDisclosurePrefs.acknowledge()
                    true
                } catch (_: Exception) {
                    false
                }
                if (acknowledgementSaved) {
                    launchOAuth(provider, OAuthAction.SIGN_IN)
                } else {
                    Toast.makeText(this, R.string.account_disclosure_save_failed, Toast.LENGTH_LONG).show()
                }
            }
            .create()
        dialog.setOnShowListener {
            dialog.getButton(DialogInterface.BUTTON_NEUTRAL).setOnClickListener { openPrivacyPolicy() }
        }
        dialog.show()
    }

    private fun openPrivacyPolicy() {
        runCatching {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(getString(R.string.consent_privacy_url))))
        }.onFailure {
            Toast.makeText(this, R.string.account_privacy_open_failed, Toast.LENGTH_LONG).show()
        }
    }

    private fun confirmDeletion() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.account_delete)
            .setMessage(R.string.account_delete_warning)
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.account_delete_confirm) { _, _ ->
                chooseReauthProvider(OAuthAction.REAUTH_DELETE)
            }
            .show()
    }

    private fun chooseReauthProvider(action: OAuthAction) {
        val providers = privateProfile?.reauthProviders().orEmpty()
        if (providers.isEmpty()) {
            showError(null)
            return
        }
        if (providers.size == 1) {
            launchOAuth(providers.single(), action)
            return
        }
        val labels = providers.map(::providerLabel).toTypedArray()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.account_choose_reauth_provider)
            .setItems(labels) { _, index -> launchOAuth(providers[index], action) }
            .setNegativeButton(android.R.string.cancel) { _, _ -> resetOAuthFlow() }
            .setOnCancelListener { resetOAuthFlow() }
            .show()
    }

    private fun providerLabel(provider: AuthProvider): String = when (provider) {
        AuthProvider.YANDEX -> getString(R.string.account_provider_yandex)
        AuthProvider.VK -> getString(R.string.account_provider_vk)
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
            resetOAuthFlow()
            Toast.makeText(this, R.string.account_provider_unconfigured, Toast.LENGTH_LONG).show()
            return
        }
        when (provider) {
            AuthProvider.YANDEX -> {
                val sdk = getOrCreateYandexSdk() ?: return
                yandexLauncher.launch(sdk.contract.createIntent(this, YandexAuthLoginOptions()))
            }
            AuthProvider.VK -> {
                val requireFreshLogin = action.requiresFreshProviderLogin()
                VKID.instance.authorize(
                    lifecycleOwner = this,
                    callback = vkCallback,
                    params = VKIDAuthParams {
                        scopes = emptySet()
                        if (requireFreshLogin) {
                            useOAuthProviderIfPossible = false
                            prompt = Prompt.LOGIN
                        }
                    },
                )
            }
        }
    }

    private fun isProviderAvailable(provider: AuthProvider): Boolean =
        BuildConfig.SYNTHETIC_AUTH_ENABLED ||
            when (provider) {
                AuthProvider.YANDEX -> BuildConfig.YANDEX_OAUTH_CONFIGURED
                AuthProvider.VK -> BuildConfig.VK_OAUTH_CONFIGURED
            }

    private fun acceptProviderToken(provider: AuthProvider, token: String) {
        lifecycleScope.launch {
            setLoading(true)
            try {
                when (oauthAction) {
                    OAuthAction.SIGN_IN -> try {
                        authRepository.signIn(provider, token)
                        com.plantdiseases.app.push.PushManager.registerIfEnabled(this@AccountActivity)
                        resetOAuthFlow()
                    } catch (failure: CancellationException) {
                        throw failure
                    } catch (failure: Exception) {
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
                        val deletedAccountId = privateProfile?.communityActorId
                        authRepository.deleteAccount(reauth.reauthToken)
                            .onSuccess {
                                resetOAuthFlow()
                                stopAccountNotifications()
                                if (deletedAccountId != null) {
                                    profileAvatarStore.clear(deletedAccountId)
                                }
                            }
                            .onFailure(::showError)
                    }.onFailure(::showError)
                    OAuthAction.DISCARD_LINK_RESULT -> resetOAuthFlow()
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

    private fun formatSessionTimestamp(timestamp: String): String {
        val instant = parseAccountTimestamp(timestamp)
            ?: return getString(R.string.account_session_time_unknown)
        return DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT)
            .format(Date.from(instant))
    }

    private fun applyProfileNicknameEditingPolicy() {
        val profile = privateProfile
        if (profile == null) {
            binding.nicknameLayout.helperText = null
            binding.etNickname.isEnabled = false
            binding.btnSaveNickname.isEnabled = false
            return
        }
        val availableAt = nicknameChangeAvailableAt(profile)
        val cooldownActive = !nicknameCanChange(profile, Instant.now())
        val canChange = !cooldownActive && loadingOperations == 0
        binding.nicknameLayout.helperText = if (availableAt != null && cooldownActive) {
            getString(
                R.string.account_nickname_available_on,
                DateFormat.getDateInstance(DateFormat.MEDIUM).format(Date.from(availableAt)),
            )
        } else {
            getString(R.string.account_nickname_every_30_days)
        }
        binding.etNickname.isEnabled = canChange
        binding.btnSaveNickname.isEnabled = canChange
    }

    private fun showAvatarPlaceholder() {
        Glide.with(this).clear(binding.ivAvatar)
        val padding = (36 * resources.displayMetrics.density).toInt()
        binding.ivAvatar.setPadding(padding, padding, padding, padding)
        binding.ivAvatar.imageTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, R.color.on_hero),
        )
        binding.ivAvatar.setImageResource(R.drawable.ic_profile)
    }

    private fun showProfileAvatar(accountId: String, avatarUrl: String?) {
        val source: Any = profileAvatarStore.current(accountId)
            ?: avatarUrl?.takeIf { it.startsWith("https://") }
            ?: return showAvatarPlaceholder()
        Glide.with(this).clear(binding.ivAvatar)
        binding.ivAvatar.setPadding(0, 0, 0, 0)
        binding.ivAvatar.imageTintList = null
        Glide.with(this)
            .load(source)
            .diskCacheStrategy(DiskCacheStrategy.NONE)
            .skipMemoryCache(true)
            .circleCrop()
            .error(R.drawable.ic_profile)
            .into(binding.ivAvatar)
    }

    private fun openAvatarEditor(uri: Uri) {
        val accountId = currentAvatarAccountId() ?: return
        lifecycleScope.launch {
            setLoading(true)
            var stagedSource: File? = null
            var editorOutput: File? = null
            try {
                val safeSource = profileAvatarStore.stageForEditing(uri).getOrElse {
                    showAvatarError()
                    return@launch
                }
                stagedSource = safeSource
                if (currentAvatarAccountId() != accountId) return@launch
                val cropOutput = File.createTempFile("profile_avatar_crop_", ".jpg", cacheDir)
                editorOutput = cropOutput
                val sourceUri = FileProvider.getUriForFile(
                    this@AccountActivity,
                    "$packageName.fileprovider",
                    safeSource,
                )
                val outputUri = FileProvider.getUriForFile(
                    this@AccountActivity,
                    "$packageName.fileprovider",
                    cropOutput,
                )
                pendingAvatarAccountId = accountId
                pendingAvatarSourceFile = safeSource
                pendingAvatarOutputFile = cropOutput
                avatarCropLauncher.launch(
                    CropContract.Input(
                        source = sourceUri,
                        output = outputUri,
                        mode = CropContract.Mode.AVATAR,
                    ),
                )
                stagedSource = null
                editorOutput = null
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                pendingAvatarAccountId = null
                pendingAvatarSourceFile = null
                pendingAvatarOutputFile = null
                showAvatarError()
            } finally {
                stagedSource?.delete()
                editorOutput?.delete()
                setLoading(false)
            }
        }
    }

    private fun completeAvatarEdit(result: CropContract.Result) {
        val accountId = pendingAvatarAccountId
        val sourceFile = pendingAvatarSourceFile
        val outputFile = pendingAvatarOutputFile
        pendingAvatarAccountId = null
        pendingAvatarSourceFile = null
        pendingAvatarOutputFile = null
        sourceFile?.delete()

        val editedUri = result.output ?: outputFile
            ?.takeIf { it.isFile && it.length() > 0L }
            ?.let(Uri::fromFile)
        if (!result.isSuccessful || accountId == null || editedUri == null) {
            deleteAvatarEditorOutput(outputFile, result.output)
            return
        }
        if (currentAvatarAccountId() != accountId) {
            deleteAvatarEditorOutput(outputFile, editedUri)
            return
        }

        lifecycleScope.launch {
            setLoading(true)
            try {
                profileAvatarStore.replace(accountId, editedUri)
                    .onSuccess {
                        val current = privateProfile
                        if (currentAvatarAccountId() == accountId) {
                            showProfileAvatar(accountId, current?.primaryProvider?.avatarUrl)
                        }
                    }
                    .onFailure { showAvatarError() }
            } finally {
                deleteAvatarEditorOutput(outputFile, editedUri)
                setLoading(false)
            }
        }
    }

    private fun currentAvatarAccountId(): String? = when (val state = authRepository.state.value) {
        is AuthState.Authenticated -> state.profile.communityActorId
        is AuthState.Unavailable -> state.lastAuthenticated?.profile?.communityActorId
        else -> null
    }

    private fun deleteAvatarEditorOutput(file: File?, uri: Uri?) {
        if (file?.delete() != true && uri != null) {
            runCatching { contentResolver.delete(uri, null, null) }
        }
    }

    private fun showAvatarError() {
        Toast.makeText(this, R.string.account_avatar_update_failed, Toast.LENGTH_LONG).show()
    }

    private fun logout() {
        lifecycleScope.launch {
            setLoading(true)
            try {
                authRepository.logout()
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
                showError(failure)
            } finally {
                stopAccountNotifications()
                setLoading(false)
            }
        }
    }

    private fun retryAccountRestore() {
        lifecycleScope.launch {
            setLoading(true)
            try {
                authRepository.restore()
            } finally {
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
                    if (authRepository.state.value is AuthState.Guest) {
                        if (onboardingEntry) continueAfterOnboarding() else finish()
                    }
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    showError(failure)
                } finally {
                    setLoading(false)
                }
            }
        } else {
            if (onboardingEntry) continueAfterOnboarding() else finish()
        }
    }

    private fun continueAfterOnboarding() {
        if (!onboardingEntry || onboardingNavigationStarted) return
        onboardingNavigationStarted = true
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private fun setLoading(loading: Boolean) {
        loadingOperations = (loadingOperations + if (loading) 1 else -1).coerceAtLeast(0)
        val active = loadingOperations > 0
        binding.progress.isVisible = active
        setEnabledRecursively(binding.content, !active)
        if (!active) {
            // Recursive re-enabling must never turn an unavailable provider
            // into a clickable control. VK remains a visible, disabled roadmap
            // item in the Yandex-only 1.9 release.
            configureSignInProviders()
            if (privateProfile == null) {
                binding.btnLinkYandex.isEnabled = false
                binding.btnLinkVk.isEnabled = false
                binding.btnSaveNickname.isEnabled = false
                binding.btnDeleteAccount.isEnabled = false
                binding.avatarPicker.isEnabled = false
            } else {
                binding.avatarPicker.isEnabled = true
                applyProfileNicknameEditingPolicy()
            }
        }
        applySignupNicknameEditingPolicy(authRepository.state.value)
        if (
            !active &&
            onboardingEntry &&
            authRepository.state.value is AuthState.Authenticated
        ) {
            continueAfterOnboarding()
        }
    }

    private fun applySignupNicknameEditingPolicy(state: AuthState) {
        binding.etSignupNickname.isEnabled = signupNicknameIsEditable(
            state = state,
            loading = loadingOperations > 0,
        )
    }

    private fun configureSignInProviders() {
        val yandexAvailable = isProviderAvailable(AuthProvider.YANDEX)
        val vkAvailable = isProviderAvailable(AuthProvider.VK)
        binding.btnYandex.isVisible = yandexAvailable
        binding.btnYandex.isEnabled = yandexAvailable
        binding.btnVk.isVisible = true
        binding.btnVk.isEnabled = vkAvailable
        binding.btnVk.setText(
            if (vkAvailable) R.string.account_continue_vk else R.string.account_vk_coming_soon,
        )
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
        pendingAvatarAccountId?.let { outState.putString(STATE_AVATAR_ACCOUNT_ID, it) }
        pendingAvatarSourceFile?.let { outState.putString(STATE_AVATAR_SOURCE, it.absolutePath) }
        pendingAvatarOutputFile?.let { outState.putString(STATE_AVATAR_OUTPUT, it.absolutePath) }
        super.onSaveInstanceState(outState)
    }

    private fun showError(failure: Throwable?) {
        val message = when (failure) {
            is LogoutNotDurableException -> R.string.account_error_logout_not_durable
            is SignupAlreadyConfirmedException -> R.string.account_signup_already_confirmed
            is IOException -> R.string.account_error_retry_online
            else -> when ((failure as? AccountApiException)?.errorCode) {
                "nickname_invalid" -> R.string.account_error_nickname_invalid
                "nickname_taken" -> R.string.account_error_nickname_taken
                "nickname_cooldown" -> R.string.account_error_nickname_cooldown
                "provider_unavailable" -> R.string.account_error_provider_unavailable
                "identity_already_linked" -> R.string.account_error_identity_linked
                else -> R.string.account_error_generic
            }
        }
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
    }

    private enum class OAuthAction {
        SIGN_IN,
        REAUTH_LINK,
        LINK_TARGET,
        REAUTH_DELETE,
        DISCARD_LINK_RESULT,
        ;

        fun requiresFreshProviderLogin(): Boolean = this != SIGN_IN
    }

    companion object {
        private const val EXTRA_ONBOARDING_ENTRY = "account.onboarding_entry"
        private const val STATE_OAUTH_ACTION = "account.oauth.action"
        private const val STATE_LINK_TARGET = "account.oauth.link_target"
        private const val STATE_AVATAR_ACCOUNT_ID = "account.avatar.account_id"
        private const val STATE_AVATAR_SOURCE = "account.avatar.source"
        private const val STATE_AVATAR_OUTPUT = "account.avatar.output"

        fun intent(context: Context): Intent = Intent(context, AccountActivity::class.java)

        fun onboardingIntent(context: Context): Intent = intent(context).putExtra(
            EXTRA_ONBOARDING_ENTRY,
            true,
        )
    }
}

internal fun AccountProfile.reauthProviders(): List<AuthProvider> = buildList {
    val providerNames = listOf(primaryProvider.provider) + linkedProviders.map { it.provider }
    providerNames.forEach { providerName ->
        AuthProvider.entries
            .firstOrNull { it.wireName == providerName }
            ?.takeIf { it !in this }
            ?.let(::add)
    }
}

internal fun parseAccountTimestamp(timestamp: String): Instant? =
    runCatching { Instant.parse(timestamp) }.getOrNull()

internal fun nicknameChangeAvailableAt(profile: AccountProfile): Instant? =
    profile.nicknameChangedAt
        ?.let(::parseAccountTimestamp)
        ?.plus(Duration.ofDays(NICKNAME_CHANGE_INTERVAL_DAYS))

internal fun nicknameCanChange(profile: AccountProfile, now: Instant): Boolean =
    nicknameChangeAvailableAt(profile)?.let { availableAt -> !now.isBefore(availableAt) } ?: true

internal fun signupNicknameIsEditable(
    state: AuthState,
    loading: Boolean,
): Boolean = !loading &&
    (state !is AuthState.ProfileRequired || state.pendingNickname == null)

private const val NICKNAME_CHANGE_INTERVAL_DAYS = 30L
