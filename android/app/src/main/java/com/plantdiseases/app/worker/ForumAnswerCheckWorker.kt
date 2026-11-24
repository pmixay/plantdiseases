package com.plantdiseases.app.worker

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.di.appEntryPoint
import com.plantdiseases.app.push.ForumAnswerNotifier
import com.plantdiseases.app.util.ForumNotificationPrefs

/**
 * Polls Community activity (answers on the user's posts, replies to their
 * answers, accepted answers) and notifies about new ones. Production has no
 * FCM, so this poll is what reaches a closed app. It imports through the same
 * [com.plantdiseases.app.data.repository.ForumNotificationSync] the
 * notification centre uses, so an answer already seen in the app never pops up
 * later. Off when the user turns answer notifications off in Profile.
 *
 * Resolves its dependencies via [appEntryPoint]; see
 * [com.plantdiseases.app.di.AppEntryPoint] for why there is no HiltWorkerFactory.
 */
class ForumAnswerCheckWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val ctx = applicationContext
        if (!BuildConfig.ACCOUNT_UI_ENABLED || !ForumNotificationPrefs.answersEnabled(ctx)) {
            return Result.success()
        }
        val imported = appEntryPoint(ctx).forumNotificationSync().sync()
        ForumAnswerNotifier.notify(ctx, imported)
        return Result.success()
    }

    companion object {
        const val PERIODIC = "forum_answer_check"
    }
}
