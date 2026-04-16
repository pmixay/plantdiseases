package com.plantdiseases.app.data.local

import androidx.room.*
import androidx.sqlite.db.SupportSQLiteDatabase

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
    // Detection region from Grad-CAM (pixel coordinates in original image)
    @ColumnInfo(name = "region_x") val regionX: Int? = null,
    @ColumnInfo(name = "region_y") val regionY: Int? = null,
    @ColumnInfo(name = "region_width") val regionWidth: Int? = null,
    @ColumnInfo(name = "region_height") val regionHeight: Int? = null,
    // All class probabilities as JSON string for top-3 diagnoses display
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

@Database(entities = [ScanEntity::class], version = 3, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun scanDao(): ScanDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_scans_timestamp` ON `scans` (`timestamp`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_scans_is_healthy` ON `scans` (`is_healthy`)")
            }
        }

        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE scans ADD COLUMN region_x INTEGER DEFAULT NULL")
                db.execSQL("ALTER TABLE scans ADD COLUMN region_y INTEGER DEFAULT NULL")
                db.execSQL("ALTER TABLE scans ADD COLUMN region_width INTEGER DEFAULT NULL")
                db.execSQL("ALTER TABLE scans ADD COLUMN region_height INTEGER DEFAULT NULL")
                db.execSQL("ALTER TABLE scans ADD COLUMN all_probs TEXT DEFAULT NULL")
            }
        }

        fun getInstance(context: android.content.Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "plantdiseases_db"
                )
                    .addMigrations(MIGRATION_1_2, MIGRATION_2_3)
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
