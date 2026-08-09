package com.plantdiseases.app.push

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.testutil.FakeAccountApi
import org.junit.Test

class PushManagerTest {
    private val session = FakeAccountApi.session()
    private val authenticated = AuthState.Authenticated(FakeAccountApi.profile(), session)

    @Test
    fun registrationStillAllowed_requiresOptInAndSameAuthenticatedSession() {
        assertThat(
            PushManager.registrationStillAllowed(
                notificationsEnabled = true,
                state = authenticated,
                expectedSessionId = session.sessionId,
            ),
        ).isTrue()

        assertThat(
            PushManager.registrationStillAllowed(
                notificationsEnabled = false,
                state = authenticated,
                expectedSessionId = session.sessionId,
            ),
        ).isFalse()
        assertThat(
            PushManager.registrationStillAllowed(
                notificationsEnabled = true,
                state = AuthState.Guest,
                expectedSessionId = session.sessionId,
            ),
        ).isFalse()
        assertThat(
            PushManager.registrationStillAllowed(
                notificationsEnabled = true,
                state = AuthState.Restoring,
                expectedSessionId = session.sessionId,
            ),
        ).isFalse()
        assertThat(
            PushManager.registrationStillAllowed(
                notificationsEnabled = true,
                state = AuthState.Unavailable(authenticated),
                expectedSessionId = session.sessionId,
            ),
        ).isFalse()
        assertThat(
            PushManager.registrationStillAllowed(
                notificationsEnabled = true,
                state = authenticated,
                expectedSessionId = "another-session",
            ),
        ).isFalse()
    }

    @Test
    fun notificationRequiresExactAuthenticatedActorBinding() {
        assertThat(
            PushManager.notificationBelongsToAuthenticatedActor(
                targetCommunityActorId = FakeAccountApi.ACTOR_ID,
                state = authenticated,
            ),
        ).isTrue()
        assertThat(
            PushManager.notificationBelongsToAuthenticatedActor(
                targetCommunityActorId = "another-actor",
                state = authenticated,
            ),
        ).isFalse()
        assertThat(
            PushManager.notificationBelongsToAuthenticatedActor(
                targetCommunityActorId = null,
                state = authenticated,
            ),
        ).isFalse()
        assertThat(
            PushManager.notificationBelongsToAuthenticatedActor(
                targetCommunityActorId = FakeAccountApi.ACTOR_ID,
                state = AuthState.Guest,
            ),
        ).isFalse()
    }
}
