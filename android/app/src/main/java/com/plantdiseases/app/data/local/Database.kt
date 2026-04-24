package com.plantdiseases.app.data.local

import androidx.room.*
import androidx.room.migration.Migration
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
    // All bounding boxes from the YOLOv8 detector, serialised as a JSON array
    // of {x, y, width, height, class, confidence}. Null when the server didn't
    // return any detections.
    @ColumnInfo(name = "regions") val regions: String? = null,
    // Index into `regions` that Stage 2 used as the primary crop (-1 if none).
    @ColumnInfo(name = "primary_region_index") val primaryRegionIndex: Int? = null,
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

@Database(entities = [ScanEntity::class], version = 4, exportSchema = false)
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

        // v3 → v4: replace the 4 single-region columns with a JSON `regions`
        // array + a `primary_region_index` pointer produced by the PlantScope
        // v3 YOLOv8 detector. Rebuild the table so the dropped columns
        // actually disappear from the schema (Room validates strictly).
        private val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE scans_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        image_path TEXT NOT NULL,
                        disease_name TEXT NOT NULL,
                        disease_name_ru TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        description TEXT NOT NULL,
                        description_ru TEXT NOT NULL,
                        treatment TEXT NOT NULL,
                        treatment_ru TEXT NOT NULL,
                        prevention TEXT NOT NULL,
                        prevention_ru TEXT NOT NULL,
                        is_healthy INTEGER NOT NULL,
                        timestamp INTEGER NOT NULL,
                        regions TEXT,
                        primary_region_index INTEGER,
                        all_probs TEXT
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    """
                    INSERT INTO scans_new (
                        id, image_path, disease_name, disease_name_ru, confidence,
                        description, description_ru, treatment, treatment_ru,
                        prevention, prevention_ru, is_healthy, timestamp,
                        regions, primary_region_index, all_probs
                    )
                    SELECT
                        id, image_path, disease_name, disease_name_ru, confidence,
                        description, description_ru, treatment, treatment_ru,
                        prevention, prevention_ru, is_healthy, timestamp,
                        CASE WHEN region_x IS NOT NULL AND region_y IS NOT NULL
                                  AND region_width IS NOT NULL AND region_height IS NOT NULL
                             THEN '[{"x":' || region_x || ',"y":' || region_y ||
                                  ',"width":' || region_width || ',"height":' || region_height ||
                                  ',"class":"diseased_leaf"}]'
                             ELSE NULL
                        END AS regions,
                        CASE WHEN region_x IS NOT NULL THEN 0 ELSE NULL END AS primary_region_index,
                        all_probs
                    FROM scans
                    """.trimIndent()
                )
                db.execSQL("DROP TABLE scans")
                db.execSQL("ALTER TABLE scans_new RENAME TO scans")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_scans_timestamp` ON `scans` (`timestamp`)")
                db.execSQL("CREATE INDEX IF NOT EXISTS `index_scans_is_healthy` ON `scans` (`is_healthy`)")
            }
        }

        fun getInstance(context: android.content.Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "plantdiseases_db"
                )
                    .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4)
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
