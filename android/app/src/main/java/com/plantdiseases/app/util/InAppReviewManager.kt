package com.plantdiseases.app.util

import android.content.Context
import android.util.Log
import ru.rustore.sdk.review.RuStoreReviewManagerFactory
import java.util.concurrent.TimeUnit

/**
 * RuStore in-app reviews (rustore.ru/help/sdk/reviews-ratings).
 *
 * [maybeRequestReview] asks the store app to prepare a review flow and, if one
 * comes back, launches RuStore's own native rate/review dialog — at most once
 * per [PROMPT_INTERVAL_MS]. RuStore itself refuses more than one launch a day
 * (`RuStoreRequestLimitReached`); ours is far longer so a returning user isn't
 * asked to rate the app every time they hit a satisfying moment.
 *
 * Callers fire this from wherever the app already knows the user is having a
 * good moment (a confirmed-correct diagnosis, a scan attached to a garden
 * plant, a care plan turned into reminders, ...) — every call site shares this
 * one cooldown, so adding more of them only improves the odds of catching a
 * good moment soon, it never increases how often people actually get asked.
 *
 * Everywhere else — sideloads, other stores, devices without the RuStore app,
 * a user who already reviewed — the SDK reports failure and the caller's flow
 * continues untouched, matching [InAppUpdateManager]'s degrade-on-failure
 * shape.
 */
class InAppReviewManager(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun maybeRequestReview() {
        if (!shouldPromptNow()) return
        runCatching {
            val manager = RuStoreReviewManagerFactory.create(appContext)
            manager.requestReviewFlow()
                .addOnSuccessListener { reviewInfo ->
                    // Marked on success, before the dialog resolves: RuStore's
                    // own 1/day limit means a failed launchReviewFlow below is
                    // still worth waiting out — retrying sooner would just hit
                    // RuStoreRequestLimitReached.
                    markPrompted()
                    manager.launchReviewFlow(reviewInfo)
                        .addOnFailureListener { e -> Log.d(TAG, "launchReviewFlow failed", e) }
                }
                .addOnFailureListener { e -> Log.d(TAG, "requestReviewFlow unavailable", e) }
        }.onFailure { e -> Log.w(TAG, "RuStore review SDK unavailable", e) }
    }

    internal fun shouldPromptNow(now: Long = System.currentTimeMillis()): Boolean {
        val elapsed = now - prefs.getLong(KEY_LAST_PROMPT, 0L)
        return elapsed < 0 || elapsed >= PROMPT_INTERVAL_MS
    }

    internal fun markPrompted(now: Long = System.currentTimeMillis()) {
        prefs.edit().putLong(KEY_LAST_PROMPT, now).apply()
    }

    companion object {
        private const val TAG = "InAppReviewManager"
        private const val PREFS_NAME = "plantdiseases_prefs"
        private const val KEY_LAST_PROMPT = "rustore_review_last_prompt_ms"
        private val PROMPT_INTERVAL_MS = TimeUnit.DAYS.toMillis(60)
    }
}
