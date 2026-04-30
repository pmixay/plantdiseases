package com.plantdiseases.app.data.local

import androidx.room.*

@Entity(
    tableName = "plants",
    indices = [Index(value = ["created_at"])]
)
data class PlantEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "name") val name: String,
    @ColumnInfo(name = "species") val species: String? = null,
    @ColumnInfo(name = "nickname") val nickname: String? = null,
    @ColumnInfo(name = "icon_uri") val iconUri: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
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
)

@Dao
interface ScanDao {
    @Query("SELECT * FROM scans ORDER BY timestamp DESC")
    suspend fun getAllScans(): List<ScanEntity>

    @Query("SELECT * FROM scans ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getRecentScans(limit: Int): List<ScanEntity>

    @Query("SELECT * FROM scans WHERE id = :id")
    suspend fun getScanById(id: Long): ScanEntity?

    @Insert
    suspend fun insert(scan: ScanEntity): Long

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

@Database(
    entities = [ScanEntity::class, PlantEntity::class],
    version = 3,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scanDao(): ScanDao
    abstract fun plantDao(): PlantDao

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
                    // No legacy migrations: pre-1.1 schemas were never
                    // shipped to production.
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
