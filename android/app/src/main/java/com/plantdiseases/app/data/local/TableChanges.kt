package com.plantdiseases.app.data.local

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.map

/**
 * Emits once on collection, then again after every committed write to any of
 * [tables]. Screens that read several tables (the garden's plants, rooms and
 * today's tasks) collect this and re-read, instead of re-reading on resume and
 * after each of their own writes. Bursts are conflated: a slow re-read sees
 * the latest state, not every intermediate one.
 *
 * Re-read on every emission, the first included: Room starts tracking the
 * tables when a query runs, so the re-read is also what guarantees a write
 * after it is seen.
 */
fun AppDatabase.changes(vararg tables: String): Flow<Unit> =
    invalidationTracker.createFlow(*tables).map { }.conflate()

/** [changes] over every table the garden tab reads. */
fun AppDatabase.gardenChanges(): Flow<Unit> = changes(
    "plants",
    "rooms",
    "scans",
    "plant_journal",
    "plant_reminders",
    "plant_reminder_events",
    "treatment_plans",
    "treatment_steps",
)
