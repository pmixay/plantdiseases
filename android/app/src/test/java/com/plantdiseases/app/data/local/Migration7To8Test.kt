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
 * Exercises [AppDatabase.MIGRATION_7_8] directly, the same way
 * Migration6To7Test covers MIGRATION_6_7. The migration's own comment in
 * Database.kt claims the new `notifications` table matches
 * NotificationEntity's schema exactly — nothing was actually checking that
 * claim before this test. Getting it wrong would not fail silently either:
 * fallbackToDestructiveMigration only covers a MISSING migration path, and
 * this one is registered, so a real column mismatch throws
 * IllegalStateException on every upgrading user's next launch.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class Migration7To8Test {

    private fun openV7Database(name: String): SupportSQLiteDatabase {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        val helper = FrameworkSQLiteOpenHelperFactory().create(
            SupportSQLiteOpenHelper.Configuration.builder(ctx)
                .name(name)
                .callback(
                    object : SupportSQLiteOpenHelper.Callback(7) {
                        // MIGRATION_7_8 only creates a new table — it reads and
                        // writes nothing that already exists, so the pre-migration
                        // database can start genuinely empty.
                        override fun onCreate(db: SupportSQLiteDatabase) = Unit

                        override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
                    }
                )
                .build()
        )
        return helper.writableDatabase
    }

    @Test
    fun migrate_notificationsTableMatchesTheEntitySchema() {
        val db = openV7Database("migration_7_8_schema")
        AppDatabase.MIGRATION_7_8.migrate(db)

        // PRAGMA table_info columns, in order: cid, name, type, notnull, dflt_value, pk.
        val columnTypes = mutableMapOf<String, String>()
        val cursor = db.query("PRAGMA table_info(`notifications`)")
        while (cursor.moveToNext()) {
            columnTypes[cursor.getString(1)] = cursor.getString(2)
        }
        cursor.close()

        // Exactly NotificationEntity's columns — nothing missing, nothing extra.
        assertThat(columnTypes.keys).containsExactly(
            "id", "type", "title", "body", "deeplink", "created_at", "read_at",
        )
        assertThat(columnTypes["id"]).isEqualTo("INTEGER")
        assertThat(columnTypes["created_at"]).isEqualTo("INTEGER")
        assertThat(columnTypes["type"]).isEqualTo("TEXT")

        db.close()
    }

    @Test
    fun migrate_createsEmptyNotificationsTable() {
        val db = openV7Database("migration_7_8_empty")
        AppDatabase.MIGRATION_7_8.migrate(db)

        val cursor = db.query("SELECT COUNT(*) FROM `notifications`")
        cursor.moveToFirst()
        assertThat(cursor.getInt(0)).isEqualTo(0)
        cursor.close()

        db.close()
    }
}
