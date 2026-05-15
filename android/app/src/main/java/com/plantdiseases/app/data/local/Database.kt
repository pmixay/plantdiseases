package com.plantdiseases.app.data.local

import androidx.room.*

@Entity(
    tableName = "plants",
    indices = [Index(value = ["created_at"])]
)
data class PlantEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "name") val name: String,
    /**
     * Free-text species (legacy field, kept for back-compat). When the user
     * accepts a PlantNet suggestion this stays in sync with `commonName`
     * for display, while the canonical Latin name lives in `scientificName`.
     */
    @ColumnInfo(name = "species") val species: String? = null,
    @ColumnInfo(name = "icon_uri") val iconUri: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    /** Latin / scientific name as returned by PlantNet (e.g. "Ficus elastica"). */
    @ColumnInfo(name = "scientific_name") val scientificName: String? = null,
    /** Localised common name (e.g. "Rubber Plant"). */
    @ColumnInfo(name = "common_name") val commonName: String? = null,
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
    // Canonical class key the server returned for this scan (e.g.
    // ``leaf_spot_fungal``). Persisted because ``argmax(all_probs)``
    // can disagree after the server's weak-healthy reroute, and
    // feedback submission must echo the DISPLAYED verdict — not
    // the argmax — to keep per-class accuracy metrics honest.
    @ColumnInfo(name = "displayed_class_key") val displayedClassKey: String? = null,
    // Server build version (e.g. ``"2.1"``) of the model that
    // produced this scan. Persisted so feedback rows can be cohorted
    // by the version actually loaded on the server, not by a stale
    // client constant.
    @ColumnInfo(name = "server_version") val serverVersion: String? = null,
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

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(reminder: PlantReminderEntity): Long

    @Query("DELETE FROM plant_reminders WHERE plant_id = :plantId")
    suspend fun deleteByPlant(plantId: Long)
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
        ChatMessageEntity::class,
    ],
    // v3: added ScanEntity.displayed_class_key so feedback
    // submissions echo the verdict the user actually saw rather
    // than argmax(all_probs), which can disagree after the
    // server's weak-healthy reroute. (v2's prediction_set column
    // was removed alongside the retired auto-retraining loop;
    // we stay on v3 with that column gone via ``fallbackToDestructiveMigration``,
    // which wipes the local scan history on first launch with
    // the new schema — same trade-off the app has been shipping under.)
    version = 3,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scanDao(): ScanDao
    abstract fun plantDao(): PlantDao
    abstract fun plantJournalDao(): PlantJournalDao
    abstract fun plantReminderDao(): PlantReminderDao
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
