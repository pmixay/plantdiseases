package com.plantdiseases.app.ui.identify

import android.content.Context
import android.content.Intent
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
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.SpeciesCandidate
import com.plantdiseases.app.databinding.ActivitySpeciesIdentifyBinding
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Locale
import kotlin.math.roundToInt

/**
 * "Identify my plant" — the species-first entry point promoted from
 * background enrichment to a headline feature (see docs/ROADMAP.md §3).
 * Take/pick a photo, choose the photographed organ, identify via the
 * server's PlantNet proxy, then drill into a candidate: Wikipedia summary
 * + lead photo (/api/species-info), a care summary (/api/care-plan with
 * disease="healthy"), and add-to-garden / scan-health follow-ups.
 */
class SpeciesIdentifyActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySpeciesIdentifyBinding

    private var photoFile: File? = null
    private var pendingCameraUri: Uri? = null
    private var selectedOrgan: String = "leaf"
    private var selectedCandidate: SpeciesCandidate? = null

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
            binding.identifyToolbar.updatePadding(top = bars.top)
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
            binding.cardDetail.visibility = View.GONE
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
        binding.cardDetail.visibility = View.GONE
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
        candidates.forEachIndexed { i, c ->
            val sci = c.scientificName ?: return@forEachIndexed
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
            row.setOnClickListener { showDetail(c) }
            binding.listCandidates.addView(row)
            if (i == 0) showDetail(c)   // auto-expand the best match
        }
    }

    // ── Species detail (Wikipedia + care plan) ──────────────────────
    private fun showDetail(c: SpeciesCandidate) {
        val sci = c.scientificName ?: return
        selectedCandidate = c
        val app = application as PlantDiseasesApp
        binding.cardDetail.visibility = View.VISIBLE
        binding.tvSpeciesName.text = c.displayCommonName ?: sci
        binding.tvSpeciesSci.text = listOfNotNull(
            sci, c.family?.let { getString(R.string.identify_family, it) },
        ).joinToString(" · ")
        binding.tvSpeciesExtract.text = getString(R.string.identify_loading)
        binding.ivSpecies.visibility = View.GONE
        binding.btnWikipedia.visibility = View.GONE
        binding.tvCareTitle.visibility = View.GONE
        binding.tvCareBody.visibility = View.GONE

        binding.btnAddGarden.setOnClickListener { addToGarden(c) }
        binding.btnScanHealth.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    putExtra(MainActivity.EXTRA_OPEN_SCANNER, true)
                },
            )
        }

        lifecycleScope.launch {
            // Wikipedia summary + lead image (best-effort).
            val info = runCatching {
                app.apiClient.apiService.speciesInfo(sci, locale).body()
            }.getOrNull()
            if (info?.available == true) {
                binding.tvSpeciesExtract.text =
                    info.extract ?: getString(R.string.identify_no_info)
                info.imageUrl?.let { url ->
                    binding.ivSpecies.visibility = View.VISIBLE
                    Glide.with(this@SpeciesIdentifyActivity).load(url).into(binding.ivSpecies)
                }
                info.pageUrl?.let { url ->
                    binding.btnWikipedia.visibility = View.VISIBLE
                    binding.btnWikipedia.setOnClickListener {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    }
                }
            } else {
                binding.tvSpeciesExtract.text = getString(R.string.identify_no_info)
            }
            // Healthy-baseline care summary for this species.
            val plan = runCatching {
                app.apiClient.apiService.carePlan(
                    CarePlanRequest(disease = "healthy", species = sci, locale = locale),
                ).body()
            }.getOrNull()
            if (plan != null) {
                val watering = when {
                    plan.wateringDaysIsAsNeeded -> getString(R.string.identify_water_as_needed)
                    plan.wateringDaysInt != null ->
                        getString(R.string.identify_water_days, plan.wateringDaysInt)
                    else -> null
                }
                val lines = listOfNotNull(
                    getString(R.string.identify_light_tier, prettyTier(plan.lightTier)),
                    watering,
                    plan.humidityTarget.takeIf { it.isNotBlank() }
                        ?.let { getString(R.string.identify_humidity, it) },
                    if (plan.toxicToPets) getString(R.string.identify_toxic_pets) else null,
                )
                if (lines.isNotEmpty()) {
                    binding.tvCareTitle.visibility = View.VISIBLE
                    binding.tvCareBody.visibility = View.VISIBLE
                    binding.tvCareBody.text = lines.joinToString("\n")
                }
            }
        }
    }

    private fun prettyTier(tier: String): String = when (tier) {
        "low" -> getString(R.string.identify_light_low)
        "medium" -> getString(R.string.identify_light_medium)
        "direct" -> getString(R.string.identify_light_direct)
        else -> getString(R.string.identify_light_bright_indirect)
    }

    private fun addToGarden(c: SpeciesCandidate) {
        val sci = c.scientificName ?: return
        val app = application as PlantDiseasesApp
        lifecycleScope.launch {
            runCatching {
                app.plantRepository.savePlant(
                    name = c.displayCommonName ?: sci,
                    species = sci,
                    scientificName = sci,
                    commonName = c.displayCommonName,
                )
            }.onSuccess {
                Toast.makeText(
                    this@SpeciesIdentifyActivity,
                    R.string.identify_added_garden, Toast.LENGTH_SHORT,
                ).show()
            }.onFailure {
                Toast.makeText(
                    this@SpeciesIdentifyActivity,
                    R.string.identify_failed, Toast.LENGTH_SHORT,
                ).show()
            }
        }
    }
}
