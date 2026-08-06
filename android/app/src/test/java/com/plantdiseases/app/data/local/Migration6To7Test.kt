package com.plantdiseases.app.data.local

import android.content.Context
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Exercises [AppDatabase.MIGRATION_6_7] directly against a raw
 * [SupportSQLiteDatabase] built to the v6 shape by hand — `exportSchema =
 * false` (Database.kt L15-ish, "the schema is treated as ephemeral")
 * means there's no schema JSON for Room's `MigrationTestHelper` to replay
 * from, so this constructs the pre-migration table itself instead.
 *
 * Covers the ROADMAP schema gate's two questions: does an existing garden
 * survive, and does the free-text `room` -> `rooms` backfill actually
 * collapse duplicates.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class Migration6To7Test {

    private fun openV6Database(name: String): SupportSQLiteDatabase {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        val helper = FrameworkSQLiteOpenHelperFactory().create(
            SupportSQLiteOpenHelper.Configuration.builder(ctx)
                .name(name)
                .callback(
                    object : SupportSQLiteOpenHelper.Callback(6) {
                        override fun onCreate(db: SupportSQLiteDatabase) {
                            // Minimal v6 shape: only the columns/tables MIGRATION_6_7
                            // actually reads or writes. The real schema carries far
                            // more (scans, journal, reminders, …); this test's job is
                            // the migration logic, not a full schema replay.
                            db.execSQL(
                                """
                                CREATE TABLE `plants` (
                                    `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                                    `name` TEXT NOT NULL,
                                    `room` TEXT
                                )
                                """.trimIndent()
                            )
                            db.execSQL(
                                "CREATE TABLE `scans` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                                    "`plant_id` INTEGER)"
                            )
                            db.execSQL(
                                "CREATE TABLE `plant_journal` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                                    "`plant_id` INTEGER NOT NULL)"
                            )
                            db.execSQL(
                                "CREATE TABLE `plant_reminders` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                                    "`plant_id` INTEGER NOT NULL)"
                            )
                        }

                        override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
                    }
                )
                .build()
        )
        return helper.writableDatabase
    }

    @Test
    fun migrate_backfillsDistinctTrimmedRoomsAndPreservesOtherTables() {
        val db = openV6Database("migration_6_7_backfill")
        db.execSQL("INSERT INTO `plants` (`id`, `name`, `room`) VALUES (1, 'Ficus', 'Office')")
        // Trailing space on an otherwise-identical name must collapse into
        // the SAME room row, not spawn a second one.
        db.execSQL("INSERT INTO `plants` (`id`, `name`, `room`) VALUES (2, 'Monstera', 'Office ')")
        db.execSQL("INSERT INTO `plants` (`id`, `name`, `room`) VALUES (3, 'Cactus', NULL)")
        db.execSQL("INSERT INTO `scans` (`id`, `plant_id`) VALUES (1, 1)")
        db.execSQL("INSERT INTO `plant_journal` (`id`, `plant_id`) VALUES (1, 1)")
        db.execSQL("INSERT INTO `plant_reminders` (`id`, `plant_id`) VALUES (1, 1)")

        AppDatabase.MIGRATION_6_7.migrate(db)

        val roomCountCursor = db.query("SELECT COUNT(*) FROM `rooms`")
        roomCountCursor.moveToFirst()
        assertThat(roomCountCursor.getInt(0)).isEqualTo(1)
        roomCountCursor.close()

        val roomNameCursor = db.query("SELECT `name` FROM `rooms`")
        roomNameCursor.moveToFirst()
        assertThat(roomNameCursor.getString(0)).isEqualTo("Office")
        roomNameCursor.close()

        val plantRoomsCursor = db.query(
            "SELECT `id`, `room_id` FROM `plants` ORDER BY `id` ASC"
        )
        val roomIds = mutableMapOf<Int, Long?>()
        while (plantRoomsCursor.moveToNext()) {
            val id = plantRoomsCursor.getInt(0)
            roomIds[id] = if (plantRoomsCursor.isNull(1)) null else plantRoomsCursor.getLong(1)
        }
        plantRoomsCursor.close()
        assertThat(roomIds[1]).isNotNull()
        assertThat(roomIds[1]).isEqualTo(roomIds[2])
        assertThat(roomIds[3]).isNull()

        assertThat(count(db, "scans")).isEqualTo(1)
        assertThat(count(db, "plant_journal")).isEqualTo(1)
        assertThat(count(db, "plant_reminders")).isEqualTo(1)

        db.close()
    }

    @Test
    fun migrate_createsEmptyTreatmentAndTaskTables() {
        val db = openV6Database("migration_6_7_new_tables")
        AppDatabase.MIGRATION_6_7.migrate(db)

        assertThat(count(db, "treatment_plans")).isEqualTo(0)
        assertThat(count(db, "treatment_steps")).isEqualTo(0)
        assertThat(count(db, "care_tasks")).isEqualTo(0)

        db.close()
    }

    private fun count(db: SupportSQLiteDatabase, table: String): Int {
        val cursor = db.query("SELECT COUNT(*) FROM `$table`")
        cursor.moveToFirst()
        val n = cursor.getInt(0)
        cursor.close()
        return n
    }
}
