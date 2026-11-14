package com.plantdiseases.app.util

import android.app.Application
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.CarePlanCheckpoint
import com.plantdiseases.app.data.model.CarePlanResponse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.Calendar

// TreatmentPlanFactory builds bullets/products via org.json.JSONArray, which
// on the raw Android stub jar silently returns null (isReturnDefaultValues =
// true) instead of throwing — Robolectric is required to get a real
// implementation, not just to avoid a crash.
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class TreatmentPlanFactoryTest {

    private fun startedAt(): Long = Calendar.getInstance().apply {
        set(2026, Calendar.AUGUST, 1, 9, 0, 0)
        set(Calendar.MILLISECOND, 0)
    }.timeInMillis

    @Test
    fun build_fiveStepPlan_producesFiveRowsWithIncreasingDueDates() {
        val plan = CarePlanResponse(
            treatment = listOf("Remove infected leaves.", "Apply fungicide.", "Improve airflow.", "Reduce watering.", "Re-check foliage."),
            followUpDays = 14,
        )
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = 10,
            diseaseKey = "botrytis",
            diseaseLabelEn = "Grey mould",
            diseaseLabelRu = "Серая гниль",
            startedAtMs = startedAt(),
        )!!

        assertThat(built.steps).hasSize(5)
        assertThat(built.steps.map { it.stepIndex }).isEqualTo(listOf(0, 1, 2, 3, 4))
        val dueAts = built.steps.map { it.dueAt }
        assertThat(dueAts).isEqualTo(dueAts.sorted())
        assertThat(dueAts.toSet()).hasSize(5)
    }

    @Test
    fun build_emptyTreatmentList_returnsNull() {
        val plan = CarePlanResponse(treatment = emptyList())
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "healthy",
            diseaseLabelEn = "Healthy",
            diseaseLabelRu = "Здорово",
            startedAtMs = startedAt(),
        )
        assertThat(built).isNull()
    }

    @Test
    fun build_nullTreatmentList_returnsNull() {
        val plan = CarePlanResponse(treatment = null)
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "healthy",
            diseaseLabelEn = "Healthy",
            diseaseLabelRu = "Здорово",
            startedAtMs = startedAt(),
        )
        assertThat(built).isNull()
    }

    @Test
    fun build_followUpDoesNotDelayAnUnrelatedTreatmentAction() {
        val plan = CarePlanResponse(
            treatment = listOf("A.", "B.", "C.", "D.", "E."),
            followUpDays = 14,
        )
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "botrytis",
            diseaseLabelEn = "Grey mould",
            diseaseLabelRu = "Серая гниль",
            startedAtMs = startedAt(),
        )!!

        val started = startedAt()
        val expectedDay12 = Calendar.getInstance().apply {
            timeInMillis = started
            add(Calendar.DAY_OF_MONTH, 12)
            set(Calendar.HOUR_OF_DAY, 12)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis
        val lastStep = built.steps.last()
        assertThat(lastStep.dueAt).isEqualTo(expectedDay12)
    }

    @Test
    fun build_checkpointsBecomeSeparateRecheckTasksAtTheirOwnDays() {
        val plan = CarePlanResponse(
            treatment = listOf("A.", "B.", "C."),
            checkpoints = listOf(CarePlanCheckpoint(day = 0, check = "start"), CarePlanCheckpoint(day = 1, check = "x"), CarePlanCheckpoint(day = 7, check = "recheck")),
            followUpDays = 14,
        )
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "botrytis",
            diseaseLabelEn = "Grey mould",
            diseaseLabelRu = "Серая гниль",
            startedAtMs = startedAt(),
        )!!

        val started = startedAt()
        assertThat(built.steps).hasSize(6)
        val expectedDay7 = Calendar.getInstance().apply {
            timeInMillis = started
            add(Calendar.DAY_OF_MONTH, 7)
            set(Calendar.HOUR_OF_DAY, 12)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis
        val recheck = built.steps.single { it.title == "recheck" }
        assertThat(recheck.dueAt).isEqualTo(expectedDay7)
        assertThat(built.steps.map { it.stepIndex }).isEqualTo((0..5).toList())
        assertThat(built.steps.map { it.dueAt }).isEqualTo(built.steps.map { it.dueAt }.sorted())
    }

    @Test
    fun build_stepTextWithNoSentenceBreak_titleEqualsTextAndDetailIsNull() {
        val plan = CarePlanResponse(treatment = listOf("Water"))
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "healthy",
            diseaseLabelEn = "Healthy",
            diseaseLabelRu = "Здорово",
            startedAtMs = startedAt(),
        )!!

        val step = built.steps.single()
        assertThat(step.title).isEqualTo("Water")
        assertThat(step.detail).isNull()
    }

    @Test
    fun build_longSentenceTruncatesAtBoundaryAndKeepsFullTextAsDetail() {
        val text = "Remove all visibly infected leaves and stems, disposing of them away from the plant."
        val plan = CarePlanResponse(treatment = listOf(text))
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "botrytis",
            diseaseLabelEn = "Grey mould",
            diseaseLabelRu = "Серая гниль",
            startedAtMs = startedAt(),
        )!!

        val step = built.steps.single()
        assertThat(step.title).isNotEqualTo(text)
        assertThat(step.detail).isEqualTo(text)
        assertThat(step.title.length).isAtMost(60)
    }

    @Test
    fun build_step0SanitationBulletsAreUsedVerbatim() {
        val plan = CarePlanResponse(
            treatment = listOf("Remove infected leaves."),
            sanitation = listOf("Disinfect pruning shears", "Discard debris in sealed bag"),
        )
        val built = TreatmentPlanFactory.build(
            plan,
            plantId = 1,
            scanId = null,
            diseaseKey = "botrytis",
            diseaseLabelEn = "Grey mould",
            diseaseLabelRu = "Серая гниль",
            startedAtMs = startedAt(),
        )!!

        assertThat(built.steps.single().bullets).contains("Disinfect pruning shears")
    }
}
