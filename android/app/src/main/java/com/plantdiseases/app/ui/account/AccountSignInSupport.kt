package com.plantdiseases.app.ui.account

import android.content.Context
import android.content.DialogInterface
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.annotation.StringRes
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.LogoutNotDurableException
import com.plantdiseases.app.auth.SignupAlreadyConfirmedException
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.util.AccountDisclosurePrefs
import java.io.IOException

/*
 * Sign-in pieces shared by the account screen and the quick sign-in sheet
 * ([QuickSignIn]), so both say the same thing and ask for the same
 * acknowledgement before the first provider exchange.
 */

/** The user-facing message for a failed account request. */
@StringRes
internal fun accountErrorMessage(failure: Throwable?): Int = when (failure) {
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

/**
 * Runs [continueSignIn] once the versioned account disclosure is acknowledged:
 * at once when it already is, otherwise after the user accepts the dialog.
 */
internal fun withAccountDisclosure(
    context: Context,
    prefs: AccountDisclosurePrefs,
    continueSignIn: () -> Unit,
) {
    if (prefs.acknowledged) {
        continueSignIn()
        return
    }
    val dialog = MaterialAlertDialogBuilder(context)
        .setTitle(R.string.account_disclosure_title)
        .setMessage(R.string.account_disclosure_body)
        .setNegativeButton(android.R.string.cancel, null)
        .setNeutralButton(R.string.account_disclosure_privacy, null)
        .setPositiveButton(R.string.account_disclosure_acknowledge) { _, _ ->
            val acknowledgementSaved = try {
                prefs.acknowledge()
                true
            } catch (_: Exception) {
                false
            }
            if (acknowledgementSaved) {
                continueSignIn()
            } else {
                Toast.makeText(context, R.string.account_disclosure_save_failed, Toast.LENGTH_LONG).show()
            }
        }
        .create()
    dialog.setOnShowListener {
        dialog.getButton(DialogInterface.BUTTON_NEUTRAL).setOnClickListener { openAccountPrivacyPolicy(context) }
    }
    dialog.show()
}

/** The hosted privacy policy, in the browser. */
internal fun openAccountPrivacyPolicy(context: Context) {
    runCatching {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(context.getString(R.string.consent_privacy_url))))
    }.onFailure {
        Toast.makeText(context, R.string.account_privacy_open_failed, Toast.LENGTH_LONG).show()
    }
}
