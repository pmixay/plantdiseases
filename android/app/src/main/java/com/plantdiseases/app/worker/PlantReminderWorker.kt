package com.plantdiseases.app.worker

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.ui.garden.PlantDetailActivity

class PlantReminderWorker(
    private val context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    @SuppressLint("MissingPermission") // permission checked at line below before notify()
    override suspend fun doWork(): Result {
        val plantId = inputData.getLong(KEY_PLANT_ID, -1L)
        val reminderType = inputData.getString(KEY_REMINDER_TYPE) ?: return Result.failure()
        val plantName = inputData.getString(KEY_PLANT_NAME) ?: return Result.failure()

        if (plantId == -1L) return Result.failure()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return Result.success()
        }

        val typeIndex = when (reminderType) {
            TYPE_WATERING -> 0
            TYPE_CHECK -> 1
            TYPE_FERTILIZE -> 2
            else -> return Result.failure()
        }
        val notificationId = (plantId * 10 + typeIndex).toInt()

        val (title, body) = when (reminderType) {
            TYPE_WATERING -> Pair(
                context.getString(R.string.reminder_notif_watering, plantName),
                context.getString(R.string.reminder_watering)
            )
            TYPE_CHECK -> Pair(
                context.getString(R.string.reminder_notif_check, plantName),
                context.getString(R.string.reminder_check)
            )
            TYPE_FERTILIZE -> Pair(
                context.getString(R.string.reminder_notif_fertilize, plantName),
                context.getString(R.string.reminder_fertilize)
            )
            else -> return Result.failure()
        }

        val contentIntent = PendingIntent.getActivity(
            context,
            notificationId,
            Intent(context, PlantDetailActivity::class.java).apply {
                putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plantId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = NotificationCompat.Builder(context, PlantDiseasesApp.CHANNEL_PLANT_REMINDERS)
            .setSmallIcon(R.drawable.ic_care)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(contentIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()

        val nm = context.getSystemService(NotificationManager::class.java)
        nm.notify(notificationId, notification)

        return Result.success()
    }

    companion object {
        const val KEY_PLANT_ID = "plant_id"
        const val KEY_REMINDER_TYPE = "reminder_type"
        const val KEY_PLANT_NAME = "plant_name"

        const val TYPE_WATERING = "watering"
        const val TYPE_CHECK = "check"
        const val TYPE_FERTILIZE = "fertilize"
    }
}
