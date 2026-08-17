package com.plantdiseases.app.data.local

import android.content.Context
import androidx.room.Room
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.Locale

/**
 * Exercises [AppDatabase.MIGRATION_6_7] and [AppDatabase.MIGRATION_7_8]
 * the way a real upgrade runs them: a v6- (or v7-) shaped file is built by
 * hand — `exportSchema = false`, so there is no schema JSON for Room's
 * `MigrationTestHelper` to replay from — and then **opened through Room**
 * with every migration registered.
 *
 * That last part is the point. Calling `migrate()` directly on a raw
 * [SupportSQLiteDatabase] asserts nothing about the schema; opening through
 * Room runs `onValidateSchema`, which compares the migrated SQLite schema to
 * the `@Entity` definitions column-by-column, index-by-index and
 * foreign-key-by-foreign-key. **An exception is the assertion** — the branch
 * writes four new tables and eight new indices twice (once as annotations,
 * once as raw SQL) and nothing else pins the two together.
 *
 * `fallbackToDestructiveMigration` does not cover this: it only applies to
 * version pairs with no registered migration. With a path registered, Room
 * runs it and then throws `IllegalStateException("Migration didn't properly
 * handle: …")` on any mismatch — a launch crash, not a silent wipe.
 *
 * The fixture is deliberately the *full* v6 shape rather than the two or
 * three tables the migration reads: the validator checks every entity, so a
 * stripped fixture would fail for reasons that have nothing to do with the
 * migrations under test.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class Migration6To7Test {

    // ─── Fixtures ────────────────────────────────────────────────────────

    /** The v6 schema, exactly as migrations 1→6 leave it: everything the
     *  entities declare minus `plants.room_id` and the four tables
     *  MIGRATION_6_7/7_8 add. */
    private fun createV6Schema(db: SupportSQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE `plants` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `name` TEXT NOT NULL,
                `species` TEXT,
                `cover_photo_uri` TEXT,
                `created_at` INTEGER NOT NULL,
                `scientific_name` TEXT,
                `common_name` TEXT,
                `measured_lux` INTEGER,
                `measured_lux_at` INTEGER,
                `room` TEXT
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX `index_plants_created_at` ON `plants` (`created_at`)")

        db.execSQL(
            """
            CREATE TABLE `scans` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `client_scan_id` TEXT NOT NULL,
                `image_path` TEXT NOT NULL,
                `disease_name` TEXT NOT NULL,
                `disease_name_ru` TEXT NOT NULL,
                `confidence` REAL NOT NULL,
                `description` TEXT NOT NULL,
                `description_ru` TEXT NOT NULL,
                `treatment` TEXT NOT NULL,
                `treatment_ru` TEXT NOT NULL,
                `prevention` TEXT NOT NULL,
                `prevention_ru` TEXT NOT NULL,
                `is_healthy` INTEGER NOT NULL,
                `timestamp` INTEGER NOT NULL,
                `regions` TEXT,
                `primary_region_index` INTEGER,
                `detection_image_width` INTEGER NOT NULL,
                `detection_image_height` INTEGER NOT NULL,
                `all_probs` TEXT,
                `warnings` TEXT,
                `plant_id` INTEGER,
                `toxic_to_pets` INTEGER NOT NULL,
                `toxic_species_list` TEXT,
                `toxicity_source` TEXT NOT NULL,
                `species_scientific` TEXT,
                `species_common` TEXT,
                `species_score` REAL,
                `prediction_set` TEXT,
                `displayed_class_key` TEXT,
                `severity` TEXT NOT NULL DEFAULT 'low',
                FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                    ON UPDATE NO ACTION ON DELETE SET NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX `index_scans_timestamp` ON `scans` (`timestamp`)")
        db.execSQL("CREATE INDEX `index_scans_is_healthy` ON `scans` (`is_healthy`)")
        db.execSQL("CREATE INDEX `index_scans_plant_id` ON `scans` (`plant_id`)")

        db.execSQL(
            """
            CREATE TABLE `plant_journal` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `plant_id` INTEGER NOT NULL,
                `type` TEXT NOT NULL,
                `text` TEXT,
                `photo_uri` TEXT,
                `timestamp` INTEGER NOT NULL,
                FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                    ON UPDATE NO ACTION ON DELETE CASCADE
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX `index_plant_journal_plant_id` ON `plant_journal` (`plant_id`)")
        db.execSQL("CREATE INDEX `index_plant_journal_timestamp` ON `plant_journal` (`timestamp`)")

        db.execSQL(
            """
            CREATE TABLE `plant_reminders` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `plant_id` INTEGER NOT NULL,
                `type` TEXT NOT NULL,
                `interval_days` INTEGER NOT NULL,
                `hour` INTEGER NOT NULL,
                `minute` INTEGER NOT NULL,
                `enabled` INTEGER NOT NULL,
                `label` TEXT,
                `description` TEXT,
                `icon` TEXT,
                FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                    ON UPDATE NO ACTION ON DELETE CASCADE
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX `index_plant_reminders_plant_id` ON `plant_reminders` (`plant_id`)")
        db.execSQL(
            "CREATE INDEX `index_plant_reminders_plant_id_type` ON `plant_reminders` (`plant_id`, `type`)"
        )

        db.execSQL(
            """
            CREATE TABLE `plant_reminder_events` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `reminder_id` INTEGER NOT NULL,
                `plant_id` INTEGER NOT NULL,
                `type` TEXT NOT NULL,
                `fired_at` INTEGER NOT NULL,
                `action` TEXT NOT NULL,
                `done_at` INTEGER,
                FOREIGN KEY(`reminder_id`) REFERENCES `plant_reminders`(`id`)
                    ON UPDATE NO ACTION ON DELETE CASCADE
            )
            """.trimIndent()
        )
        db.execSQL(
            "CREATE INDEX `index_plant_reminder_events_reminder_id` " +
                "ON `plant_reminder_events` (`reminder_id`)"
        )
        db.execSQL(
            "CREATE INDEX `index_plant_reminder_events_plant_id_fired_at` " +
                "ON `plant_reminder_events` (`plant_id`, `fired_at`)"
        )

        db.execSQL(
            """
            CREATE TABLE `chat_messages` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `plant_id` INTEGER NOT NULL,
                `role` TEXT NOT NULL,
                `content` TEXT NOT NULL,
                `timestamp` INTEGER NOT NULL,
                FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                    ON UPDATE NO ACTION ON DELETE CASCADE
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX `index_chat_messages_plant_id` ON `chat_messages` (`plant_id`)")

        db.execSQL(
            """
            CREATE TABLE `scan_chat_messages` (
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
        db.execSQL("CREATE INDEX `index_scan_chat_messages_scan_id` ON `scan_chat_messages` (`scan_id`)")
    }

    /**
     * Writes a database file stamped at [version] (6 or 7) and closes it, so
     * the subsequent Room open sees an un-migrated file. `migrate()` is
     * deliberately NOT called by hand for the 6→7 step under test: doing so
     * would leave `user_version` at 6, Room would re-run MIGRATION_6_7 and
     * die on `ALTER TABLE plants ADD COLUMN room_id` ("duplicate column
     * name") — the `IF NOT EXISTS` guards protect the CREATEs, not the ALTER.
     */
    private fun createDatabaseFile(name: String, version: Int, seed: (SupportSQLiteDatabase) -> Unit = {}) {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        val helper = FrameworkSQLiteOpenHelperFactory().create(
            SupportSQLiteOpenHelper.Configuration.builder(ctx)
                .name(name)
                .callback(
                    object : SupportSQLiteOpenHelper.Callback(version) {
                        override fun onCreate(db: SupportSQLiteDatabase) {
                            createV6Schema(db)
                            // A v7 fixture is "v6 plus what 6→7 does" — reusing
                            // the production migration keeps the two in step.
                            if (version >= 7) AppDatabase.MIGRATION_6_7.migrate(db)
                        }

                        override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
                    }
                )
                .build()
        )
        val db = helper.writableDatabase
        seed(db)
        helper.close()
    }

    /** Opens the file through Room with every migration registered. Room
     *  walks the remaining versions and validates the result against the
     *  entities; anything wrong throws out of here. */
    private fun openThroughRoom(name: String): AppDatabase {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        val db = Room.databaseBuilder(ctx, AppDatabase::class.java, name)
            .addMigrations(
                AppDatabase.MIGRATION_1_2,
                AppDatabase.MIGRATION_2_3,
                AppDatabase.MIGRATION_3_4,
                AppDatabase.MIGRATION_4_5,
                AppDatabase.MIGRATION_5_6,
                AppDatabase.MIGRATION_6_7,
                AppDatabase.MIGRATION_7_8,
            )
            .allowMainThreadQueries()
            .build()
        db.openHelper.writableDatabase // force the upgrade + schema validation
        return db
    }

    // ─── Tests ───────────────────────────────────────────────────────────

    @Test
    fun migrateV6ThroughRoom_validatesSchemaAndPreservesTheGarden() {
        createDatabaseFile("migration_v6_backfill", version = 6) { db ->
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (1, 'Ficus', 1000, 'Office')")
            // Trailing space on an otherwise-identical name must collapse into
            // the SAME room row, not spawn a second one.
            db.execSQL(
                "INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (2, 'Monstera', 1000, 'Office ')"
            )
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (3, 'Cactus', 1000, NULL)")
            insertScan(db, id = 1, plantId = 1)
            db.execSQL(
                "INSERT INTO `plant_journal` (`id`, `plant_id`, `type`, `timestamp`) VALUES (1, 1, 'watering', 1000)"
            )
            db.execSQL(
                "INSERT INTO `plant_reminders` " +
                    "(`id`, `plant_id`, `type`, `interval_days`, `hour`, `minute`, `enabled`) " +
                    "VALUES (1, 1, 'watering', 7, 9, 0, 1)"
            )
        }

        val db = openThroughRoom("migration_v6_backfill")
        try {
            assertThat(names(db, "SELECT `name` FROM `rooms`")).containsExactly("Office")

            val roomIds = mutableMapOf<Int, Long?>()
            raw(db).query("SELECT `id`, `room_id` FROM `plants` ORDER BY `id` ASC").use { cursor ->
                while (cursor.moveToNext()) {
                    roomIds[cursor.getInt(0)] = if (cursor.isNull(1)) null else cursor.getLong(1)
                }
            }
            assertThat(roomIds[1]).isNotNull()
            assertThat(roomIds[1]).isEqualTo(roomIds[2])
            assertThat(roomIds[3]).isNull()

            assertThat(count(db, "scans")).isEqualTo(1)
            assertThat(count(db, "plant_journal")).isEqualTo(1)
            assertThat(count(db, "plant_reminders")).isEqualTo(1)
            // Added by 6→7 / 7→8, and empty on an upgrade.
            assertThat(count(db, "treatment_plans")).isEqualTo(0)
            assertThat(count(db, "treatment_steps")).isEqualTo(0)
            assertThat(count(db, "notifications")).isEqualTo(0)
        } finally {
            db.close()
        }
    }

    @Test
    fun migrateV6ThroughRoom_foldsCaseVariantRoomNamesIntoOneRoomEach() {
        // SQLite's DISTINCT is BINARY and COLLATE NOCASE folds ASCII A–Z only,
        // so the original SQL-only backfill made 'Office'/'office' two rooms
        // and had no answer at all for 'Кухня'/'кухня'.
        createDatabaseFile("migration_v6_case_fold", version = 6) { db ->
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (1, 'A', 1000, 'Office')")
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (2, 'B', 1000, 'office ')")
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (3, 'C', 1000, 'Кухня')")
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`, `room`) VALUES (4, 'D', 1000, 'кухня')")
        }

        val db = openThroughRoom("migration_v6_case_fold")
        try {
            val roomNames = names(db, "SELECT `name` FROM `rooms`").map { it.lowercase(Locale.ROOT) }
            assertThat(roomNames).containsExactly("office", "кухня")

            val roomIds = mutableMapOf<Int, Long?>()
            raw(db).query("SELECT `id`, `room_id` FROM `plants` ORDER BY `id` ASC").use { cursor ->
                while (cursor.moveToNext()) {
                    roomIds[cursor.getInt(0)] = if (cursor.isNull(1)) null else cursor.getLong(1)
                }
            }
            assertThat(roomIds[1]).isEqualTo(roomIds[2])
            assertThat(roomIds[3]).isEqualTo(roomIds[4])
            assertThat(roomIds[1]).isNotEqualTo(roomIds[3])
        } finally {
            db.close()
        }
    }

    @Test
    fun migrateV7ThroughRoom_addsTheNotificationCentreAndValidates() {
        createDatabaseFile("migration_v7_notifications", version = 7) { db ->
            db.execSQL("INSERT INTO `plants` (`id`, `name`, `created_at`) VALUES (1, 'Ficus', 1000)")
            db.execSQL("INSERT INTO `rooms` (`id`, `name`, `created_at`, `sort_order`) VALUES (1, 'Office', 1000, 0)")
        }

        val db = openThroughRoom("migration_v7_notifications")
        try {
            assertThat(count(db, "notifications")).isEqualTo(0)
            // Nothing MIGRATION_7_8 touches, so it must be untouched.
            assertThat(count(db, "plants")).isEqualTo(1)
            assertThat(count(db, "rooms")).isEqualTo(1)
        } finally {
            db.close()
        }
    }

    // ─── Helpers ─────────────────────────────────────────────────────────

    private fun insertScan(db: SupportSQLiteDatabase, id: Long, plantId: Long) {
        db.execSQL(
            """
            INSERT INTO `scans` (
                `id`, `client_scan_id`, `image_path`, `disease_name`, `disease_name_ru`, `confidence`,
                `description`, `description_ru`, `treatment`, `treatment_ru`, `prevention`, `prevention_ru`,
                `is_healthy`, `timestamp`, `detection_image_width`, `detection_image_height`,
                `toxic_to_pets`, `toxicity_source`, `severity`, `plant_id`
            ) VALUES (?, 'c1', '/p.jpg', 'Healthy', 'Здоровое', 0.9, '', '', '[]', '[]', '[]', '[]',
                1, 1000, 0, 0, 0, 'unknown', 'low', ?)
            """.trimIndent(),
            arrayOf<Any>(id, plantId),
        )
    }

    /** The already-open, already-migrated connection behind [db]. */
    private fun raw(db: AppDatabase): SupportSQLiteDatabase = db.openHelper.writableDatabase

    private fun names(db: AppDatabase, sql: String): List<String> {
        val out = mutableListOf<String>()
        raw(db).query(sql).use { cursor ->
            while (cursor.moveToNext()) out += cursor.getString(0)
        }
        return out
    }

    private fun count(db: AppDatabase, table: String): Int =
        raw(db).query("SELECT COUNT(*) FROM `$table`").use { cursor ->
            cursor.moveToFirst()
            cursor.getInt(0)
        }
}
