package com.plantdiseases.app.util

import android.content.Context
import android.content.SharedPreferences
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingCatalog
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingChapter
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingChapterProgress
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingChapterStatus
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStepId

/** Versioned, app-wide persistence for the independent contextual chapters. */
class OnboardingStateStore internal constructor(
    private val preferences: SharedPreferences,
) {

    constructor(context: Context) : this(
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE),
    )

    init {
        migrateIfNeeded()
    }

    val isAutomaticGuidanceEnabled: Boolean
        get() = preferences.getBoolean(KEY_ENABLED, false)

    val isSkipAll: Boolean
        get() = preferences.getBoolean(KEY_SKIP_ALL, false)

    /** Called only by the first-launch privacy screen on a genuinely new install. */
    fun startForNewInstall() = reset(enabled = true)

    /** Manual Profile entry point. Consent and every unrelated preference live elsewhere. */
    fun replay() = reset(enabled = true)

    fun progress(chapter: OnboardingChapter): OnboardingChapterProgress {
        val status = preferences.getString(statusKey(chapter), null)
            ?.let { raw -> OnboardingChapterStatus.entries.firstOrNull { it.name == raw } }
            ?: defaultStatus(chapter)
        val savedStep = preferences.getString(stepKey(chapter), null)
            ?.let { raw -> OnboardingStepId.entries.firstOrNull { it.name == raw } }
            ?.takeIf { OnboardingCatalog.step(it).chapter == chapter }
            ?: OnboardingCatalog.firstStep(chapter)
        return OnboardingChapterProgress(chapter, status, savedStep)
    }

    fun isEligible(chapter: OnboardingChapter): Boolean =
        isAutomaticGuidanceEnabled &&
            !isSkipAll &&
            progress(chapter).status == OnboardingChapterStatus.PENDING

    fun markStepShown(chapter: OnboardingChapter, step: OnboardingStepId) {
        require(OnboardingCatalog.step(step).chapter == chapter)
        updateStepSet(shownStepsKey(chapter), step)
    }

    fun wasStepShown(chapter: OnboardingChapter, step: OnboardingStepId): Boolean =
        step.name in preferences.getStringSet(shownStepsKey(chapter), emptySet()).orEmpty()

    fun wasStepOmitted(chapter: OnboardingChapter, step: OnboardingStepId): Boolean =
        step.name in preferences.getStringSet(omittedStepsKey(chapter), emptySet()).orEmpty()

    fun omitAndAdvance(
        chapter: OnboardingChapter,
        step: OnboardingStepId,
    ): OnboardingChapterProgress = synchronized(preferences) {
        require(OnboardingCatalog.step(step).chapter == chapter)
        require(progress(chapter).currentStep == step)
        updateStepSet(omittedStepsKey(chapter), step)
        advance(chapter)
    }

    fun advance(chapter: OnboardingChapter): OnboardingChapterProgress = synchronized(preferences) {
        val current = progress(chapter)
        if (current.status != OnboardingChapterStatus.PENDING) return@synchronized current
        val steps = OnboardingCatalog.steps(chapter)
        val index = steps.indexOfFirst { it.id == current.currentStep }
        if (index >= steps.lastIndex) {
            setStatus(chapter, OnboardingChapterStatus.COMPLETED)
        } else {
            setStep(chapter, steps[index + 1].id)
        }
        progress(chapter)
    }

    fun back(chapter: OnboardingChapter): OnboardingChapterProgress = synchronized(preferences) {
        val current = progress(chapter)
        if (current.status != OnboardingChapterStatus.PENDING) return@synchronized current
        val steps = OnboardingCatalog.steps(chapter)
        val index = steps.indexOfFirst { it.id == current.currentStep }
        if (index > 0) setStep(chapter, steps[index - 1].id)
        progress(chapter)
    }

    fun moveTo(chapter: OnboardingChapter, step: OnboardingStepId): OnboardingChapterProgress {
        require(OnboardingCatalog.step(step).chapter == chapter)
        if (progress(chapter).status == OnboardingChapterStatus.PENDING) setStep(chapter, step)
        return progress(chapter)
    }

    fun skipChapter(chapter: OnboardingChapter) = setStatus(chapter, OnboardingChapterStatus.SKIPPED)

    fun completeChapter(chapter: OnboardingChapter) = setStatus(chapter, OnboardingChapterStatus.COMPLETED)

    /** Enables a contextual continuation that is dormant during normal replay/reset. */
    fun activateChapter(chapter: OnboardingChapter) = synchronized(preferences) {
        if (!isAutomaticGuidanceEnabled || isSkipAll) return@synchronized progress(chapter)
        setStep(chapter, OnboardingCatalog.firstStep(chapter))
        setStatus(chapter, OnboardingChapterStatus.PENDING)
        clearStepSets(chapter)
        progress(chapter)
    }

    fun skipAll() = synchronized(preferences) {
        preferences.edit().apply {
            putBoolean(KEY_SKIP_ALL, true)
            OnboardingChapter.entries.forEach { chapter ->
                putString(statusKey(chapter), OnboardingChapterStatus.SKIPPED.name)
            }
        }.apply()
    }

    private fun reset(enabled: Boolean) = synchronized(preferences) {
        preferences.edit().apply {
            putInt(KEY_VERSION, CURRENT_VERSION)
            putBoolean(KEY_ENABLED, enabled)
            putBoolean(KEY_SKIP_ALL, false)
            OnboardingChapter.entries.forEach { chapter ->
                putString(statusKey(chapter), defaultStatus(chapter).name)
                putString(stepKey(chapter), OnboardingCatalog.firstStep(chapter).name)
                putStringSet(shownStepsKey(chapter), emptySet())
                putStringSet(omittedStepsKey(chapter), emptySet())
            }
        }.apply()
    }

    private fun setStep(chapter: OnboardingChapter, step: OnboardingStepId) {
        preferences.edit().putString(stepKey(chapter), step.name).apply()
    }

    private fun setStatus(chapter: OnboardingChapter, status: OnboardingChapterStatus) {
        preferences.edit().putString(statusKey(chapter), status.name).apply()
    }

    private fun updateStepSet(key: String, step: OnboardingStepId) = synchronized(preferences) {
        val existing = preferences.getStringSet(key, emptySet()).orEmpty()
        if (step.name !in existing) {
            preferences.edit().putStringSet(key, existing + step.name).apply()
        }
    }

    private fun clearStepSets(chapter: OnboardingChapter) {
        preferences.edit()
            .putStringSet(shownStepsKey(chapter), emptySet())
            .putStringSet(omittedStepsKey(chapter), emptySet())
            .apply()
    }

    /**
     * A missing schema belongs to an existing install unless the consent screen
     * explicitly calls [startForNewInstall]. This is what prevents an upgrade
     * from suddenly displaying guidance.
     */
    private fun migrateIfNeeded() = synchronized(preferences) {
        val version = preferences.getInt(KEY_VERSION, 0)
        if (version >= CURRENT_VERSION) return@synchronized
        val edit = preferences.edit()
        if (version == 0) {
            edit.putBoolean(KEY_ENABLED, false)
            edit.putBoolean(KEY_SKIP_ALL, false)
        }
        OnboardingChapter.entries.forEach { chapter ->
            if (!preferences.contains(statusKey(chapter))) {
                edit.putString(statusKey(chapter), defaultStatus(chapter).name)
            }
            if (!preferences.contains(stepKey(chapter))) {
                edit.putString(stepKey(chapter), OnboardingCatalog.firstStep(chapter).name)
            }
            if (!preferences.contains(shownStepsKey(chapter))) {
                edit.putStringSet(shownStepsKey(chapter), emptySet())
            }
            if (!preferences.contains(omittedStepsKey(chapter))) {
                edit.putStringSet(omittedStepsKey(chapter), emptySet())
            }
        }
        edit.putInt(KEY_VERSION, CURRENT_VERSION).apply()
    }

    companion object {
        internal const val PREFS_NAME = "plantdiseases_onboarding_state"
        internal const val CURRENT_VERSION = 4
        internal const val KEY_VERSION = "schema_version"
        internal const val KEY_ENABLED = "guidance_enabled"
        internal const val KEY_SKIP_ALL = "skip_all"

        internal fun statusKey(chapter: OnboardingChapter) = "chapter.${chapter.storageKey}.status"

        internal fun stepKey(chapter: OnboardingChapter) = "chapter.${chapter.storageKey}.step"

        internal fun shownStepsKey(chapter: OnboardingChapter) = "chapter.${chapter.storageKey}.shown_steps"

        internal fun omittedStepsKey(chapter: OnboardingChapter) = "chapter.${chapter.storageKey}.omitted_steps"

        private fun defaultStatus(chapter: OnboardingChapter): OnboardingChapterStatus =
            if (chapter.startsAutomatically) {
                OnboardingChapterStatus.PENDING
            } else {
                OnboardingChapterStatus.SKIPPED
            }
    }
}
