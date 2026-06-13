package com.plantdiseases.app.ui.identify

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.google.android.material.chip.Chip
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.SpeciesCandidate
import com.plantdiseases.app.databinding.ActivitySpeciesIdentifyBinding
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Locale
import kotlin.math.roundToInt

/**
 * "Identify my plant" — the species-first entry point (see docs/ROADMAP.md
 * §3). Take/pick a photo, choose the photographed organ, identify via the
 * server's PlantNet proxy, then tap a candidate to open the full, scan-like
 * [SpeciesDetailActivity] (reference photos, encyclopedia blurb, care summary,
 * common problems with guide links, add-to-garden / scan-health).
 */
class SpeciesIdentifyActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySpeciesIdentifyBinding

    private var photoFile: File? = null
    private var pendingCameraUri: Uri? = null
    private var selectedOrgan: String = "leaf"

    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
    private val locale: String get() = if (isRu) "ru" else "en"

    private val takePicture =
        registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
            val uri = pendingCameraUri
            if (ok && uri != null) onPhotoChosen(uri)
        }

    private val pickImage =
        registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
            if (uri != null) onPhotoChosen(uri)
        }

    private val requestCamera =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) launchCamera()
        }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySpeciesIdentifyBinding.inflate(layoutInflater)
        setContentView(binding.root)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            // Pad the app-bar container (not the fixed-height toolbar) so the
            // toolbar sits fully below the status bar instead of being squeezed.
            binding.appbar.updatePadding(top = bars.top)
            insets
        }

        binding.identifyToolbar.setNavigationOnClickListener { finish() }
        binding.btnCamera.setOnClickListener { ensureCameraThenLaunch() }
        binding.btnGallery.setOnClickListener { pickImage.launch("image/*") }
        binding.btnIdentify.setOnClickListener { identify() }
        buildOrganChips()
    }

    // ── Photo acquisition ───────────────────────────────────────────
    private fun ensureCameraThenLaunch() {
        if (checkSelfPermission(android.Manifest.permission.CAMERA) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            launchCamera()
        } else {
            requestCamera.launch(android.Manifest.permission.CAMERA)
        }
    }

    private fun launchCamera() {
        val file = File(cacheDir, "identify_capture.jpg")
        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
        pendingCameraUri = uri
        takePicture.launch(uri)
    }

    /** Copy + downscale the chosen image into a cache file the repository
     *  can upload (PlantNet doesn't need more than ~1600px). */
    private fun onPhotoChosen(uri: Uri) {
        lifecycleScope.launch {
            val file = withContext(Dispatchers.IO) {
                runCatching {
                    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    contentResolver.openInputStream(uri)?.use {
                        BitmapFactory.decodeStream(it, null, bounds)
                    }
                    var sample = 1
                    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= 1600) {
                        sample *= 2
                    }
                    val opts = BitmapFactory.Options().apply { inSampleSize = sample }
                    val bmp = contentResolver.openInputStream(uri)?.use {
                        BitmapFactory.decodeStream(it, null, opts)
                    } ?: return@runCatching null
                    val out = File(cacheDir, "identify_upload.jpg")
                    out.outputStream().use { bmp.compress(Bitmap.CompressFormat.JPEG, 90, it) }
                    out
                }.getOrNull()
            }
            if (file == null) {
                Toast.makeText(
                    this@SpeciesIdentifyActivity,
                    R.string.identify_photo_failed, Toast.LENGTH_SHORT,
                ).show()
                return@launch
            }
            photoFile = file
            binding.btnIdentify.isEnabled = true
            binding.listCandidates.removeAllViews()
            Glide.with(this@SpeciesIdentifyActivity).load(file)
                .skipMemoryCache(true)
                .diskCacheStrategy(com.bumptech.glide.load.engine.DiskCacheStrategy.NONE)
                .into(binding.ivPhoto)
        }
    }

    // ── Organ chips ─────────────────────────────────────────────────
    private fun buildOrganChips() {
        val organs = listOf(
            "leaf" to R.string.identify_organ_leaf,
            "flower" to R.string.identify_organ_flower,
            "fruit" to R.string.identify_organ_fruit,
            "bark" to R.string.identify_organ_bark,
        )
        organs.forEach { (key, label) ->
            binding.chipsOrgan.addView(Chip(this).apply {
                text = getString(label)
                isCheckable = true
                isChecked = key == selectedOrgan
                setOnClickListener { selectedOrgan = key }
            })
        }
    }

    // ── Identify ────────────────────────────────────────────────────
    private fun identify() {
        val file = photoFile ?: return
        val app = application as PlantDiseasesApp
        binding.progress.visibility = View.VISIBLE
        binding.tvStatus.visibility = View.GONE
        binding.btnIdentify.isEnabled = false
        binding.listCandidates.removeAllViews()
        lifecycleScope.launch {
            val result = app.scanRepository.identifySpecies(file, selectedOrgan, locale)
            binding.progress.visibility = View.GONE
            binding.btnIdentify.isEnabled = true
            result.onSuccess { resp ->
                when {
                    !resp.available -> showStatus(getString(R.string.identify_unavailable))
                    resp.results.isEmpty() -> showStatus(getString(R.string.identify_no_match))
                    else -> renderCandidates(resp.results.take(5))
                }
            }.onFailure {
                showStatus(it.localizedMessage ?: getString(R.string.identify_failed))
            }
        }
    }

    private fun showStatus(msg: String) {
        binding.tvStatus.text = msg
        binding.tvStatus.visibility = View.VISIBLE
    }

    private fun renderCandidates(candidates: List<SpeciesCandidate>) {
        binding.listCandidates.removeAllViews()
        candidates.forEach { c ->
            val sci = c.scientificName ?: return@forEach
            val row = layoutInflater.inflate(
                R.layout.item_home_tool, binding.listCandidates, false,
            )
            row.findViewById<android.widget.ImageView>(R.id.tool_icon)
                .setImageResource(R.drawable.ic_plant_placeholder)
            row.findViewById<TextView>(R.id.tool_title).text = getString(
                R.string.identify_candidate_row,
                c.displayCommonName ?: sci,
                sci,
                (c.score * 100).roundToInt(),
            )
            (row.layoutParams as? LinearLayout.LayoutParams)?.topMargin =
                (8 * resources.displayMetrics.density).toInt()
            row.setOnClickListener {
                SpeciesDetailActivity.start(this, sci, c.displayCommonName)
            }
            binding.listCandidates.addView(row)
        }
    }
}
