package com.plantdiseases.app.ui.onboarding.guidance

import android.content.SharedPreferences
import com.google.common.truth.Truth.assertThat
import org.junit.Before
import org.junit.Test

class OnboardingStateStoreTest {

    private lateinit var preferences: InMemorySharedPreferences

    @Before
    fun setUp() {
        preferences = InMemorySharedPreferences()
    }

    @Test
    fun missingStateTreatsInstallAsExistingAndDoesNotPrompt() {
        val store = store()

        assertThat(store.isAutomaticGuidanceEnabled).isFalse()
        assertThat(store.isEligible(OnboardingChapter.HOME_SCAN)).isFalse()
    }

    @Test
    fun newInstallStartsOnlyAfterExplicitActivation() {
        val store = store()

        store.startForNewInstall()

        assertThat(store.isEligible(OnboardingChapter.HOME_SCAN)).isTrue()
        assertThat(store.isEligible(OnboardingChapter.SPECIES_DETAILS)).isTrue()
        assertThat(store.isEligible(OnboardingChapter.GARDEN_PLANT_DETAILS)).isTrue()
        assertThat(store.isEligible(OnboardingChapter.GARDEN_SCAN_ATTACH)).isFalse()
    }

    @Test
    fun chaptersAdvanceAndCompleteIndependentlyInAnyOrder() {
        val store = store().apply { startForNewInstall() }

        store.advance(OnboardingChapter.GALLERY)
        store.advance(OnboardingChapter.GALLERY)

        assertThat(store.progress(OnboardingChapter.GALLERY).status)
            .isEqualTo(OnboardingChapterStatus.COMPLETED)
        assertThat(store.progress(OnboardingChapter.HOME_SCAN).currentStep)
            .isEqualTo(OnboardingStepId.HOME_SCAN_IDENTIFY)
        assertThat(store.isEligible(OnboardingChapter.PROFILE)).isTrue()
    }

    @Test
    fun backtrackingStopsAtChapterBeginning() {
        val store = store().apply { startForNewInstall() }

        store.advance(OnboardingChapter.HOME_SCAN)
        assertThat(store.back(OnboardingChapter.HOME_SCAN).currentStep)
            .isEqualTo(OnboardingStepId.HOME_SCAN_IDENTIFY)
        assertThat(store.back(OnboardingChapter.HOME_SCAN).currentStep)
            .isEqualTo(OnboardingStepId.HOME_SCAN_IDENTIFY)
    }

    @Test
    fun skipSectionDoesNotDisableOtherChapters() {
        val store = store().apply { startForNewInstall() }

        store.skipChapter(OnboardingChapter.GARDEN)

        assertThat(store.progress(OnboardingChapter.GARDEN).status)
            .isEqualTo(OnboardingChapterStatus.SKIPPED)
        assertThat(store.isEligible(OnboardingChapter.GARDEN)).isFalse()
        assertThat(store.isEligible(OnboardingChapter.PROFILE)).isTrue()
    }

    @Test
    fun skipAllDisablesEveryChapter() {
        val store = store().apply { startForNewInstall() }

        store.skipAll()

        assertThat(store.isSkipAll).isTrue()
        OnboardingChapter.entries.forEach { chapter ->
            assertThat(store.isEligible(chapter)).isFalse()
            assertThat(store.progress(chapter).status).isEqualTo(OnboardingChapterStatus.SKIPPED)
        }
    }

    @Test
    fun replayResetsProgressAndSkipAll() {
        val store = store().apply { startForNewInstall() }
        store.advance(OnboardingChapter.HOME_SCAN)
        store.skipChapter(OnboardingChapter.GARDEN)
        store.skipAll()

        store.replay()

        assertThat(store.isSkipAll).isFalse()
        OnboardingChapter.entries.forEach { chapter ->
            val progress = store.progress(chapter)
            val expectedStatus = if (chapter.startsAutomatically) {
                OnboardingChapterStatus.PENDING
            } else {
                OnboardingChapterStatus.SKIPPED
            }
            assertThat(progress.status).isEqualTo(expectedStatus)
            assertThat(progress.currentStep).isEqualTo(OnboardingCatalog.firstStep(chapter))
        }
    }

    @Test
    fun gardenScanContinuationIsDormantUntilInlineLinkActivatesIt() {
        val store = store().apply { startForNewInstall() }

        assertThat(store.isEligible(OnboardingChapter.GARDEN_SCAN_ATTACH)).isFalse()

        store.activateChapter(OnboardingChapter.GARDEN_SCAN_ATTACH)

        assertThat(store.isEligible(OnboardingChapter.GARDEN_SCAN_ATTACH)).isTrue()
        assertThat(store.progress(OnboardingChapter.GARDEN_SCAN_ATTACH).currentStep)
            .isEqualTo(OnboardingStepId.GARDEN_RESULT_ADD)
    }

    @Test
    fun cameraPreviewIsUndimmedAndPlantGuidanceEndsAtAssistant() {
        assertThat(OnboardingCatalog.step(OnboardingStepId.CAMERA_PREVIEW).dimBackground).isFalse()
        assertThat(OnboardingCatalog.step(OnboardingStepId.CAMERA_PERMISSION).conditional).isTrue()
        assertThat(OnboardingCatalog.steps(OnboardingChapter.GARDEN_PLANT_DETAILS).last().id)
            .isEqualTo(OnboardingStepId.GARDEN_PLANT_ASSISTANT)
    }

    @Test
    fun omittedConditionalStepPersistsAndReplayClearsVisibilityHistory() {
        val store = store().apply {
            startForNewInstall()
            moveTo(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PERMISSION)
            omitAndAdvance(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PERMISSION)
        }

        assertThat(store.progress(OnboardingChapter.HOME_SCAN).currentStep)
            .isEqualTo(OnboardingStepId.CAMERA_PREVIEW)
        assertThat(store.wasStepOmitted(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PERMISSION))
            .isTrue()
        assertThat(
            store().wasStepOmitted(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PERMISSION),
        ).isTrue()

        store.markStepShown(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PREVIEW)
        store.replay()

        assertThat(store.wasStepOmitted(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PERMISSION))
            .isFalse()
        assertThat(store.wasStepShown(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_PREVIEW))
            .isFalse()
    }

    @Test
    fun currentStepSurvivesStoreRecreation() {
        store().apply {
            startForNewInstall()
            moveTo(OnboardingChapter.HOME_SCAN, OnboardingStepId.CAMERA_GALLERY)
        }

        val recreated = store()

        assertThat(recreated.progress(OnboardingChapter.HOME_SCAN).currentStep)
            .isEqualTo(OnboardingStepId.CAMERA_GALLERY)
    }

    @Test
    fun versionOneMigrationPreservesKnownProgressAndAddsMissingChapters() {
        preferences.edit()
            .putInt(OnboardingStateStore.KEY_VERSION, 1)
            .putBoolean(OnboardingStateStore.KEY_ENABLED, true)
            .putString(
                OnboardingStateStore.statusKey(OnboardingChapter.GARDEN),
                OnboardingChapterStatus.COMPLETED.name,
            )
            .putString(
                OnboardingStateStore.stepKey(OnboardingChapter.HOME_SCAN),
                OnboardingStepId.CAMERA_PREVIEW.name,
            )
            .commit()

        val migrated = store()

        assertThat(preferences.getInt(OnboardingStateStore.KEY_VERSION, 0))
            .isEqualTo(OnboardingStateStore.CURRENT_VERSION)
        assertThat(migrated.progress(OnboardingChapter.GARDEN).status)
            .isEqualTo(OnboardingChapterStatus.COMPLETED)
        assertThat(migrated.progress(OnboardingChapter.HOME_SCAN).currentStep)
            .isEqualTo(OnboardingStepId.CAMERA_PREVIEW)
        assertThat(migrated.progress(OnboardingChapter.PROFILE).status)
            .isEqualTo(OnboardingChapterStatus.PENDING)
        assertThat(migrated.progress(OnboardingChapter.GARDEN_PLANT_DETAILS).status)
            .isEqualTo(OnboardingChapterStatus.PENDING)
        assertThat(migrated.progress(OnboardingChapter.GARDEN_SCAN_ATTACH).status)
            .isEqualTo(OnboardingChapterStatus.SKIPPED)
    }

    private fun store() = OnboardingStateStore(preferences)
}

private class InMemorySharedPreferences : SharedPreferences {
    private val values = linkedMapOf<String, Any?>()
    private val listeners = linkedSetOf<SharedPreferences.OnSharedPreferenceChangeListener>()

    override fun getAll(): MutableMap<String, *> = values.toMutableMap()

    override fun getString(key: String?, default: String?): String? = values[key] as? String ?: default

    @Suppress("UNCHECKED_CAST")
    override fun getStringSet(key: String?, default: MutableSet<String>?): MutableSet<String>? =
        (values[key] as? Set<String>)?.toMutableSet() ?: default

    override fun getInt(key: String?, default: Int): Int = values[key] as? Int ?: default

    override fun getLong(key: String?, default: Long): Long = values[key] as? Long ?: default

    override fun getFloat(key: String?, default: Float): Float = values[key] as? Float ?: default

    override fun getBoolean(key: String?, default: Boolean): Boolean = values[key] as? Boolean ?: default

    override fun contains(key: String?): Boolean = values.containsKey(key)

    override fun edit(): SharedPreferences.Editor = Editor()

    override fun registerOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener?,
    ) {
        if (listener != null) listeners += listener
    }

    override fun unregisterOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener?,
    ) {
        if (listener != null) listeners -= listener
    }

    private inner class Editor : SharedPreferences.Editor {
        private val updates = linkedMapOf<String, Any?>()
        private val removals = linkedSetOf<String>()
        private var clearRequested = false

        override fun putString(key: String?, value: String?) = put(key, value)

        override fun putStringSet(key: String?, values: MutableSet<String>?) = put(key, values?.toSet())

        override fun putInt(key: String?, value: Int) = put(key, value)

        override fun putLong(key: String?, value: Long) = put(key, value)

        override fun putFloat(key: String?, value: Float) = put(key, value)

        override fun putBoolean(key: String?, value: Boolean) = put(key, value)

        override fun remove(key: String?): SharedPreferences.Editor = apply {
            if (key != null) removals += key
        }

        override fun clear(): SharedPreferences.Editor = apply { clearRequested = true }

        override fun commit(): Boolean {
            val changed = linkedSetOf<String>()
            if (clearRequested) {
                changed += values.keys
                values.clear()
            }
            removals.forEach { key ->
                if (values.remove(key) != null) changed += key
            }
            updates.forEach { (key, value) ->
                if (values[key] != value) changed += key
                if (value == null) values.remove(key) else values[key] = value
            }
            changed.forEach { key ->
                listeners.forEach { listener -> listener.onSharedPreferenceChanged(this@InMemorySharedPreferences, key) }
            }
            return true
        }

        override fun apply() {
            commit()
        }

        private fun put(key: String?, value: Any?): SharedPreferences.Editor = apply {
            if (key != null) updates[key] = value
        }
    }
}
