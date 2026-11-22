package com.plantdiseases.app.ui.account

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultCaller
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.core.view.isVisible
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.SyntheticProviderToken
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.di.appEntryPoint
import com.plantdiseases.app.push.PushManager
import com.yandex.authsdk.YandexAuthLoginOptions
import com.yandex.authsdk.YandexAuthOptions
import com.yandex.authsdk.YandexAuthResult
import com.yandex.authsdk.YandexAuthSdk
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * Sign-in in place, for a guest who tries to take part in the community
 * (like, answer, post, ask for an opinion). Instead of a toast and the account
 * screen, a sheet explains what an account adds and signs in with Yandex ID
 * right there; a new account picks its nickname in the same sheet. The
 * account disclosure is still acknowledged once before the first exchange.
 *
 * Build it while the host is being created (a property initializer): it
 * registers the Yandex ID result launcher. [onSignedIn] runs once the user is
 * signed in, so the host can drop its guest styling.
 */
class QuickSignIn private constructor(
    caller: ActivityResultCaller,
    private val hostContext: () -> Context,
    private val hostScope: () -> CoroutineScope,
    private val onSignedIn: () -> Unit,
) {
    private var yandexSdk: YandexAuthSdk? = null
    private var sheet: BottomSheetDialog? = null
    private var views: SheetViews? = null
    private var nicknameStep = false

    private val yandexLauncher = caller.registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val sdk = yandexSdk() ?: return@registerForActivityResult setBusy(false)
        when (val parsed = sdk.contract.parseResult(result.resultCode, result.data)) {
            is YandexAuthResult.Success -> exchange(parsed.token.value)
            is YandexAuthResult.Failure -> showError(null)
            YandexAuthResult.Cancelled -> setBusy(false)
        }
    }

    private val context: Context get() = hostContext()
    private val auth get() = appEntryPoint(context).authRepository()

    val isSignedIn: Boolean get() = auth.state.value is AuthState.Authenticated

    /** True when the user is signed in; otherwise offers sign-in here and is false. */
    fun require(): Boolean {
        if (isSignedIn) return true
        show()
        return false
    }

    fun show() {
        if (!BuildConfig.ACCOUNT_UI_ENABLED) {
            // A build without accounts: nothing to sign in to.
            Toast.makeText(context, R.string.quick_sign_in_title, Toast.LENGTH_SHORT).show()
            return
        }
        val v = openSheet()
        if (auth.state.value is AuthState.ProfileRequired) showNicknameStep(v)
    }

    // ── sheet ─────────────────────────────────────────────────────────────

    private class SheetViews(root: View) {
        val title: TextView = root.findViewById(R.id.tv_quick_title)
        val benefits: LinearLayout = root.findViewById(R.id.ll_quick_benefits)
        val nicknameGroup: View = root.findViewById(R.id.quick_nickname_group)
        val nicknameLayout: TextInputLayout = root.findViewById(R.id.til_quick_nickname)
        val nickname: TextInputEditText = root.findViewById(R.id.et_quick_nickname)
        val error: TextView = root.findViewById(R.id.tv_quick_error)
        val progress: LinearProgressIndicator = root.findViewById(R.id.quick_progress)
        val yandex: MaterialButton = root.findViewById(R.id.btn_quick_yandex)
        val other: MaterialButton = root.findViewById(R.id.btn_quick_other)
        val create: MaterialButton = root.findViewById(R.id.btn_quick_create)
        val later: MaterialButton = root.findViewById(R.id.btn_quick_later)
    }

    private fun openSheet(): SheetViews {
        val current = views
        if (current != null && sheet?.isShowing == true) return current
        val ctx = context
        val root = LayoutInflater.from(ctx).inflate(R.layout.sheet_quick_sign_in, null)
        val v = SheetViews(root)
        BENEFITS.forEach { (icon, text) ->
            val row = LayoutInflater.from(ctx).inflate(R.layout.item_quick_benefit, v.benefits, false)
            row.findViewById<ImageView>(R.id.iv_benefit_icon).setImageResource(icon)
            row.findViewById<TextView>(R.id.tv_benefit).setText(text)
            v.benefits.addView(row)
        }
        val yandexAvailable = BuildConfig.SYNTHETIC_AUTH_ENABLED || BuildConfig.YANDEX_OAUTH_CONFIGURED
        v.yandex.isVisible = yandexAvailable
        v.other.isVisible = !yandexAvailable
        v.yandex.setOnClickListener { startYandex() }
        v.other.setOnClickListener {
            sheet?.dismiss()
            ctx.startActivity(AccountActivity.intent(ctx))
        }
        v.create.setOnClickListener { completeNickname() }
        v.later.setOnClickListener { later() }
        nicknameStep = false

        val dialog = BottomSheetDialog(ctx)
        dialog.setContentView(root)
        dialog.behavior.state = BottomSheetBehavior.STATE_EXPANDED
        dialog.behavior.skipCollapsed = true
        dialog.setOnDismissListener {
            if (sheet === dialog) {
                sheet = null
                views = null
            }
        }
        sheet = dialog
        views = v
        dialog.show()
        return v
    }

    private fun showNicknameStep(v: SheetViews) {
        nicknameStep = true
        v.title.setText(R.string.account_finish_profile)
        v.benefits.isVisible = false
        v.nicknameGroup.isVisible = true
        v.yandex.isVisible = false
        v.other.isVisible = false
        v.create.isVisible = true
        v.later.setText(R.string.account_cancel_signup)
        val state = auth.state.value
        (state as? AuthState.ProfileRequired)?.pendingNickname?.let { durable ->
            if (v.nickname.text?.toString() != durable) v.nickname.setText(durable)
        }
        v.nickname.isEnabled = signupNicknameIsEditable(state, loading = false)
    }

    private fun later() {
        if (!nicknameStep) {
            sheet?.dismiss()
            return
        }
        // Leaving the nickname step abandons the new account: stay a guest.
        setBusy(true)
        hostScope().launch {
            try {
                auth.cancelProfile()
                sheet?.dismiss()
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
                showError(failure)
            } finally {
                setBusy(false)
            }
        }
    }

    private fun setBusy(busy: Boolean) {
        val v = views ?: return
        v.progress.isVisible = busy
        v.yandex.isEnabled = !busy
        v.other.isEnabled = !busy
        v.create.isEnabled = !busy
        v.later.isEnabled = !busy
        if (nicknameStep) v.nickname.isEnabled = signupNicknameIsEditable(auth.state.value, busy)
        sheet?.setCancelable(!busy)
        if (busy) v.error.isVisible = false
    }

    private fun showError(failure: Throwable?) {
        setBusy(false)
        val message = accountErrorMessage(failure)
        val v = views
        if (v == null || sheet?.isShowing != true) {
            Toast.makeText(context, message, Toast.LENGTH_LONG).show()
            return
        }
        v.error.setText(message)
        v.error.isVisible = true
    }

    // ── sign-in ───────────────────────────────────────────────────────────

    private fun startYandex() {
        val ctx = context
        withAccountDisclosure(ctx, appEntryPoint(ctx).accountDisclosurePrefs()) {
            if (BuildConfig.SYNTHETIC_AUTH_ENABLED) {
                val installation = appEntryPoint(ctx).installationIdProvider().get()
                exchange(SyntheticProviderToken.forInstallation(AuthProvider.YANDEX, installation))
                return@withAccountDisclosure
            }
            val sdk = yandexSdk()
            if (sdk == null) {
                Toast.makeText(ctx, R.string.account_provider_unconfigured, Toast.LENGTH_LONG).show()
                return@withAccountDisclosure
            }
            setBusy(true)
            yandexLauncher.launch(sdk.contract.createIntent(ctx, YandexAuthLoginOptions()))
        }
    }

    private fun exchange(providerToken: String) {
        setBusy(true)
        hostScope().launch {
            try {
                auth.signIn(AuthProvider.YANDEX, providerToken)
                PushManager.registerIfEnabled(context)
                afterSignIn()
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
                showError(failure)
            } finally {
                setBusy(false)
            }
        }
    }

    private fun completeNickname() {
        val v = views ?: return
        val nickname = v.nickname.text?.toString()?.trim().orEmpty()
        if (nickname.isBlank()) {
            v.nicknameLayout.error = context.getString(R.string.account_nickname_required)
            return
        }
        v.nicknameLayout.error = null
        setBusy(true)
        hostScope().launch {
            try {
                auth.completeProfile(nickname)
                PushManager.registerIfEnabled(context)
                afterSignIn()
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: Exception) {
                showError(failure)
            } finally {
                setBusy(false)
            }
        }
    }

    private fun afterSignIn() {
        when (val state = auth.state.value) {
            is AuthState.Authenticated -> {
                sheet?.dismiss()
                Toast.makeText(
                    context,
                    context.getString(R.string.quick_sign_in_done, state.profile.nickname),
                    Toast.LENGTH_SHORT,
                ).show()
                onSignedIn()
            }
            is AuthState.ProfileRequired -> showNicknameStep(openSheet())
            else -> showError(null)
        }
    }

    private fun yandexSdk(): YandexAuthSdk? {
        if (!BuildConfig.YANDEX_OAUTH_CONFIGURED || BuildConfig.SYNTHETIC_AUTH_ENABLED) return null
        return yandexSdk ?: YandexAuthSdk.create(
            YandexAuthOptions(context, loggingEnabled = false),
        ).also { yandexSdk = it }
    }

    companion object {
        /** What an account adds, one icon per line. */
        private val BENEFITS: List<Pair<Int, Int>> = listOf(
            benefit(R.drawable.ic_heart, R.string.quick_sign_in_benefit_like),
            benefit(R.drawable.ic_reply, R.string.quick_sign_in_benefit_answer),
            benefit(R.drawable.ic_add, R.string.quick_sign_in_benefit_ask),
            benefit(R.drawable.ic_comment, R.string.quick_sign_in_benefit_opinions),
        )

        private fun benefit(@DrawableRes icon: Int, @StringRes text: Int) = icon to text

        fun of(activity: ComponentActivity, onSignedIn: () -> Unit = {}): QuickSignIn =
            QuickSignIn(activity, { activity }, { activity.lifecycleScope }, onSignedIn)

        fun of(fragment: Fragment, onSignedIn: () -> Unit = {}): QuickSignIn =
            QuickSignIn(fragment, { fragment.requireContext() }, { fragment.lifecycleScope }, onSignedIn)
    }
}
