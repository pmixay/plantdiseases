package com.plantdiseases.app.ui.identify

import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.os.bundleOf
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.SpeciesCandidate
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivitySpeciesIdentifyBinding
import com.plantdiseases.app.ui.camera.CameraFragment
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import java.io.File
import java.util.Locale

/**
 * "Identify my plant" — a full live-camera screen that mirrors the disease
 * scanner. It hosts the shared [CameraFragment] in identify mode (same
 * preview, shutter, gallery, flash, grid, pinch-zoom, tap-to-focus, crop);
 * when a photo is captured the fragment hands the cropped path back here, we
 * run PlantNet identification, and the candidate picker slides up over the
 * camera. Tapping a candidate opens the rich [SpeciesDetailActivity].
 */
class SpeciesIdentifyActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySpeciesIdentifyBinding

    private val scanRepository: ScanRepository by inject()
    private val connectivity: ConnectivityObserver by inject()

    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
    private val locale: String get() = if (isRu) "ru" else "en"

    private val requestCamera =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            // No-op: CameraFragment.onResume starts the preview once granted.
        }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySpeciesIdentifyBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Edge-to-edge with the camera framed inside the safe area (black bars
        // behind the system bars), matching how the scan camera is framed.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.identifyRoot) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.updatePadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        binding.offlineNotice.observe(this, connectivity)

        binding.btnClose.setOnClickListener { finish() }
        binding.btnRetake.setOnClickListener { hideCandidates() }

        // Back dismisses the candidate panel (return to the live camera)
        // before leaving the screen.
        onBackPressedDispatcher.addCallback(this) {
            if (binding.candidatesPanel.visibility == View.VISIBLE) {
                hideCandidates()
            } else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(
                    R.id.camera_container,
                    CameraFragment().apply {
                        arguments = bundleOf(CameraFragment.ARG_IDENTIFY_MODE to true)
                    },
                )
                .commit()
        }

        // Receive the captured+cropped photo from the camera fragment.
        supportFragmentManager.setFragmentResultListener(
            CameraFragment.RESULT_IDENTIFY_CAPTURE,
            this,
        ) { _, result ->
            result.getString(CameraFragment.RESULT_PATH)?.let { runIdentify(File(it)) }
        }

        ensureCameraPermission()
    }

    private fun ensureCameraPermission() {
        val granted = ContextCompat.checkSelfPermission(
            this,
            android.Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) requestCamera.launch(android.Manifest.permission.CAMERA)
    }

    // ── Identify ────────────────────────────────────────────────────
    private fun runIdentify(file: File) {
        binding.candidatesPanel.visibility = View.GONE
        binding.identifyProgress.visibility = View.VISIBLE
        lifecycleScope.launch {
            val result = scanRepository.identifySpecies(file, "auto", locale)
            binding.identifyProgress.visibility = View.GONE
            result.onSuccess { resp ->
                when {
                    !resp.available -> showStatus(getString(R.string.identify_unavailable))
                    resp.results.isEmpty() -> showStatus(getString(R.string.identify_no_match))
                    else -> openTopCandidate(resp.results)
                }
            }.onFailure {
                showStatus(it.localizedMessage ?: getString(R.string.identify_failed))
            }
        }
    }

    /** Auto-open the single best match — no "which is yours?" picker, mirroring
     *  how the disease scan jumps straight to its top diagnosis. Picks the
     *  highest-scoring candidate with a usable scientific name; if none has one,
     *  degrade to the no-match status (same as an empty result set). */
    private fun openTopCandidate(candidates: List<SpeciesCandidate>) {
        val top = candidates
            .filter { !it.scientificName.isNullOrBlank() }
            .maxByOrNull { it.score }
        val sci = top?.scientificName
        if (sci.isNullOrBlank()) {
            showStatus(getString(R.string.identify_no_match))
            return
        }
        // Return to the live camera underneath so backing out of the detail
        // screen lands on a fresh capture, not a stale panel.
        hideCandidates()
        SpeciesDetailActivity.start(this, sci, top.displayCommonName)
    }

    /** Show the panel with a status line instead of opening a match (no match,
     *  unavailable, or an error). The picker title is hidden — the panel is now
     *  status-only. */
    private fun showStatus(msg: String) {
        binding.candidatesList.removeAllViews()
        binding.candidatesTitle.visibility = View.GONE
        binding.candidatesStatus.text = msg
        binding.candidatesStatus.visibility = View.VISIBLE
        showCandidates()
    }

    private fun showCandidates() {
        binding.candidatesPanel.visibility = View.VISIBLE
    }

    private fun hideCandidates() {
        binding.candidatesPanel.visibility = View.GONE
    }
}
