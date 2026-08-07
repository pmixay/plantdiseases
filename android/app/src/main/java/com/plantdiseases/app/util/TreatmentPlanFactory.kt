package com.plantdiseases.app.util

import com.plantdiseases.app.data.local.TreatmentPlanEntity
import com.plantdiseases.app.data.local.TreatmentStepEntity
import com.plantdiseases.app.data.model.CarePlanResponse
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/**
 * Turns a `/api/care-plan` response into the rows that back the plant's
 * treatment checklist. Pure: takes the DTO + a start time, returns entities;
 * persists nothing (CareRepository.startTreatmentPlan does the writing).
 *
 * The server already produces the treatment text (ai_services.py
 * generate_care_plan) — this factory never invents steps, only splits and
 * schedules the ones the server returned.
 */
object TreatmentPlanFactory {

    /** Days between steps when the plan carries no checkpoint for that index. */
    const val STEP_SPACING_DAYS = 3

    data class Built(val plan: TreatmentPlanEntity, val steps: List<TreatmentStepEntity>)

    /** Null when the plan carries no treatment steps (a healthy verdict) —
     *  the caller then creates NO plan at all rather than an empty one. */
    fun build(
        plan: CarePlanResponse,
        plantId: Long,
        scanId: Long?,
        diseaseKey: String,
        diseaseLabelEn: String,
        diseaseLabelRu: String,
        startedAtMs: Long,
    ): Built? {
        val treatment = plan.treatment
        if (treatment.isNullOrEmpty()) return null

        // Checkpoints are (day, check) pairs with no explicit step index in
        // the wire format — map by POSITION: the i-th checkpoint overrides
        // the i-th step's due day, same convention the mockup's "Step 2 of 5
        // · Fungicide" progression implies (checkpoints walk the same steps
        // in order).
        val checkpointByIndex = plan.checkpoints.orEmpty()
            .mapIndexed { index, checkpoint -> index to checkpoint.day }
            .filter { it.second > 0 }
            .toMap()

        // Full product objects, not just keys — see TreatmentStepEntity's
        // kdoc for why the app can't resolve a bare key against a
        // catalogue it doesn't ship offline.
        val productsJson = plan.products
            ?.filter { it.key.isNotBlank() }
            ?.takeIf { it.isNotEmpty() }
            ?.let { products ->
                val array = JSONArray()
                products.forEach { p ->
                    array.put(
                        JSONObject().apply {
                            put("key", p.key)
                            put("kind", p.kind)
                            put("name", p.name)
                            put("active_ingredient", p.activeIngredient)
                            p.note?.let { put("note", it) }
                        }
                    )
                }
                array.toString()
            }

        val lastIndex = treatment.lastIndex
        val steps = treatment.mapIndexed { index, text ->
            val (title, detail) = splitTitleAndDetail(text)
            var dueAt = dueAtForIndex(startedAtMs, index, checkpointByIndex)
            // The follow-up-day floor is a FALLBACK for when nothing else pins
            // the final step's timing. An explicit checkpoint on that step is
            // the server's own re-check day and takes precedence over it.
            if (index == lastIndex && index !in checkpointByIndex) {
                val followUpDueAt = addDays(startedAtMs, plan.followUpDays)
                if (followUpDueAt > dueAt) dueAt = followUpDueAt
            }
            TreatmentStepEntity(
                planId = 0, // patched by the caller after the plan row's id is known
                stepIndex = index,
                title = title,
                detail = detail,
                bullets = bulletsFor(index, detail ?: text, plan),
                productsJson = if (index == 0) productsJson else null,
                dueAt = dueAt,
            )
        }

        val conditionsJson = plan.treatmentConditions?.let { conditions ->
            JSONObject().apply {
                put("best_time", conditions.bestTime)
                conditions.tempC?.let { put("temp_c", JSONArray(it)) }
                conditions.dryHours?.let { put("dry_hours", it) }
            }.toString()
        }

        return Built(
            plan = TreatmentPlanEntity(
                plantId = plantId,
                scanId = scanId,
                diseaseKey = diseaseKey,
                diseaseLabelEn = diseaseLabelEn,
                diseaseLabelRu = diseaseLabelRu,
                startedAt = startedAtMs,
                status = "active",
                conditionsJson = conditionsJson,
            ),
            steps = steps,
        )
    }

    private fun dueAtForIndex(startedAtMs: Long, index: Int, checkpointByIndex: Map<Int, Int>): Long {
        val days = checkpointByIndex[index] ?: (index * STEP_SPACING_DAYS)
        return addDays(startedAtMs, days)
    }

    /** Adds [days] to [startedAtMs] and re-bases onto 12:00 local — the
     *  mockup's midday treatment slot (matches CareQueue's reminder
     *  time-of-day handling). */
    private fun addDays(startedAtMs: Long, days: Int): Long {
        val cal = Calendar.getInstance().apply {
            timeInMillis = startedAtMs
            add(Calendar.DAY_OF_MONTH, days)
            set(Calendar.HOUR_OF_DAY, 12)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    /** Splits at the first sentence boundary (. ; or 60 chars, whichever
     *  comes first). Returns (title, detail) where detail is null when it
     *  would just repeat the title verbatim. */
    private fun splitTitleAndDetail(text: String): Pair<String, String?> {
        val trimmed = text.trim()
        val dot = trimmed.indexOf('.').let { if (it in 1 until trimmed.length - 1) it else -1 }
        val semi = trimmed.indexOf(';').let { if (it in 1 until trimmed.length - 1) it else -1 }
        val boundary = listOf(dot, semi, 60)
            .filter { it > 0 }
            .filter { it < trimmed.length }
            .minOrNull()
        val title = if (boundary != null) trimmed.take(boundary).trim().trimEnd('.', ';') else trimmed
        return if (title == trimmed) title to null else title to trimmed
    }

    private fun bulletsFor(index: Int, detailOrText: String, plan: CarePlanResponse): String? {
        if (index == 0) {
            val sanitation = plan.sanitation?.filter { it.isNotBlank() }
            if (!sanitation.isNullOrEmpty()) return JSONArray(sanitation).toString()
        }
        val fragments = detailOrText.split("; ").map { it.trim() }.filter { it.isNotEmpty() }
        return if (fragments.size >= 2) JSONArray(fragments).toString() else null
    }
}
