package com.plantdiseases.app.ui.crop

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.result.contract.ActivityResultContract
import androidx.core.content.IntentCompat

/**
 * Drop-in replacement for the removed library's `CropImageContract`. Launches
 * [CropActivity] on a source image and writes the cropped JPEG to [Input.output].
 * [Mode.AVATAR] keeps the crop square and renders a circular final-shape preview.
 *
 * [Result] deliberately mirrors the old result shape (`isSuccessful` + `error`)
 * so the caller's existing callback — which keys real success on bytes landing
 * in the output file — stays unchanged.
 */
class CropContract : ActivityResultContract<CropContract.Input, CropContract.Result>() {

    enum class Mode { FREEFORM, AVATAR }

    data class Input(
        val source: Uri,
        val output: Uri,
        val mode: Mode = Mode.FREEFORM,
    )

    data class Result(
        val isSuccessful: Boolean,
        val output: Uri? = null,
        val error: Throwable? = null,
    )

    override fun createIntent(context: Context, input: Input): Intent =
        Intent(context, CropActivity::class.java).apply {
            putExtra(CropActivity.EXTRA_SOURCE, input.source)
            putExtra(CropActivity.EXTRA_OUTPUT, input.output)
            putExtra(CropActivity.EXTRA_MODE, input.mode.name)
        }

    override fun parseResult(resultCode: Int, intent: Intent?): Result {
        val message = intent?.getStringExtra(CropActivity.EXTRA_ERROR)
        return Result(
            isSuccessful = resultCode == Activity.RESULT_OK,
            output = intent?.let {
                IntentCompat.getParcelableExtra(it, CropActivity.EXTRA_OUTPUT, Uri::class.java)
            },
            error = message?.let { RuntimeException(it) },
        )
    }
}
