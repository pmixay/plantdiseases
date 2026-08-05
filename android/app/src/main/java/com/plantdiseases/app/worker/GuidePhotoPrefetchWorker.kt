package com.plantdiseases.app.worker

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.bumptech.glide.Glide
import com.plantdiseases.app.data.GuideDataProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Warms Glide's disk cache with every guide/disease example photo while the
 * device is online, so they render offline afterwards.
 *
 * The photos live on the server (`/api/guide/photo/<key>`) — they were moved
 * out of the APK to shrink it — and Glide only caches an image once it has
 * actually been requested. Without this, a guide article or result-screen
 * photo the user never opened while connected shows the neutral placeholder
 * offline. Downloading the small PNG set once (deduped by Glide's cache on
 * re-run) closes that gap.
 *
 * Runs under a CONNECTED constraint; a network failure mid-run returns
 * [Result.retry] so WorkManager retries when connectivity is back.
 */
class GuidePhotoPrefetchWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val ctx = applicationContext
        var anyFailed = false
        for (url in GuideDataProvider.allExamplePhotoUrls()) {
            try {
                // Blocking download into Glide's disk cache (DATA). A cache hit
                // returns immediately without touching the network, so re-runs
                // are cheap.
                Glide.with(ctx).download(url).submit().get()
            } catch (e: Exception) {
                // A single miss (server 404 for a not-yet-provisioned key, or a
                // transient blip) shouldn't fail the whole batch; note it so we
                // retry the set later.
                anyFailed = true
            }
        }
        if (anyFailed) Result.retry() else Result.success()
    }
}
