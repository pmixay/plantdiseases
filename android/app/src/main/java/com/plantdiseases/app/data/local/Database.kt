package com.plantdiseases.app.data.local

import androidx.room.*

@Entity(
    tableName = "plants",
    indices = [Index(value = ["created_at"]), Index(value = ["room_id"])]
)
data class PlantEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "name") val name: String,
    /** Free-text species. Stays in sync with `commonName` when the user
     *  accepts a PlantNet suggestion; the canonical Latin name lives
     *  in `scientificName`. */
    @ColumnInfo(name = "species") val species: String? = null,
    /** Optional user-picked cover photo (content:// URI). When null the
     *  garden card and plant-detail hero fall back to the latest scan
     *  image; when both are null they fall back to the default icon. */
    @ColumnInfo(name = "cover_photo_uri") val coverPhotoUri: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    /** Latin / scientific name as returned by PlantNet (e.g. "Ficus elastica"). */
    @ColumnInfo(name = "scientific_name") val scientificName: String? = null,
    /** Localised common name (e.g. "Rubber Plant"). */
    @ColumnInfo(name = "common_name") val commonName: String? = null,
    /** Free-text room / location (e.g. "Living room", "Bathroom"). Garden
     *  list groups by this when populated; null plants land in an
     *  "Unassigned" group at the bottom. Re-added by MIGRATION_5_6 after
     *  MIGRATION_4_5 previously dropped it. */
    @ColumnInfo(name = "room") val room: String? = null,
    /** FK into `rooms` (no ForeignKey constraint: deleting a room must NOT
     *  cascade-delete plants, so RoomRepository nulls this column instead).
     *  Replaces the free-text `room` column above, which MIGRATION_6_7
     *  backfills from and then leaves in place, unread, since SQLite has
     *  no cheap DROP COLUMN. NULL = unassigned. */
    @ColumnInfo(name = "room_id") val roomId: Long? = null,
    /** Most recent peak lux reading from the Light Meter, captured
     *  on a session launched WITH this plant's context. Null until
     *  the user runs the meter from plant detail. */
    @ColumnInfo(name = "measured_lux") val measuredLux: Int? = null,
    @ColumnInfo(name = "measured_lux_at") val measuredLuxAt: Long? = null,
)

@Entity(
    tableName = "plant_journal",
    foreignKeys = [
        ForeignKey(
            entity = PlantEntity::class,
            parentColumns = ["id"],
            childColumns = ["plant_id"],
            onDelete = ForeignKey.CASCADE,
        )
    ],
    indices = [
        Index(value = ["plant_id"]),
        Index(value = ["timestamp"]),
    ]
)
data class PlantJournalEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    @ColumnInfo(name = "type") val type: String,
    @ColumnInfo(name = "text") val text: String? = null,
    @ColumnInfo(name = "photo_uri") val photoUri: String? = null,
    @ColumnInfo(name = "timestamp") val timestamp: Long = System.currentTimeMillis(),
)

@Entity(
    tableName = "scans",
    foreignKeys = [
        ForeignKey(
            entity = PlantEntity::class,
            parentColumns = ["id"],
            childColumns = ["plant_id"],
            onDelete = ForeignKey.SET_NULL,
        )
    ],
    indices = [
        Index(value = ["timestamp"]),
        Index(value = ["is_healthy"]),
        Index(value = ["plant_id"]),
    ]
)
data class ScanEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    // Opaque, randomly-generated client id sent to the server for any
    // per-scan signal (feedback, future progression-pinning, etc.).
    // Decoupled from the autoincrement primary key so the server can
    // never correlate ids across users by counting. Populated at
    // insertion in ScanRepository.saveScan().
    @ColumnInfo(name = "client_scan_id") val clientScanId: String = "",
    @ColumnInfo(name = "image_path") val imagePath: String,
    @ColumnInfo(name = "disease_name") val diseaseName: String,
    @ColumnInfo(name = "disease_name_ru") val diseaseNameRu: String,
    @ColumnInfo(name = "confidence") val confidence: Float,
    @ColumnInfo(name = "description") val description: String,
    @ColumnInfo(name = "description_ru") val descriptionRu: String,
    @ColumnInfo(name = "treatment") val treatment: String, // JSON array
    @ColumnInfo(name = "treatment_ru") val treatmentRu: String, // JSON array
    @ColumnInfo(name = "prevention") val prevention: String, // JSON array
    @ColumnInfo(name = "prevention_ru") val preventionRu: String, // JSON array
    @ColumnInfo(name = "is_healthy") val isHealthy: Boolean,
    @ColumnInfo(name = "timestamp") val timestamp: Long = System.currentTimeMillis(),
    // All bounding boxes from the YOLOv8 detector, serialised as a JSON
    // array of {x, y, width, height, class, confidence}.
    @ColumnInfo(name = "regions") val regions: String? = null,
    // Index into `regions` that Stage 2 used as the primary crop (-1 if none).
    @ColumnInfo(name = "primary_region_index") val primaryRegionIndex: Int? = null,
    // Pixel dims of the image the server actually saw (post-upload-decode).
    // ``regions`` coordinates live in this space, so the overlay renderer
    // must scale against these instead of the raw camera file (which is
    // typically larger and EXIF-rotated).
    @ColumnInfo(name = "detection_image_width") val detectionImageWidth: Int = 0,
    @ColumnInfo(name = "detection_image_height") val detectionImageHeight: Int = 0,
    // Per-class probabilities from the classifier (JSON map).
    @ColumnInfo(name = "all_probs") val allProbs: String? = null,
    // Diagnostic flags from the pipeline post-processing rules
    // (e.g. "uncertain_healthy", "detector_classifier_mismatch"),
    // serialised as a JSON array of strings.
    @ColumnInfo(name = "warnings") val warnings: String? = null,
    // Optional foreign key into `plants`. Null until the user assigns the
    // scan to a plant from the result screen ("Add to garden" flow).
    @ColumnInfo(name = "plant_id") val plantId: Long? = null,
    // Pet-toxicity surface from /api/analyze. `toxicitySource` is "db"
    // when the server matched the species, "unknown" otherwise. UI
    // must NOT falsely reassure on "unknown".
    @ColumnInfo(name = "toxic_to_pets") val toxicToPets: Boolean = false,
    @ColumnInfo(name = "toxic_species_list") val toxicSpeciesList: String? = null, // JSON array
    @ColumnInfo(name = "toxicity_source") val toxicitySource: String = "unknown",
    // Server-side PlantNet fan-out result. Null when PlantNet wasn't
    // configured or returned no candidates. Persisted at insertion
    // time so the result screen can render the species card without a
    // second network round-trip.
    @ColumnInfo(name = "species_scientific") val speciesScientific: String? = null,
    @ColumnInfo(name = "species_common") val speciesCommon: String? = null,
    @ColumnInfo(name = "species_score") val speciesScore: Float? = null,
    // Conformal prediction set the server returned at /api/analyse
    // time (JSON array of class keys). Persisted here so feedback
    // submission later — even from a stale gallery row — can echo it
    // back to the server, enabling the "was the right answer in the
    // 90 % set?" diagnostic.
    @ColumnInfo(name = "prediction_set") val predictionSet: String? = null,
    // Canonical class key the server returned for this scan (e.g.
    // ``leaf_spot``). Persisted because ``argmax(all_probs)``
    // can disagree after the server's weak-healthy reroute, and
    // feedback submission must echo the DISPLAYED verdict — not
    // the argmax — to keep per-class accuracy metrics honest.
    @ColumnInfo(name = "displayed_class_key") val displayedClassKey: String? = null,
    @ColumnInfo(name = "severity") val severity: String = "low",
)

// ─── Plant Reminder Entity ──────────────────────────────────────────────────

@Entity(
    tableName = "plant_reminders",
    foreignKeys = [
        ForeignKey(
            entity = PlantEntity::class,
            parentColumns = ["id"],
            childColumns = ["plant_id"],
            onDelete = ForeignKey.CASCADE,
        )
    ],
    indices = [
        Index(value = ["plant_id"]),
        // Was unique up to schema v3 — dropped in MIGRATION_3_4 so a
        // plant can hold several generic ("custom") reminders. The
        // legacy preset types are still kept one-per-plant at the DAO
        // level via upsertByPlantAndType.
        Index(value = ["plant_id", "type"]),
    ]
)
data class PlantReminderEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    /** Semantic key. The legacy presets keep their historical values
     *  ("watering" | "check" | "fertilize") because care-plan apply and
     *  the "mark watered/fertilised" quick-log re-anchor by type; newer
     *  presets store their preset key and free-form reminders store
     *  "custom". */
    @ColumnInfo(name = "type") val type: String,
    @ColumnInfo(name = "interval_days") val intervalDays: Int = 7,
    @ColumnInfo(name = "hour") val hour: Int = 9,
    @ColumnInfo(name = "minute") val minute: Int = 0,
    @ColumnInfo(name = "enabled") val enabled: Boolean = false,
    /** User-visible name. NULL on rows written before schema v4 — the
     *  UI derives a display label from [type] via ReminderPresets. */
    @ColumnInfo(name = "label") val label: String? = null,
    /** Optional free-text note, doubles as the notification body. */
    @ColumnInfo(name = "description") val description: String? = null,
    /** Preset icon key (see ReminderPresets). NULL → derive from [type]. */
    @ColumnInfo(name = "icon") val icon: String? = null,
)

@Dao
abstract class PlantReminderDao {
    @Query("SELECT * FROM plant_reminders WHERE plant_id = :plantId")
    abstract suspend fun getForPlant(plantId: Long): List<PlantReminderEntity>

    @Query("SELECT * FROM plant_reminders WHERE plant_id = :plantId AND type = :type LIMIT 1")
    abstract suspend fun getByPlantAndType(plantId: Long, type: String): PlantReminderEntity?

    @Query("SELECT * FROM plant_reminders WHERE id = :id LIMIT 1")
    abstract suspend fun getById(id: Long): PlantReminderEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun upsert(reminder: PlantReminderEntity): Long

    @Query("DELETE FROM plant_reminders WHERE id = :id")
    abstract suspend fun deleteById(id: Long)

    /** Read-then-write merge of a (plant_id, type) reminder inside a
     *  single Room transaction. Two coroutines hitting saveReminder on
     *  the same (plant_id, type) used to race — both read existing=null
     *  and inserted, leaving an orphan row that the UI could
     *  partially address. Wrapping the read+upsert in @Transaction
     *  serialises them at the SQLite write-lock level. */
    @androidx.room.Transaction
    open suspend fun upsertByPlantAndType(entity: PlantReminderEntity): Long {
        val existing = getByPlantAndType(entity.plantId, entity.type)
        // Merge-by-type callers (care-plan defaults, adaptive watering,
        // follow-up CTA) only carry scheduling fields — preserve any
        // user-customised label/description/icon already on the row.
        return upsert(
            entity.copy(
                id = existing?.id ?: entity.id,
                label = entity.label ?: existing?.label,
                description = entity.description ?: existing?.description,
                icon = entity.icon ?: existing?.icon,
            )
        )
    }
}

// ─── Reminder event log ──────────────────────────────────────────────
//
// Persists every reminder firing + its disposition (pending / done /
// snoozed / skipped). The plant-detail "Reminders" surface reads this
// to show a streak ("last watered 3 days ago · 5-day streak"); the
// notification-action receivers write into it when the user taps
// Done / Snooze on a fired notification. ``fired_at`` is the epoch
// when the WorkManager worker ran; ``done_at`` is set on disposition.

@Entity(
    tableName = "plant_reminder_events",
    foreignKeys = [
        ForeignKey(
            entity = PlantReminderEntity::class,
            parentColumns = ["id"],
            childColumns = ["reminder_id"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [
        Index(value = ["reminder_id"]),
        Index(value = ["plant_id", "fired_at"]),
    ],
)
data class PlantReminderEventEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "reminder_id") val reminderId: Long,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    @ColumnInfo(name = "type") val type: String,
    @ColumnInfo(name = "fired_at") val firedAt: Long,
    /** "pending" | "done" | "snoozed" | "skipped". A "done" event also
     *  triggers an auto-created journal entry of the matching type. */
    @ColumnInfo(name = "action") val action: String = "pending",
    @ColumnInfo(name = "done_at") val doneAt: Long? = null,
)

@Dao
interface PlantReminderEventDao {
    @Insert
    suspend fun insert(event: PlantReminderEventEntity): Long

    @Update
    suspend fun update(event: PlantReminderEventEntity)

    /** Undo path for CareRepository.complete(): a day-view "Done" tap on a
     *  recurring occurrence has no prior notification-fired row to revert to
     *  pending (unlike the notification action receivers, which always
     *  resolve an existing row) — it inserts a fresh done event, so undoing
     *  it means deleting exactly that row. */
    @Query("DELETE FROM plant_reminder_events WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("SELECT * FROM plant_reminder_events WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): PlantReminderEventEntity?

    @Query(
        """
        SELECT * FROM plant_reminder_events
        WHERE plant_id = :plantId
        ORDER BY fired_at DESC
        LIMIT :limit
    """
    )
    suspend fun recentForPlant(plantId: Long, limit: Int = 50): List<PlantReminderEventEntity>

    @Query(
        """
        SELECT MAX(done_at) FROM plant_reminder_events
        WHERE plant_id = :plantId AND type = :type AND action = 'done'
    """
    )
    suspend fun lastDoneAt(plantId: Long, type: String): Long?
}

// ─── Chat Message Entity ─────────────────────────────────────────────────────

@Entity(
    tableName = "chat_messages",
    foreignKeys = [
        ForeignKey(
            entity = PlantEntity::class,
            parentColumns = ["id"],
            childColumns = ["plant_id"],
            onDelete = ForeignKey.CASCADE,
        )
    ],
    indices = [Index(value = ["plant_id"])]
)
data class ChatMessageEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    @ColumnInfo(name = "role") val role: String, // "user" | "bot"
    @ColumnInfo(name = "content") val content: String,
    @ColumnInfo(name = "timestamp") val timestamp: Long = System.currentTimeMillis(),
)

@Dao
interface ChatMessageDao {
    @Query("SELECT * FROM chat_messages WHERE plant_id = :plantId ORDER BY timestamp ASC")
    suspend fun getForPlant(plantId: Long): List<ChatMessageEntity>

    @Insert
    suspend fun insert(message: ChatMessageEntity): Long

    @Query("DELETE FROM chat_messages WHERE plant_id = :plantId")
    suspend fun deleteForPlant(plantId: Long)
}

// ─── Scan Chat Message Entity ────────────────────────────────────────────────
//
// Mirrors `chat_messages` for AI Plant Doctor sessions launched from a scan
// that isn't (yet) attached to a garden plant. Keyed by the scan row so the
// transcript survives closing/reopening the result screen; CASCADE keeps it
// tidy when the scan is deleted.

@Entity(
    tableName = "scan_chat_messages",
    foreignKeys = [
        ForeignKey(
            entity = ScanEntity::class,
            parentColumns = ["id"],
            childColumns = ["scan_id"],
            onDelete = ForeignKey.CASCADE,
        )
    ],
    indices = [Index(value = ["scan_id"])]
)
data class ScanChatMessageEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "scan_id") val scanId: Long,
    @ColumnInfo(name = "role") val role: String, // "user" | "bot"
    @ColumnInfo(name = "content") val content: String,
    @ColumnInfo(name = "timestamp") val timestamp: Long = System.currentTimeMillis(),
)

@Dao
interface ScanChatMessageDao {
    @Query("SELECT * FROM scan_chat_messages WHERE scan_id = :scanId ORDER BY timestamp ASC")
    suspend fun getForScan(scanId: Long): List<ScanChatMessageEntity>

    @Insert
    suspend fun insert(message: ScanChatMessageEntity): Long

    @Query("DELETE FROM scan_chat_messages WHERE scan_id = :scanId")
    suspend fun deleteForScan(scanId: Long)
}

// ─── Original DAOs ───────────────────────────────────────────────────────────

@Dao
interface ScanDao {
    @Query("SELECT * FROM scans ORDER BY timestamp DESC")
    suspend fun getAllScans(): List<ScanEntity>

    @Query("SELECT * FROM scans ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getRecentScans(limit: Int): List<ScanEntity>

    @Query("SELECT * FROM scans WHERE id = :id")
    suspend fun getScanById(id: Long): ScanEntity?

    @Query("SELECT * FROM scans WHERE client_scan_id = :clientScanId LIMIT 1")
    suspend fun getScanByClientScanId(clientScanId: String): ScanEntity?

    @Query("SELECT client_scan_id FROM scans WHERE client_scan_id != ''")
    suspend fun allClientScanIds(): List<String>

    @Query("SELECT * FROM scans WHERE plant_id = :plantId ORDER BY timestamp DESC")
    suspend fun getScansForPlant(plantId: Long): List<ScanEntity>

    @Insert
    suspend fun insert(scan: ScanEntity): Long

    @Query("UPDATE scans SET plant_id = :plantId WHERE id = :scanId")
    suspend fun assignScanToPlant(scanId: Long, plantId: Long?)

    @Query("DELETE FROM scans WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("DELETE FROM scans")
    suspend fun deleteAll()
}

@Dao
interface PlantDao {
    @Query("SELECT * FROM plants ORDER BY created_at DESC")
    suspend fun getAllPlants(): List<PlantEntity>

    @Query("SELECT * FROM plants WHERE id = :id")
    suspend fun getPlantById(id: Long): PlantEntity?

    @Insert
    suspend fun insert(plant: PlantEntity): Long

    @Update
    suspend fun update(plant: PlantEntity)

    @Query("DELETE FROM plants WHERE id = :id")
    suspend fun deleteById(id: Long)
}

@Dao
interface PlantJournalDao {
    @Query("SELECT * FROM plant_journal WHERE plant_id = :plantId ORDER BY timestamp DESC")
    suspend fun getEntriesForPlant(plantId: Long): List<PlantJournalEntity>

    @Query("SELECT * FROM plant_journal WHERE id = :id")
    suspend fun getById(id: Long): PlantJournalEntity?

    @Insert
    suspend fun insert(entry: PlantJournalEntity): Long

    @Update
    suspend fun update(entry: PlantJournalEntity)

    @Query("DELETE FROM plant_journal WHERE id = :id")
    suspend fun deleteById(id: Long)
}

// ─── Room Entity ──────────────────────────────────────────────────────────
//
// A plant's location. First-class rather than the free-text `plants.room`
// column: the Garden refactor makes rooms creatable/renameable/deletable
// objects that own a plant collection and (later) a light baseline, none of
// which a plain string column supports without a rewrite on every rename.

@Entity(tableName = "rooms", indices = [Index(value = ["name"], unique = true)])
data class RoomEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "name") val name: String,
    /** RoomPresets key (living/bedroom/kitchen/office/balcony/bathroom/other).
     *  Drives the room card icon; NULL renders the generic ic_room. */
    @ColumnInfo(name = "icon") val icon: String? = null,
    /** Optional per-room light baseline in lux, seeded by the Light Meter.
     *  NULL until measured. Not read by anything yet — reserved for the
     *  room-level care defaults ROADMAP §4 describes. */
    @ColumnInfo(name = "baseline_lux") val baselineLux: Int? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "sort_order") val sortOrder: Int = 0,
)

@Dao
interface RoomDao {
    @Query("SELECT * FROM rooms ORDER BY sort_order ASC, name ASC")
    suspend fun getAll(): List<RoomEntity>

    @Query("SELECT * FROM rooms WHERE id = :id")
    suspend fun getById(id: Long): RoomEntity?

    @Query("SELECT * FROM rooms WHERE name = :name COLLATE NOCASE LIMIT 1")
    suspend fun findByName(name: String): RoomEntity?

    @Insert
    suspend fun insert(room: RoomEntity): Long

    @Update
    suspend fun update(room: RoomEntity)

    @Query("DELETE FROM rooms WHERE id = :id")
    suspend fun deleteById(id: Long)
}

// ─── Treatment Plan / Step Entities ────────────────────────────────────────
//
// Materialises the treatment checklist a disease-diagnosing scan produces
// (server `/api/care-plan` -> `treatment: List<String>`) so its steps can be
// tracked over the recovery window instead of being read once and forgotten.
// See util/TreatmentPlanFactory.kt for the DTO -> rows construction.

@Entity(
    tableName = "treatment_plans",
    foreignKeys = [
        ForeignKey(
            entity = PlantEntity::class,
            parentColumns = ["id"],
            childColumns = ["plant_id"],
            onDelete = ForeignKey.CASCADE,
        ),
        ForeignKey(
            entity = ScanEntity::class,
            parentColumns = ["id"],
            childColumns = ["scan_id"],
            onDelete = ForeignKey.SET_NULL,
        ),
    ],
    indices = [
        Index(value = ["plant_id"]),
        Index(value = ["scan_id"]),
        Index(value = ["plant_id", "status"]),
    ],
)
data class TreatmentPlanEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    /** The scan that diagnosed it. NULL if that scan is later deleted
     *  (ON DELETE SET NULL) — the plan and its history survive. */
    @ColumnInfo(name = "scan_id") val scanId: Long? = null,
    /** Canonical model class key (e.g. "botrytis"). Keys the product
     *  catalogue lookup and the step icon. */
    @ColumnInfo(name = "disease_key") val diseaseKey: String,
    /** Both locales snapshotted at creation: the plan must render
     *  correctly after a language switch, and the offline device can't
     *  re-fetch /api/care-plan to re-derive the label later. */
    @ColumnInfo(name = "disease_label_en") val diseaseLabelEn: String,
    @ColumnInfo(name = "disease_label_ru") val diseaseLabelRu: String,
    @ColumnInfo(name = "started_at") val startedAt: Long = System.currentTimeMillis(),
    /** "active" | "completed" | "abandoned". At most one "active" row per
     *  plant — enforced by CareRepository.startTreatmentPlan, not by a
     *  DB constraint (SQLite partial unique indices are awkward via Room). */
    @ColumnInfo(name = "status") val status: String = "active",
    @ColumnInfo(name = "completed_at") val completedAt: Long? = null,
)

@Dao
interface TreatmentPlanDao {
    @Query("SELECT * FROM treatment_plans WHERE plant_id = :plantId AND status = 'active' LIMIT 1")
    suspend fun getActiveForPlant(plantId: Long): TreatmentPlanEntity?

    @Query("SELECT * FROM treatment_plans WHERE plant_id = :plantId ORDER BY started_at DESC")
    suspend fun getAllForPlant(plantId: Long): List<TreatmentPlanEntity>

    @Query("SELECT * FROM treatment_plans WHERE id = :id")
    suspend fun getById(id: Long): TreatmentPlanEntity?

    @Query("SELECT * FROM treatment_plans WHERE status = 'active'")
    suspend fun getAllActive(): List<TreatmentPlanEntity>

    @Insert
    suspend fun insert(plan: TreatmentPlanEntity): Long

    @Update
    suspend fun update(plan: TreatmentPlanEntity)
}

@Entity(
    tableName = "treatment_steps",
    foreignKeys = [
        ForeignKey(
            entity = TreatmentPlanEntity::class,
            parentColumns = ["id"],
            childColumns = ["plan_id"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [
        Index(value = ["plan_id"]),
        Index(value = ["due_at"]),
        Index(value = ["plan_id", "step_index"], unique = true),
    ],
)
data class TreatmentStepEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plan_id") val planId: Long,
    /** 0-based; the UI renders stepIndex + 1 ("Step 2 of 5"). */
    @ColumnInfo(name = "step_index") val stepIndex: Int,
    /** Short imperative title — the first sentence of care-plan
     *  `treatment[i]`. See TreatmentPlanFactory for the split rule. */
    @ColumnInfo(name = "title") val title: String,
    /** Full step text, or NULL when it's identical to `title` (nothing
     *  left to say twice). */
    @ColumnInfo(name = "detail") val detail: String? = null,
    /** JSON array of strings — the "What to do" checklist. */
    @ColumnInfo(name = "bullets") val bullets: String? = null,
    /** JSON array of product-catalogue keys (server `products[].key`). */
    @ColumnInfo(name = "product_keys") val productKeys: String? = null,
    @ColumnInfo(name = "due_at") val dueAt: Long,
    /** "pending" | "done" | "skipped" */
    @ColumnInfo(name = "status") val status: String = "pending",
    @ColumnInfo(name = "done_at") val doneAt: Long? = null,
)

@Dao
interface TreatmentStepDao {
    @Query("SELECT * FROM treatment_steps WHERE plan_id = :planId ORDER BY step_index ASC")
    suspend fun getForPlan(planId: Long): List<TreatmentStepEntity>

    @Query("SELECT * FROM treatment_steps WHERE id = :id")
    suspend fun getById(id: Long): TreatmentStepEntity?

    @Insert
    suspend fun insertAll(steps: List<TreatmentStepEntity>): List<Long>

    @Update
    suspend fun update(step: TreatmentStepEntity)

    @Query("SELECT * FROM treatment_steps WHERE status = 'pending' AND due_at < :cutoff")
    suspend fun pendingDueBefore(cutoff: Long): List<TreatmentStepEntity>

    /** Steps carry no `plant_id` of their own (only `plan_id`) — CareRepository's
     *  cross-garden queue needs the owning plant, hence the join. */
    @Query(
        """
        SELECT treatment_steps.*, treatment_plans.plant_id AS plant_id
        FROM treatment_steps
        JOIN treatment_plans ON treatment_steps.plan_id = treatment_plans.id
        WHERE treatment_steps.due_at >= :from AND treatment_steps.due_at < :to
        """
    )
    suspend fun getAllInRangeWithPlantId(from: Long, to: Long): List<StepWithPlantId>

    @Query("SELECT COUNT(*) FROM treatment_steps WHERE plan_id = :planId")
    suspend fun countForPlan(planId: Long): Int
}

data class StepWithPlantId(
    @Embedded val step: TreatmentStepEntity,
    @ColumnInfo(name = "plant_id") val plantId: Long,
)

// ─── One-off Care Task Entity ──────────────────────────────────────────────
//
// User-typed one-off tasks only. Recurring care lives in `plant_reminders`
// and is projected forward by util/CareQueue.kt — it is NOT materialised
// here. This table exists solely for tasks with nowhere else to live.

@Entity(
    tableName = "care_tasks",
    foreignKeys = [
        ForeignKey(
            entity = PlantEntity::class,
            parentColumns = ["id"],
            childColumns = ["plant_id"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index(value = ["plant_id"]), Index(value = ["due_at"])],
)
data class CareTaskEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    /** ReminderPresets key: watering / fertilize / check / mist / … / custom. */
    @ColumnInfo(name = "type") val type: String,
    @ColumnInfo(name = "title") val title: String,
    @ColumnInfo(name = "note") val note: String? = null,
    @ColumnInfo(name = "due_at") val dueAt: Long,
    @ColumnInfo(name = "status") val status: String = "pending",
    @ColumnInfo(name = "done_at") val doneAt: Long? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
)

@Dao
interface CareTaskDao {
    @Query("SELECT * FROM care_tasks WHERE plant_id = :plantId ORDER BY due_at ASC")
    suspend fun getForPlant(plantId: Long): List<CareTaskEntity>

    @Query("SELECT * FROM care_tasks WHERE id = :id")
    suspend fun getById(id: Long): CareTaskEntity?

    @Query("SELECT * FROM care_tasks WHERE due_at >= :from AND due_at < :to ORDER BY due_at ASC")
    suspend fun inRange(from: Long, to: Long): List<CareTaskEntity>

    @Insert
    suspend fun insert(task: CareTaskEntity): Long

    @Update
    suspend fun update(task: CareTaskEntity)

    @Query("DELETE FROM care_tasks WHERE id = :id")
    suspend fun deleteById(id: Long)
}

@Database(
    entities = [
        ScanEntity::class,
        PlantEntity::class,
        PlantJournalEntity::class,
        PlantReminderEntity::class,
        PlantReminderEventEntity::class,
        ChatMessageEntity::class,
        ScanChatMessageEntity::class,
        RoomEntity::class,
        TreatmentPlanEntity::class,
        TreatmentStepEntity::class,
        CareTaskEntity::class,
    ],
    // The schema is treated as ephemeral while the app is in pre-release
    // testing: ``fallbackToDestructiveMigration`` is configured below
    // and any schema change wipes local data on next launch. Real
    // migrations come later, alongside cloud sync.
    version = 7,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scanDao(): ScanDao
    abstract fun plantDao(): PlantDao
    abstract fun plantJournalDao(): PlantJournalDao
    abstract fun plantReminderDao(): PlantReminderDao
    abstract fun plantReminderEventDao(): PlantReminderEventDao
    abstract fun chatMessageDao(): ChatMessageDao
    abstract fun scanChatMessageDao(): ScanChatMessageDao
    abstract fun roomDao(): RoomDao
    abstract fun treatmentPlanDao(): TreatmentPlanDao
    abstract fun treatmentStepDao(): TreatmentStepDao
    abstract fun careTaskDao(): CareTaskDao

    companion object {
        @Volatile
        private var databaseInstance: AppDatabase? = null

        val MIGRATION_1_2 = object : androidx.room.migration.Migration(1, 2) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE scans ADD COLUMN severity TEXT NOT NULL DEFAULT 'low'")
            }
        }

        val MIGRATION_2_3 = object : androidx.room.migration.Migration(2, 3) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `scan_chat_messages` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `scan_id` INTEGER NOT NULL,
                        `role` TEXT NOT NULL,
                        `content` TEXT NOT NULL,
                        `timestamp` INTEGER NOT NULL,
                        FOREIGN KEY(`scan_id`) REFERENCES `scans`(`id`)
                            ON UPDATE NO ACTION ON DELETE CASCADE
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_scan_chat_messages_scan_id` " +
                        "ON `scan_chat_messages` (`scan_id`)"
                )
            }
        }

        /** Generic-reminder columns + drop the (plant_id, type) UNIQUE
         *  constraint so several "custom" reminders can coexist on a
         *  plant. Existing typed rows are untouched: label/description/
         *  icon stay NULL and the UI derives display data from `type`. */
        val MIGRATION_3_4 = object : androidx.room.migration.Migration(3, 4) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE plant_reminders ADD COLUMN label TEXT")
                db.execSQL("ALTER TABLE plant_reminders ADD COLUMN description TEXT")
                db.execSQL("ALTER TABLE plant_reminders ADD COLUMN icon TEXT")
                db.execSQL("DROP INDEX IF EXISTS `index_plant_reminders_plant_id_type`")
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_plant_reminders_plant_id_type` " +
                        "ON `plant_reminders` (`plant_id`, `type`)"
                )
            }
        }

        /** Drop the free-text `room` plant-location column. SQLite has no
         *  portable DROP COLUMN, so rebuild `plants` without it and copy every
         *  other column over — existing gardens are preserved (no data wipe). */
        val MIGRATION_4_5 = object : androidx.room.migration.Migration(4, 5) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `plants_new` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `name` TEXT NOT NULL,
                        `species` TEXT,
                        `cover_photo_uri` TEXT,
                        `created_at` INTEGER NOT NULL,
                        `scientific_name` TEXT,
                        `common_name` TEXT,
                        `measured_lux` INTEGER,
                        `measured_lux_at` INTEGER
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    """
                    INSERT INTO `plants_new` (
                        `id`, `name`, `species`, `cover_photo_uri`, `created_at`,
                        `scientific_name`, `common_name`, `measured_lux`, `measured_lux_at`
                    )
                    SELECT
                        `id`, `name`, `species`, `cover_photo_uri`, `created_at`,
                        `scientific_name`, `common_name`, `measured_lux`, `measured_lux_at`
                    FROM `plants`
                    """.trimIndent()
                )
                db.execSQL("DROP TABLE `plants`")
                db.execSQL("ALTER TABLE `plants_new` RENAME TO `plants`")
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_plants_created_at` " +
                        "ON `plants` (`created_at`)"
                )
            }
        }

        /** Re-add the plant-location column MIGRATION_4_5 dropped. ADD COLUMN is
         *  additive, so — unlike the 4→5 table rebuild — no row is copied and no
         *  garden is lost. */
        val MIGRATION_5_6 = object : androidx.room.migration.Migration(5, 6) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE `plants` ADD COLUMN `room` TEXT")
            }
        }

        /** Rooms become first-class rows, and treatment plans/steps + one-off
         *  tasks get storage. Entirely additive — three CREATE TABLEs and one
         *  ADD COLUMN, then a backfill that turns each distinct trimmed
         *  `plants.room` value into a `rooms` row and points `room_id` at it.
         *  No table is rebuilt, so no existing garden, scan, journal or
         *  reminder is touched, and the legacy `room` column is left in
         *  place (unread) rather than dropped. */
        val MIGRATION_6_7 = object : androidx.room.migration.Migration(6, 7) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `rooms` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `name` TEXT NOT NULL,
                        `icon` TEXT,
                        `baseline_lux` INTEGER,
                        `created_at` INTEGER NOT NULL,
                        `sort_order` INTEGER NOT NULL DEFAULT 0
                    )
                    """.trimIndent()
                )
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_rooms_name` ON `rooms` (`name`)")

                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `treatment_plans` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `plant_id` INTEGER NOT NULL,
                        `scan_id` INTEGER,
                        `disease_key` TEXT NOT NULL,
                        `disease_label_en` TEXT NOT NULL,
                        `disease_label_ru` TEXT NOT NULL,
                        `started_at` INTEGER NOT NULL,
                        `status` TEXT NOT NULL DEFAULT 'active',
                        `completed_at` INTEGER,
                        FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                            ON UPDATE NO ACTION ON DELETE CASCADE,
                        FOREIGN KEY(`scan_id`) REFERENCES `scans`(`id`)
                            ON UPDATE NO ACTION ON DELETE SET NULL
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_treatment_plans_plant_id` " +
                        "ON `treatment_plans` (`plant_id`)"
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_treatment_plans_scan_id` " +
                        "ON `treatment_plans` (`scan_id`)"
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_treatment_plans_plant_id_status` " +
                        "ON `treatment_plans` (`plant_id`, `status`)"
                )

                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `treatment_steps` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `plan_id` INTEGER NOT NULL,
                        `step_index` INTEGER NOT NULL,
                        `title` TEXT NOT NULL,
                        `detail` TEXT,
                        `bullets` TEXT,
                        `product_keys` TEXT,
                        `due_at` INTEGER NOT NULL,
                        `status` TEXT NOT NULL DEFAULT 'pending',
                        `done_at` INTEGER,
                        FOREIGN KEY(`plan_id`) REFERENCES `treatment_plans`(`id`)
                            ON UPDATE NO ACTION ON DELETE CASCADE
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_treatment_steps_plan_id` " +
                        "ON `treatment_steps` (`plan_id`)"
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_treatment_steps_due_at` " +
                        "ON `treatment_steps` (`due_at`)"
                )
                db.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS `index_treatment_steps_plan_id_step_index` " +
                        "ON `treatment_steps` (`plan_id`, `step_index`)"
                )

                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `care_tasks` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `plant_id` INTEGER NOT NULL,
                        `type` TEXT NOT NULL,
                        `title` TEXT NOT NULL,
                        `note` TEXT,
                        `due_at` INTEGER NOT NULL,
                        `status` TEXT NOT NULL DEFAULT 'pending',
                        `done_at` INTEGER,
                        `created_at` INTEGER NOT NULL,
                        FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                            ON UPDATE NO ACTION ON DELETE CASCADE
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_care_tasks_plant_id` ON `care_tasks` (`plant_id`)"
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_care_tasks_due_at` ON `care_tasks` (`due_at`)"
                )

                db.execSQL("ALTER TABLE `plants` ADD COLUMN `room_id` INTEGER")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_plants_room_id` ON `plants` (`room_id`)")

                // Backfill: every distinct non-blank trimmed room string
                // becomes a row, then each matching plant points at it.
                // TRIM collapses "Kitchen " and "Kitchen" into one room,
                // which the old free-text grouping never did.
                db.execSQL(
                    """
                    INSERT OR IGNORE INTO `rooms` (`name`, `created_at`, `sort_order`)
                    SELECT DISTINCT TRIM(`room`), strftime('%s','now') * 1000, 0
                    FROM `plants`
                    WHERE `room` IS NOT NULL AND TRIM(`room`) != ''
                    """.trimIndent()
                )
                db.execSQL(
                    """
                    UPDATE `plants`
                    SET `room_id` = (
                        SELECT `r`.`id` FROM `rooms` `r` WHERE `r`.`name` = TRIM(`plants`.`room`)
                    )
                    WHERE `room` IS NOT NULL AND TRIM(`room`) != ''
                    """.trimIndent()
                )
            }
        }

        fun getInstance(context: android.content.Context): AppDatabase = databaseInstance ?: synchronized(this) {
            val instance = Room.databaseBuilder(
                context.applicationContext,
                AppDatabase::class.java,
                "plantdiseases_db"
            )
                .addMigrations(
                    MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5, MIGRATION_5_6, MIGRATION_6_7,
                )
                // Room 2.7 deprecated the no-arg overload; `dropAllTables =
                // true` preserves the previous behaviour (wipe everything
                // when no migration path exists).
                .fallbackToDestructiveMigration(dropAllTables = true)
                .build()
            databaseInstance = instance
            instance
        }
    }
}
