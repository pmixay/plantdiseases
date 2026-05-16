package com.plantdiseases.app.data.local

import androidx.room.*

@Entity(
    tableName = "plants",
    indices = [Index(value = ["created_at"])]
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
    /** Free-text room / location (e.g. "Living room", "Bathroom").
     *  Garden list groups by this when populated; null plants land
     *  in an "Unassigned" group at the bottom. */
    @ColumnInfo(name = "room") val room: String? = null,
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
    @ColumnInfo(name = "treatment") val treatment: String,        // JSON array
    @ColumnInfo(name = "treatment_ru") val treatmentRu: String,   // JSON array
    @ColumnInfo(name = "prevention") val prevention: String,      // JSON array
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
    // ``leaf_spot_fungal``). Persisted because ``argmax(all_probs)``
    // can disagree after the server's weak-healthy reroute, and
    // feedback submission must echo the DISPLAYED verdict — not
    // the argmax — to keep per-class accuracy metrics honest.
    @ColumnInfo(name = "displayed_class_key") val displayedClassKey: String? = null,
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
        Index(value = ["plant_id", "type"], unique = true),
    ]
)
data class PlantReminderEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    @ColumnInfo(name = "type") val type: String,          // "watering" | "check" | "fertilize"
    @ColumnInfo(name = "interval_days") val intervalDays: Int = 7,
    @ColumnInfo(name = "hour") val hour: Int = 9,
    @ColumnInfo(name = "minute") val minute: Int = 0,
    @ColumnInfo(name = "enabled") val enabled: Boolean = false,
)

@Dao
interface PlantReminderDao {
    @Query("SELECT * FROM plant_reminders WHERE plant_id = :plantId")
    suspend fun getForPlant(plantId: Long): List<PlantReminderEntity>

    @Query("SELECT * FROM plant_reminders WHERE plant_id = :plantId AND type = :type LIMIT 1")
    suspend fun getByPlantAndType(plantId: Long, type: String): PlantReminderEntity?

    @Query("SELECT * FROM plant_reminders WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): PlantReminderEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(reminder: PlantReminderEntity): Long

    @Query("DELETE FROM plant_reminders WHERE plant_id = :plantId")
    suspend fun deleteByPlant(plantId: Long)
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

    @Query("SELECT * FROM plant_reminder_events WHERE id = :id LIMIT 1")
    suspend fun getById(id: Long): PlantReminderEventEntity?

    @Query("""
        SELECT * FROM plant_reminder_events
        WHERE plant_id = :plantId
        ORDER BY fired_at DESC
        LIMIT :limit
    """)
    suspend fun recentForPlant(plantId: Long, limit: Int = 50): List<PlantReminderEventEntity>

    @Query("""
        SELECT MAX(done_at) FROM plant_reminder_events
        WHERE plant_id = :plantId AND type = :type AND action = 'done'
    """)
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
    @ColumnInfo(name = "role") val role: String,       // "user" | "bot"
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

// ─── Original DAOs ───────────────────────────────────────────────────────────

@Dao
interface ScanDao {
    @Query("SELECT * FROM scans ORDER BY timestamp DESC")
    suspend fun getAllScans(): List<ScanEntity>

    @Query("SELECT * FROM scans ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getRecentScans(limit: Int): List<ScanEntity>

    @Query("SELECT * FROM scans WHERE id = :id")
    suspend fun getScanById(id: Long): ScanEntity?

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

@Database(
    entities = [
        ScanEntity::class,
        PlantEntity::class,
        PlantJournalEntity::class,
        PlantReminderEntity::class,
        PlantReminderEventEntity::class,
        ChatMessageEntity::class,
    ],
    // The schema is treated as ephemeral while the app is in pre-release
    // testing: ``fallbackToDestructiveMigration`` is configured below
    // and any schema change wipes local data on next launch. Real
    // migrations come later, alongside cloud sync.
    version = 1,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scanDao(): ScanDao
    abstract fun plantDao(): PlantDao
    abstract fun plantJournalDao(): PlantJournalDao
    abstract fun plantReminderDao(): PlantReminderDao
    abstract fun plantReminderEventDao(): PlantReminderEventDao
    abstract fun chatMessageDao(): ChatMessageDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getInstance(context: android.content.Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "plantdiseases_db"
                )
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
