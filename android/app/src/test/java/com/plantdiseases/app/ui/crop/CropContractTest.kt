package com.plantdiseases.app.ui.crop

import android.app.Application
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class CropContractTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun avatarModeAndOutputRoundTripThroughTheContract() {
        val source = Uri.parse("content://test/avatar-source")
        val output = Uri.parse("content://test/avatar-output")
        val contract = CropContract()

        val launchIntent = contract.createIntent(
            context,
            CropContract.Input(source, output, CropContract.Mode.AVATAR),
        )
        assertThat(launchIntent.getStringExtra(CropActivity.EXTRA_MODE))
            .isEqualTo(CropContract.Mode.AVATAR.name)

        val result = contract.parseResult(
            Activity.RESULT_OK,
            Intent().putExtra(CropActivity.EXTRA_OUTPUT, output),
        )
        assertThat(result.isSuccessful).isTrue()
        assertThat(result.output).isEqualTo(output)
    }
}
