package com.plantdiseases.app.auth

import com.plantdiseases.app.data.account.AccountSession
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.TokenProfile

sealed interface AuthState {
    data object Guest : AuthState

    data class ProfileRequired(
        val provider: AuthProvider,
        val pendingSignupToken: String,
        val expiresInSeconds: Int,
    ) : AuthState

    data class Authenticated(
        val profile: TokenProfile,
        val session: AccountSession,
    ) : AuthState
}

class AuthenticationRequiredException : Exception("Authentication required")
