package com.plantdiseases.app.ui.onboarding.guidance

import com.plantdiseases.app.R

object OnboardingCatalog {
    val steps: List<OnboardingStep> = listOf(
        step(
            OnboardingStepId.HOME_SCAN_IDENTIFY,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.HOME,
            OnboardingAnchorKey.HOME_SCAN_IDENTIFY,
            R.string.guidance_home_actions,
        ),
        step(
            OnboardingStepId.HOME_QUICK_TOOLS,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.HOME,
            OnboardingAnchorKey.HOME_QUICK_TOOLS,
            R.string.guidance_home_tools,
        ),
        step(
            OnboardingStepId.HOME_SCAN_NAV,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.HOME,
            OnboardingAnchorKey.BOTTOM_NAV_SCAN,
            R.string.guidance_home_scan_nav,
            targetAction = OnboardingTargetAction.ADVANCE,
            forwardUsesTarget = true,
        ),
        step(
            OnboardingStepId.CAMERA_PERMISSION,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.CAMERA,
            OnboardingAnchorKey.CAMERA_PERMISSION,
            R.string.guidance_camera_permission,
            targetAction = OnboardingTargetAction.PAUSE,
            conditional = true,
            skipWhenUnavailable = true,
        ),
        step(
            OnboardingStepId.CAMERA_PREVIEW,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.CAMERA,
            OnboardingAnchorKey.CAMERA_PREVIEW,
            R.string.guidance_camera_preview,
            dimBackground = false,
            skipWhenUnavailable = true,
        ),
        step(
            OnboardingStepId.CAMERA_GALLERY,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.CAMERA,
            OnboardingAnchorKey.CAMERA_GALLERY,
            R.string.guidance_camera_gallery,
            targetAction = OnboardingTargetAction.PAUSE,
        ),
        step(
            OnboardingStepId.CAMERA_SHUTTER,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.CAMERA,
            OnboardingAnchorKey.CAMERA_SHUTTER,
            R.string.guidance_camera_shutter,
            targetAction = OnboardingTargetAction.ADVANCE,
        ),
        step(
            OnboardingStepId.RESULT_DIAGNOSIS,
            OnboardingChapter.HOME_SCAN,
            OnboardingContext.RESULT,
            OnboardingAnchorKey.RESULT_DIAGNOSIS,
            R.string.guidance_result,
        ),
        step(
            OnboardingStepId.GARDEN_HERO,
            OnboardingChapter.GARDEN,
            OnboardingContext.GARDEN,
            OnboardingAnchorKey.GARDEN_HERO,
            R.string.guidance_garden_hero,
        ),
        step(
            OnboardingStepId.GARDEN_PLANTS,
            OnboardingChapter.GARDEN,
            OnboardingContext.GARDEN,
            OnboardingAnchorKey.GARDEN_PLANTS,
            R.string.guidance_garden_plants,
        ),
        step(
            OnboardingStepId.GARDEN_ADD,
            OnboardingChapter.GARDEN,
            OnboardingContext.GARDEN,
            OnboardingAnchorKey.GARDEN_ADD,
            R.string.guidance_garden_add,
            targetAction = OnboardingTargetAction.ADVANCE,
            inlineLinkTextRes = R.string.guidance_garden_attach_scan_link,
        ),
        step(
            OnboardingStepId.GARDEN_RESULT_ADD,
            OnboardingChapter.GARDEN_SCAN_ATTACH,
            OnboardingContext.RESULT,
            OnboardingAnchorKey.RESULT_ADD_TO_GARDEN,
            R.string.guidance_garden_result_add,
            targetAction = OnboardingTargetAction.ADVANCE,
            skipWhenUnavailable = true,
        ),
        step(
            OnboardingStepId.GARDEN_PLANT_SUMMARY,
            OnboardingChapter.GARDEN_PLANT_DETAILS,
            OnboardingContext.GARDEN_PLANT_DETAILS,
            OnboardingAnchorKey.GARDEN_PLANT_SUMMARY,
            R.string.guidance_garden_plant_summary,
        ),
        step(
            OnboardingStepId.GARDEN_PLANT_ACTIONS,
            OnboardingChapter.GARDEN_PLANT_DETAILS,
            OnboardingContext.GARDEN_PLANT_DETAILS,
            OnboardingAnchorKey.GARDEN_PLANT_ACTIONS,
            R.string.guidance_garden_plant_actions,
        ),
        step(
            OnboardingStepId.GARDEN_PLANT_CARE,
            OnboardingChapter.GARDEN_PLANT_DETAILS,
            OnboardingContext.GARDEN_PLANT_DETAILS,
            OnboardingAnchorKey.GARDEN_PLANT_CARE,
            R.string.guidance_garden_plant_care,
        ),
        step(
            OnboardingStepId.GARDEN_PLANT_HISTORY,
            OnboardingChapter.GARDEN_PLANT_DETAILS,
            OnboardingContext.GARDEN_PLANT_DETAILS,
            OnboardingAnchorKey.GARDEN_PLANT_HISTORY,
            R.string.guidance_garden_plant_history,
        ),
        step(
            OnboardingStepId.GARDEN_PLANT_ASSISTANT,
            OnboardingChapter.GARDEN_PLANT_DETAILS,
            OnboardingContext.GARDEN_PLANT_DETAILS,
            OnboardingAnchorKey.GARDEN_PLANT_ASSISTANT,
            R.string.guidance_garden_plant_assistant,
            targetAction = OnboardingTargetAction.ADVANCE,
        ),
        step(
            OnboardingStepId.GALLERY_FILTERS,
            OnboardingChapter.GALLERY,
            OnboardingContext.GALLERY,
            OnboardingAnchorKey.GALLERY_FILTERS,
            R.string.guidance_gallery_filters,
        ),
        step(
            OnboardingStepId.GALLERY_RESULTS,
            OnboardingChapter.GALLERY,
            OnboardingContext.GALLERY,
            OnboardingAnchorKey.GALLERY_RESULTS,
            R.string.guidance_gallery_results,
        ),
        step(
            OnboardingStepId.PROFILE_SETTINGS,
            OnboardingChapter.PROFILE,
            OnboardingContext.PROFILE,
            OnboardingAnchorKey.PROFILE_SETTINGS,
            R.string.guidance_profile_settings,
        ),
        step(
            OnboardingStepId.PROFILE_STATISTICS,
            OnboardingChapter.PROFILE,
            OnboardingContext.PROFILE,
            OnboardingAnchorKey.PROFILE_STATISTICS,
            R.string.guidance_profile_statistics,
        ),
        step(
            OnboardingStepId.PROFILE_REPLAY,
            OnboardingChapter.PROFILE,
            OnboardingContext.PROFILE,
            OnboardingAnchorKey.PROFILE_REPLAY,
            R.string.guidance_profile_replay,
            targetAction = OnboardingTargetAction.PAUSE,
        ),
        step(
            OnboardingStepId.SPECIES_SUMMARY,
            OnboardingChapter.SPECIES_DETAILS,
            OnboardingContext.SPECIES_DETAILS,
            OnboardingAnchorKey.SPECIES_SUMMARY,
            R.string.guidance_species_summary,
        ),
        step(
            OnboardingStepId.SPECIES_CARE,
            OnboardingChapter.SPECIES_DETAILS,
            OnboardingContext.SPECIES_DETAILS,
            OnboardingAnchorKey.SPECIES_CARE,
            R.string.guidance_species_care,
        ),
        step(
            OnboardingStepId.SPECIES_FLOWERING,
            OnboardingChapter.SPECIES_DETAILS,
            OnboardingContext.SPECIES_DETAILS,
            OnboardingAnchorKey.SPECIES_FLOWERING,
            R.string.guidance_species_flowering,
            targetAction = OnboardingTargetAction.ADVANCE,
            conditional = true,
            skipWhenUnavailable = true,
        ),
        step(
            OnboardingStepId.SPECIES_NATIVE_RANGE,
            OnboardingChapter.SPECIES_DETAILS,
            OnboardingContext.SPECIES_DETAILS,
            OnboardingAnchorKey.SPECIES_NATIVE_RANGE,
            R.string.guidance_species_native_range,
            targetAction = OnboardingTargetAction.ADVANCE,
            conditional = true,
            skipWhenUnavailable = true,
        ),
        step(
            OnboardingStepId.COMMUNITY_SCOPE,
            OnboardingChapter.COMMUNITY,
            OnboardingContext.COMMUNITY,
            OnboardingAnchorKey.COMMUNITY_SCOPE,
            R.string.guidance_community_scope,
        ),
        step(
            OnboardingStepId.COMMUNITY_FEED,
            OnboardingChapter.COMMUNITY,
            OnboardingContext.COMMUNITY,
            OnboardingAnchorKey.COMMUNITY_FEED,
            R.string.guidance_community_feed,
        ),
    )

    private val byId = steps.associateBy(OnboardingStep::id)
    private val byChapter = steps.groupBy(OnboardingStep::chapter)

    fun step(id: OnboardingStepId): OnboardingStep = checkNotNull(byId[id])

    fun steps(chapter: OnboardingChapter): List<OnboardingStep> = byChapter.getValue(chapter)

    fun firstStep(chapter: OnboardingChapter): OnboardingStepId = steps(chapter).first().id

    private fun step(
        id: OnboardingStepId,
        chapter: OnboardingChapter,
        context: OnboardingContext,
        anchor: OnboardingAnchorKey,
        bodyRes: Int,
        targetAction: OnboardingTargetAction = OnboardingTargetAction.NONE,
        forwardUsesTarget: Boolean = false,
        dimBackground: Boolean = true,
        conditional: Boolean = false,
        skipWhenUnavailable: Boolean = false,
        inlineLinkTextRes: Int? = null,
    ) = OnboardingStep(
        id = id,
        chapter = chapter,
        context = context,
        anchor = anchor,
        titleRes = when (chapter) {
            OnboardingChapter.HOME_SCAN -> R.string.guidance_title_home_scan
            OnboardingChapter.GARDEN,
            OnboardingChapter.GARDEN_SCAN_ATTACH,
            -> R.string.guidance_title_garden
            OnboardingChapter.GARDEN_PLANT_DETAILS -> R.string.guidance_title_garden_plant
            OnboardingChapter.GALLERY -> R.string.guidance_title_gallery
            OnboardingChapter.PROFILE -> R.string.guidance_title_profile
            OnboardingChapter.SPECIES_DETAILS -> R.string.guidance_title_species
            OnboardingChapter.COMMUNITY -> R.string.guidance_title_community
        },
        bodyRes = bodyRes,
        inlineLinkTextRes = inlineLinkTextRes,
        targetAction = targetAction,
        forwardUsesTarget = forwardUsesTarget,
        dimBackground = dimBackground,
        conditional = conditional,
        skipWhenUnavailable = skipWhenUnavailable,
    )
}
