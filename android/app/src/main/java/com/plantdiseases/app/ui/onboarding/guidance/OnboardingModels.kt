package com.plantdiseases.app.ui.onboarding.guidance

import androidx.annotation.StringRes

enum class OnboardingChapter(
    val storageKey: String,
    val startsAutomatically: Boolean = true,
) {
    HOME_SCAN("home_scan"),
    GARDEN("garden"),
    GARDEN_SCAN_ATTACH("garden_scan_attach", startsAutomatically = false),
    GARDEN_PLANT_DETAILS("garden_plant_details"),
    GALLERY("gallery"),
    PROFILE("profile"),
    SPECIES_DETAILS("species_details"),
}

enum class OnboardingChapterStatus {
    PENDING,
    COMPLETED,
    SKIPPED,
}

enum class OnboardingContext {
    HOME,
    CAMERA,
    GARDEN,
    GALLERY,
    PROFILE,
    RESULT,
    GARDEN_PLANT_DETAILS,
    SPECIES_DETAILS,
}

/** Stable, persistence-safe identifiers. Never rename an existing value. */
enum class OnboardingStepId {
    HOME_SCAN_IDENTIFY,
    HOME_QUICK_TOOLS,
    HOME_SCAN_NAV,
    CAMERA_PERMISSION,
    CAMERA_PREVIEW,
    CAMERA_GALLERY,
    CAMERA_SHUTTER,
    RESULT_DIAGNOSIS,
    GARDEN_HERO,
    GARDEN_PLANTS,
    GARDEN_ADD,
    GARDEN_RESULT_ADD,
    GARDEN_PLANT_SUMMARY,
    GARDEN_PLANT_ACTIONS,
    GARDEN_PLANT_CARE,
    GARDEN_PLANT_HISTORY,
    GARDEN_PLANT_ASSISTANT,
    GALLERY_FILTERS,
    GALLERY_RESULTS,
    PROFILE_SETTINGS,
    PROFILE_STATISTICS,
    PROFILE_REPLAY,
    SPECIES_SUMMARY,
    SPECIES_CARE,
    SPECIES_FLOWERING,
    SPECIES_NATIVE_RANGE,
}

enum class OnboardingAnchorKey {
    HOME_SCAN_IDENTIFY,
    HOME_QUICK_TOOLS,
    BOTTOM_NAV_SCAN,
    CAMERA_PERMISSION,
    CAMERA_PREVIEW,
    CAMERA_GALLERY,
    CAMERA_SHUTTER,
    RESULT_DIAGNOSIS,
    GARDEN_HERO,
    GARDEN_PLANTS,
    GARDEN_ADD,
    RESULT_ADD_TO_GARDEN,
    GARDEN_PLANT_SUMMARY,
    GARDEN_PLANT_ACTIONS,
    GARDEN_PLANT_CARE,
    GARDEN_PLANT_HISTORY,
    GARDEN_PLANT_ASSISTANT,
    GALLERY_FILTERS,
    GALLERY_RESULTS,
    PROFILE_SETTINGS,
    PROFILE_STATISTICS,
    PROFILE_REPLAY,
    SPECIES_SUMMARY,
    SPECIES_CARE,
    SPECIES_FLOWERING,
    SPECIES_NATIVE_RANGE,
}

enum class OnboardingTargetAction {
    NONE,

    /** Advance first, then invoke the highlighted view's existing click flow. */
    ADVANCE,

    /** Invoke the target and keep the same step until the external flow resolves. */
    PAUSE,
}

data class OnboardingStep(
    val id: OnboardingStepId,
    val chapter: OnboardingChapter,
    val context: OnboardingContext,
    val anchor: OnboardingAnchorKey,
    @StringRes val titleRes: Int,
    @StringRes val bodyRes: Int,
    @StringRes val inlineLinkTextRes: Int? = null,
    val targetAction: OnboardingTargetAction = OnboardingTargetAction.NONE,
    val forwardUsesTarget: Boolean = false,
    /** False for a whole-screen explanation that should leave the underlying UI fully visible. */
    val dimBackground: Boolean = true,
    /** Count this step only when its optional target exists or the user has already seen it. */
    val conditional: Boolean = false,
    /** The host has resolved this target but may report it unavailable. */
    val skipWhenUnavailable: Boolean = false,
)

data class OnboardingChapterProgress(
    val chapter: OnboardingChapter,
    val status: OnboardingChapterStatus,
    val currentStep: OnboardingStepId,
)
