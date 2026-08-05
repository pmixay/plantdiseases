package com.plantdiseases.app.ui.crop

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.IntentCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ActivityCropBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Custom crop screen — our own view-based cropper (see [CropImageView]),
 * replacing the deprecated android-image-cropper library.
 *
 * The screen is a single seamless block: the whole window (including the band
 * behind the system bars) is `@color/background`, so there is no distinct top
 * navbar. The photo is stretched adaptively to fill the viewport, and all
 * controls live in a fixed floating rounded green-glass panel at the bottom.
 *
 * Success returns `RESULT_OK` after the cropped JPEG (q90) is written to the
 * caller-supplied output Uri; back-press / cancel returns `RESULT_CANCELED`,
 * which the caller treats as a benign no-op.
 */
class CropActivity : AppCompatActivity() {

    private lateinit var binding: ActivityCropBinding
    private lateinit var outputUri: Uri
    private var saving = false

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityCropBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val source = IntentCompat.getParcelableExtra(intent, EXTRA_SOURCE, Uri::class.java)
        val output = IntentCompat.getParcelableExtra(intent, EXTRA_OUTPUT, Uri::class.java)
        if (source == null || output == null) {
            finishWithError(null)
            return
        }
        outputUri = output

        // Edge-to-edge, with the background band showing behind the (transparent)
        // system bars so the top area is seamless with the crop canvas. Bar icons
        // follow the theme: dark on the light background, light on the dark one.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val nightMask = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK
        val night = nightMask == Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(window, binding.root).apply {
            isAppearanceLightStatusBars = !night
            isAppearanceLightNavigationBars = !night
        }
        ViewCompat.setOnApplyWindowInsetsListener(binding.cropRoot) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        // Restore rotation + crop rect across recreation / process death, before
        // the (async) bitmap decode so setBitmap applies them.
        savedInstanceState?.getFloatArray(STATE_CROP)?.let { norm ->
            binding.cropView.restoreState(savedInstanceState.getInt(STATE_ROTATION, 0), norm)
        }

        binding.btnCancel.setOnClickListener {
            setResult(RESULT_CANCELED)
            finish()
        }
        binding.btnRotate.setOnClickListener {
            Haptics.tick(this)
            binding.cropView.rotate90()
        }
        binding.btnDone.setOnClickListener { saveAndFinish() }

        loadSource(source)
    }

    private fun loadSource(source: Uri) {
        lifecycleScope.launch {
            val bmp = withContext(Dispatchers.IO) { ImageUtils.decodeUpright(this@CropActivity, source) }
            if (bmp == null) {
                finishWithError(getString(R.string.crop_error))
            } else {
                binding.cropView.setBitmap(bmp)
            }
        }
    }

    private fun saveAndFinish() {
        if (saving) return
        saving = true
        setButtonsEnabled(false)
        // Crop on the main thread (a fast pixel copy on a ≤2048px bitmap, and
        // it reads the live crop rect race-free); only the JPEG encode — the
        // slow part — goes off-thread.
        val cropped = binding.cropView.getCroppedBitmap()
        if (cropped == null) {
            onSaveFailed()
            return
        }
        lifecycleScope.launch {
            val ok = withContext(Dispatchers.IO) {
                try {
                    contentResolver.openOutputStream(outputUri)?.use { out ->
                        cropped.compress(Bitmap.CompressFormat.JPEG, 90, out)
                    } ?: false
                } catch (e: Exception) {
                    false
                } finally {
                    cropped.recycle()
                }
            }
            if (ok) {
                setResult(RESULT_OK)
                finish()
            } else {
                onSaveFailed()
            }
        }
    }

    private fun onSaveFailed() {
        saving = false
        setButtonsEnabled(true)
        Toast.makeText(this, R.string.crop_error, Toast.LENGTH_SHORT).show()
    }

    private fun setButtonsEnabled(enabled: Boolean) {
        binding.btnCancel.isEnabled = enabled
        binding.btnRotate.isEnabled = enabled
        binding.btnDone.isEnabled = enabled
    }

    private fun finishWithError(message: String?) {
        setResult(RESULT_CANCELED, Intent().apply { message?.let { putExtra(EXTRA_ERROR, it) } })
        message?.let { Toast.makeText(this, it, Toast.LENGTH_SHORT).show() }
        finish()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(STATE_ROTATION, binding.cropView.rotationValue())
        outState.putFloatArray(STATE_CROP, binding.cropView.cropRectNormalized())
    }

    override fun onDestroy() {
        binding.cropView.recycleBitmaps()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_SOURCE = "crop_source"
        const val EXTRA_OUTPUT = "crop_output"
        const val EXTRA_ERROR = "crop_error"
        private const val STATE_ROTATION = "state_rotation"
        private const val STATE_CROP = "state_crop"
    }
}
