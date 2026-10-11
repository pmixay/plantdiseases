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
    // (e.g. "uncertain_healthy", "nap_ood_gated"),
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
    // back to the server for set-membership diagnostics. Coverage remains
    // nominal/advisory until an independent locked evaluation exists.
    @ColumnInfo(name = "prediction_set") val predictionSet: String? = null,
    // Probability-backed class key the server returned (e.g. ``leaf_spot``).
    // Abstention is persisted separately through warning markers, so the raw
    // diagnostic remains available without being presented as a diagnosis.
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
    /** Durable cadence anchor for a reminder that has no matching care log
     * yet. A reminder added to a years-old plant must start its interval now,
     * not inherit the plant's creation date and appear instantly overdue. */
    @ColumnInfo(name = "created_at", defaultValue = "0")
    val createdAt: Long = System.currentTimeMillis(),
)

@Dao
abstract class PlantReminderDao {
    @Query("SELECT * FROM plant_reminders WHERE plant_id = :plantId")
    abstract suspend fun getForPlant(plantId: Long): List<PlantReminderEntity>

    /** Whole table — for garden-wide reads that would otherwise call
     *  [getForPlant] once per plant. */
    @Query("SELECT * FROM plant_reminders")
    abstract suspend fun getAll(): List<PlantReminderEntity>

    @Query("SELECT * FROM plant_reminders WHERE plant_id = :plantId AND type = :type LIMIT 1")
    abstract suspend fun getByPlantAndType(plantId: Long, type: String): PlantReminderEntity?

    @Query("SELECT * FROM plant_reminders WHERE id = :id LIMIT 1")
    abstract suspend fun getById(id: Long): PlantReminderEntity?

    // Room @Upsert updates an existing primary key in place. INSERT(REPLACE)
    // performs DELETE + INSERT in SQLite and would cascade-delete every
    // plant_reminder_events history row whenever the reminder was edited.
    @Upsert
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
        val target = entity.copy(
            id = existing?.id ?: entity.id,
            label = entity.label ?: existing?.label,
            description = entity.description ?: existing?.description,
            icon = entity.icon ?: existing?.icon,
            createdAt = existing?.createdAt ?: entity.createdAt,
        )
        val insertedId = upsert(target)
        return if (target.id != 0L) target.id else insertedId
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

    /** Undo path for a checklist-created completion marker. If completion
     * resolved a pre-existing pending/snoozed notification row, Undo restores
     * that exact row with [update] instead. Duplicate completion is a no-op,
     * so this never deletes an earlier action's event. */
    @Query("DELETE FROM plant_reminder_events WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("SELECT * FROM plant_reminder_events WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): PlantReminderEventEntity?

    /** Any disposition already stored for this reminder occurrence. Used by
     * the worker's transactional insert so an in-app Done racing a worker
     * firing cannot leave both a synthetic done row and a pending row. */
    @Query(
        """
        SELECT * FROM plant_reminder_events
        WHERE reminder_id = :reminderId
          AND fired_at >= :fromMs AND fired_at < :toMs
        ORDER BY fired_at DESC
        LIMIT 1
        """
    )
    suspend fun anyForOccurrence(
        reminderId: Long,
        fromMs: Long,
        toMs: Long,
    ): PlantReminderEventEntity?

    @Query(
        """
        SELECT * FROM plant_reminder_events
        WHERE plant_id = :plantId
        ORDER BY fired_at DESC
        LIMIT :limit
    """
    )
    suspend fun recentForPlant(plantId: Long, limit: Int = 50): List<PlantReminderEventEntity>

    /** Every "done" disposition overlapping [fromMs, toMs) — the range-bounded
     *  counterpart of [recentForPlant], which the care queue used to page
     *  through instead. Its `LIMIT 50` window covers ~16 days of three daily
     *  reminders, so browsing an older month read the same 50 newest rows and
     *  rendered every past occurrence as pending/overdue.
     *
     *  Both timestamps are tested: `CareRepository.completeRecurring` stamps
     *  `fired_at` with the occurrence's ORIGINAL due instant while `done_at`
     *  records when the user actually ticked it. Including either timestamp
     *  keeps occurrence rendering and recent adherence/history reads complete;
     *  the queue still keys Done status only to the original fired day.
     *  Covered by Index(["plant_id", "fired_at"]). */
    @Query(
        """
        SELECT * FROM plant_reminder_events
        WHERE plant_id = :plantId AND action = 'done'
          AND ((fired_at >= :fromMs AND fired_at < :toMs)
               OR (done_at >= :fromMs AND done_at < :toMs))
    """
    )
    suspend fun doneBetween(plantId: Long, fromMs: Long, toMs: Long): List<PlantReminderEventEntity>

    /** A notification firing that still represents this projected
     * occurrence. Completing care from an in-app checklist must resolve this
     * row instead of inserting a second event and leaving the notification
     * event pending forever. */
    @Query(
        """
        SELECT * FROM plant_reminder_events
        WHERE reminder_id = :reminderId
          AND action IN ('pending', 'snoozed')
          AND fired_at >= :fromMs AND fired_at < :toMs
        ORDER BY fired_at DESC
        LIMIT 1
        """
    )
    suspend fun pendingForOccurrence(
        reminderId: Long,
        fromMs: Long,
        toMs: Long,
    ): PlantReminderEventEntity?

    @Query(
        """
        SELECT * FROM plant_reminder_events
        WHERE reminder_id = :reminderId AND action = 'done'
          AND fired_at >= :fromMs AND fired_at < :toMs
        LIMIT 1
        """
    )
    suspend fun doneForOccurrence(
        reminderId: Long,
        fromMs: Long,
        toMs: Long,
    ): PlantReminderEventEntity?

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

/** Projection for [ScanDao.latestImageByPlant] — a plant id and the photo of
 *  its newest scan, without dragging the whole [ScanEntity] along. */
data class PlantThumb(
    @ColumnInfo(name = "plant_id") val plantId: Long,
    @ColumnInfo(name = "image_path") val imagePath: String?,
)

@Dao
interface ScanDao {
    @Query("SELECT * FROM scans ORDER BY timestamp DESC")
    suspend fun getAllScans(): List<ScanEntity>

    @Query("SELECT * FROM scans WHERE id = :id")
    suspend fun getScanById(id: Long): ScanEntity?

    @Query("SELECT * FROM scans WHERE client_scan_id = :clientScanId LIMIT 1")
    suspend fun getScanByClientScanId(clientScanId: String): ScanEntity?

    @Query("SELECT client_scan_id FROM scans WHERE client_scan_id != ''")
    suspend fun allClientScanIds(): List<String>

    @Query("SELECT * FROM scans WHERE plant_id = :plantId ORDER BY timestamp DESC")
    suspend fun getScansForPlant(plantId: Long): List<ScanEntity>

    /** Every scan that belongs to a plant, newest first — for garden-wide reads
     *  that would otherwise call [getScansForPlant] once per plant. Filtered on
     *  `plant_id IS NOT NULL` on purpose: [getAllScans] would also drag in every
     *  loose gallery scan (all 28 columns, JSON blobs included) only for the
     *  caller to throw the null bucket away. Same ordering as the per-plant
     *  query, so a caller grouping by plant_id sees identical buckets. */
    @Query("SELECT * FROM scans WHERE plant_id IS NOT NULL ORDER BY timestamp DESC")
    suspend fun getAllAssignedScans(): List<ScanEntity>

    /** Latest scan photo per plant, for the garden/care thumbnail strips.
     *  The callers used to run [getScansForPlant] once per plant and keep
     *  one `image_path` — reading all 28 columns, including the JSON blobs
     *  (`regions`, `all_probs`, `prediction_set`), to reach a single string.
     *  This is SQLite's documented bare-column-with-aggregate form: the row
     *  chosen by `MAX(timestamp)` supplies `image_path`. Do not rewrite it
     *  as `HAVING MAX(timestamp)`, which filters on truthiness rather than
     *  selecting a row. */
    @SuppressWarnings(RoomWarnings.QUERY_MISMATCH) // MAX(timestamp) selects the row; PlantThumb doesn't carry it.
    @Query(
        "SELECT plant_id, image_path, MAX(timestamp) FROM scans " +
            "WHERE plant_id IN (:plantIds) GROUP BY plant_id",
    )
    suspend fun latestImageByPlant(plantIds: List<Long>): List<PlantThumb>

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

    @Query("UPDATE plants SET room_id = NULL WHERE room_id = :roomId")
    suspend fun clearRoom(roomId: Long)

    @Query("DELETE FROM plants WHERE id = :id")
    suspend fun deleteById(id: Long)
}

@Dao
interface PlantJournalDao {
    @Query("SELECT * FROM plant_journal WHERE plant_id = :plantId ORDER BY timestamp DESC")
    suspend fun getEntriesForPlant(plantId: Long): List<PlantJournalEntity>

    /** Whole table, newest first — for garden-wide reads that would otherwise
     *  call [getEntriesForPlant] once per plant. Same ordering, so a caller
     *  grouping by plant_id sees exactly what the per-plant query returns. */
    @Query("SELECT * FROM plant_journal ORDER BY timestamp DESC")
    suspend fun getAllEntries(): List<PlantJournalEntity>

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
// objects that own a plant collection, which a plain string column does not
// support without a rewrite on every rename.

@Entity(tableName = "rooms", indices = [Index(value = ["name"], unique = true)])
data class RoomEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "name") val name: String,
    /** RoomPresets key (living/bedroom/kitchen/office/balcony/bathroom/other).
     *  Drives the room card icon; NULL renders the generic ic_room. */
    @ColumnInfo(name = "icon") val icon: String? = null,
    /** Optional per-room light baseline in lux. Nothing writes or reads it:
     *  room-level care defaults were dropped, and the column stays only so
     *  existing databases need no migration. Always NULL today. */
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

    // No findByName here on purpose: SQLite's COLLATE NOCASE folds ASCII
    // A–Z only, so a SQL uniqueness check let "Кухня" and "кухня" both
    // through the BINARY unique index. RoomRepository.findClash decides
    // name collisions in Kotlin instead, where the comparison is Unicode-aware.

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
    /** Snapshotted `treatment_conditions` from the care-plan response
     *  (best_time/temp_c/dry_hours), as JSON — {"best_time":str,
     *  "temp_c":[min,max]?,"dry_hours":int?}. Plan-level, not per-step:
     *  the server returns one application-window per disease, not per
     *  step. NULL when the disease has no curated entry (see
     *  diseases_data.py TREATMENT_CONDITIONS). Persisted here because,
     *  unlike the treatment text, the app never re-fetches /api/care-plan
     *  after plan creation — offline, this is the only copy. */
    @ColumnInfo(name = "conditions_json") val conditionsJson: String? = null,
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
    /** JSON array of the FULL product objects (key, kind, name,
     *  active_ingredient, note) — not just keys. The app is offline-capable
     *  and never re-fetches /api/care-plan after plan creation, so this is
     *  the only copy; a bare key with no local catalogue to resolve it
     *  against would strand the "Recommended products" section empty. */
    @ColumnInfo(name = "products_json") val productsJson: String? = null,
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

    @Query("DELETE FROM treatment_steps WHERE plan_id = :planId")
    suspend fun deleteForPlan(planId: Long)

    /** Steps carry no `plant_id` of their own (only `plan_id`) — CareRepository's
     *  cross-garden queue needs the owning plant, hence the join. */
    @Query(
        """
        SELECT treatment_steps.*, treatment_plans.plant_id AS plant_id
        FROM treatment_steps
        JOIN treatment_plans ON treatment_steps.plan_id = treatment_plans.id
        WHERE treatment_steps.due_at >= :from AND treatment_steps.due_at < :to
          AND treatment_plans.status = 'active'
        """
    )
    suspend fun getAllInRangeWithPlantId(from: Long, to: Long): List<StepWithPlantId>

    /** The home-screen Today queue intentionally includes every still-open
     * overdue step. Only active plans are actionable: steps belonging to a
     * superseded/abandoned plan must never leak back into the user's list. */
    @Query(
        """
        SELECT treatment_steps.*, treatment_plans.plant_id AS plant_id
        FROM treatment_steps
        JOIN treatment_plans ON treatment_steps.plan_id = treatment_plans.id
        WHERE treatment_steps.status = 'pending'
          AND treatment_steps.due_at < :cutoff
          AND treatment_plans.status = 'active'
        ORDER BY treatment_steps.due_at ASC
        """
    )
    suspend fun getActionableDueBeforeWithPlantId(cutoff: Long): List<StepWithPlantId>

    /** Step totals for a batch of plans ("Step 2 of 5"). One grouped read
     *  instead of one COUNT per plan on every calendar month/day load. */
    @Query(
        """
        SELECT `plan_id`, COUNT(*) AS `step_count` FROM treatment_steps
        WHERE plan_id IN (:planIds)
        GROUP BY plan_id
        """
    )
    suspend fun countsForPlans(planIds: List<Long>): List<PlanStepCount>
}

data class PlanStepCount(
    @ColumnInfo(name = "plan_id") val planId: Long,
    @ColumnInfo(name = "step_count") val stepCount: Int,
)

data class StepWithPlantId(
    @Embedded val step: TreatmentStepEntity,
    @ColumnInfo(name = "plant_id") val plantId: Long,
)

// ─── Notification centre ─────────────────────────────────────────────
//
// One row per notification the app has ever surfaced (community replies,
// care reminders, garden alerts), regardless of whether the system
// notification itself was allowed to post — see util/NotificationCenter.kt.
// `deeplink` is a plain "prefix:id" string (e.g. "community:abc123"),
// resolved by ui/notifications/NotificationRouter.kt; null means "just
// open Home".

@Entity(tableName = "notifications", indices = [Index(value = ["created_at"])])
data class NotificationEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "type") val type: String,
    @ColumnInfo(name = "title") val title: String,
    @ColumnInfo(name = "body") val body: String,
    @ColumnInfo(name = "deeplink") val deeplink: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long,
    @ColumnInfo(name = "read_at") val readAt: Long? = null,
)

@Dao
interface NotificationDao {
    @Query("SELECT * FROM notifications ORDER BY created_at DESC")
    suspend fun getAll(): List<NotificationEntity>

    @Query("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL")
    suspend fun getUnreadCount(): Int

    @Insert
    suspend fun insert(entity: NotificationEntity): Long

    @Query("UPDATE notifications SET read_at = :now WHERE id = :id AND read_at IS NULL")
    suspend fun markRead(id: Long, now: Long)

    @Query("UPDATE notifications SET read_at = :now WHERE read_at IS NULL")
    suspend fun markAllRead(now: Long)

    @Query("DELETE FROM notifications WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("DELETE FROM notifications WHERE created_at < :cutoff")
    suspend fun deleteOlderThan(cutoff: Long)

    @Query("DELETE FROM notifications")
    suspend fun clearAll()
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
        NotificationEntity::class,
    ],
    // Every version step from 1 has a hand-written migration registered
    // below, so upgrades preserve the garden, scans, journal and reminders.
    // ``fallbackToDestructiveMigration`` is still configured as the last
    // resort for version pairs with NO registered path (a downgrade, or a
    // developer build that skipped a step) — it does not rescue a migration
    // that leaves the schema drifted from the entities; that throws.
    version = 9,
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
    abstract fun notificationDao(): NotificationDao

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

        /** Rooms become first-class rows and treatment plans/steps get
         *  storage. Entirely additive — three CREATE TABLEs and one
         *  ADD COLUMN, then a backfill that turns each trimmed, case-insensitively
         *  distinct `plants.room` value into a `rooms` row and points `room_id` at it.
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
                        `conditions_json` TEXT,
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
                        `products_json` TEXT,
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

                db.execSQL("ALTER TABLE `plants` ADD COLUMN `room_id` INTEGER")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_plants_room_id` ON `plants` (`room_id`)")

                // Backfill: every distinct non-blank trimmed room string
                // becomes a row, then each matching plant points at it.
                // TRIM collapses "Kitchen " and "Kitchen" into one room,
                // which the old free-text grouping never did.
                //
                // The grouping is done in Kotlin, not SQL: SQLite's DISTINCT
                // is BINARY and COLLATE NOCASE folds ASCII A–Z only, so a
                // pure-SQL backfill made "Office"/"office" — and, with no
                // remedy at all, "Кухня"/"кухня" — two rooms on upgrade,
                // recreating the exact fragmentation this table exists to
                // prevent. `equals(ignoreCase = true)` in RoomRepository
                // guards new names the same way.
                val trimmedRoomNames = mutableListOf<String>()
                db.query(
                    "SELECT DISTINCT TRIM(`room`) FROM `plants` " +
                        "WHERE `room` IS NOT NULL AND TRIM(`room`) != ''"
                ).use { cursor ->
                    while (cursor.moveToNext()) {
                        trimmedRoomNames += cursor.getString(0)
                    }
                }
                val createdAt = System.currentTimeMillis()
                trimmedRoomNames.groupBy { it.lowercase(java.util.Locale.ROOT) }.forEach { (_, variants) ->
                    val canonical = variants.first()
                    db.execSQL(
                        "INSERT OR IGNORE INTO `rooms` (`name`, `created_at`, `sort_order`) VALUES (?, ?, 0)",
                        arrayOf<Any>(canonical, createdAt),
                    )
                    val roomId = db.query(
                        "SELECT `id` FROM `rooms` WHERE `name` = ?",
                        arrayOf<Any>(canonical),
                    ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else null }
                        ?: return@forEach
                    // One UPDATE per spelling: TRIM(room) = ? is a BINARY
                    // compare, which is what we want now that the grouping
                    // decision has already been made above.
                    variants.forEach { variant ->
                        db.execSQL(
                            "UPDATE `plants` SET `room_id` = ? WHERE TRIM(`room`) = ?",
                            arrayOf<Any>(roomId, variant),
                        )
                    }
                }
            }
        }

        /** Adds the notification centre. Entirely additive — one CREATE TABLE
         *  IF NOT EXISTS + one index, no existing table touched, no row
         *  copied. Matches NotificationEntity's schema exactly, and it has
         *  to: because this migration is registered, a 7→8 upgrade always
         *  runs this SQL and Room then validates the result against the
         *  entity — drift throws IllegalStateException at open time (a
         *  launch crash). It is NOT rescued by fallbackToDestructiveMigration,
         *  which only applies to version pairs with no registered migration.
         *  Migration6To7Test walks a v6 file up through here via Room so the
         *  validator actually runs. */
        val MIGRATION_7_8 = object : androidx.room.migration.Migration(7, 8) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `notifications` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `type` TEXT NOT NULL,
                        `title` TEXT NOT NULL,
                        `body` TEXT NOT NULL,
                        `deeplink` TEXT,
                        `created_at` INTEGER NOT NULL,
                        `read_at` INTEGER
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_notifications_created_at` " +
                        "ON `notifications` (`created_at`)"
                )
            }
        }

        /** Persists the reminder's own cadence anchor. Existing reminders are
         * anchored at upgrade time so an old plant does not generate years of
         * synthetic overdue work immediately after installing this version. */
        val MIGRATION_8_9 = object : androidx.room.migration.Migration(8, 9) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL(
                    "ALTER TABLE `plant_reminders` " +
                        "ADD COLUMN `created_at` INTEGER NOT NULL DEFAULT 0"
                )
                db.execSQL(
                    "UPDATE `plant_reminders` SET `created_at` = " +
                        "CAST(strftime('%s', 'now') AS INTEGER) * 1000 " +
                        "WHERE `created_at` = 0"
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
                    MIGRATION_1_2,
                    MIGRATION_2_3,
                    MIGRATION_3_4,
                    MIGRATION_4_5,
                    MIGRATION_5_6,
                    MIGRATION_6_7,
                    MIGRATION_7_8,
                    MIGRATION_8_9,
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
