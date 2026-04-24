package com.plantdiseases.app.data.local

import androidx.room.*

@Entity(
    tableName = "scans",
    indices = [
        Index(value = ["timestamp"]),
        Index(value = ["is_healthy"])
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
    @ColumnInfo(name = "all_probs") val allProbs: String? = null
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

    @Delete
    suspend fun delete(scan: ScanEntity)

    @Query("DELETE FROM scans WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("SELECT COUNT(*) FROM scans")
    suspend fun getCount(): Int

    @Query("SELECT COUNT(*) FROM scans WHERE is_healthy = 1")
    suspend fun getHealthyCount(): Int

    @Query("SELECT COUNT(*) FROM scans WHERE is_healthy = 0")
    suspend fun getDiseasedCount(): Int

    @Query("SELECT disease_name FROM scans WHERE is_healthy = 0 GROUP BY disease_name ORDER BY COUNT(*) DESC LIMIT 1")
    suspend fun getMostCommonDisease(): String?

    @Query("SELECT disease_name_ru FROM scans WHERE is_healthy = 0 GROUP BY disease_name_ru ORDER BY COUNT(*) DESC LIMIT 1")
    suspend fun getMostCommonDiseaseRu(): String?

    @Query("DELETE FROM scans")
    suspend fun deleteAll()
}

@Database(entities = [ScanEntity::class], version = 1, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scanDao(): ScanDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getInstance(context: android.content.Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "plantdiseases_db"
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}
