package com.plantdiseases.app.auth

import com.plantdiseases.app.data.account.AccountSession
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.TokenProfile

sealed interface AuthState {
    /** A persisted credential may exist and is being checked; sign-in stays disabled. */
    data object Restoring : AuthState

    data object Guest : AuthState

    /**
     * Authentication cannot currently be trusted. A previous profile is kept
     * only for continuity; protected actions remain disabled until restore or
     * a fresh provider sign-in succeeds.
     */
    data class Unavailable(
        val lastAuthenticated: Authenticated? = null,
    ) : AuthState

    data class ProfileRequired(
        val provider: AuthProvider,
        val pendingSignupToken: String,
        val expiresInSeconds: Int,
        /** Frozen after an ambiguous request; null again after a pre-commit rejection. */
        val pendingNickname: String? = null,
    ) : AuthState

    data class Authenticated(
        val profile: TokenProfile,
        val session: AccountSession,
    ) : AuthState
}

class AuthenticationRequiredException : Exception("Authentication required")

class AuthenticationUnavailableException : Exception("Authentication temporarily unavailable")

class SignupCompletionInProgressException : Exception("Signup completion is already in progress")

class SignupCompletionConflictException : Exception("Signup completion inputs changed")

class SignupAlreadyConfirmedException : Exception("Signup was already confirmed")

class LogoutNotDurableException(
    cause: Throwable? = null,
) : Exception("Local or remote logout could not be made durable", cause)
