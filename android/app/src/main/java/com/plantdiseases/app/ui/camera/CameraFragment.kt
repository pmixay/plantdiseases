package com.plantdiseases.app.ui.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.util.Size
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.Surface
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentCameraBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class CameraFragment : Fragment() {

    private var _binding: FragmentCameraBinding? = null
    private val binding get() = _binding!!

    private var imageCapture: ImageCapture? = null
    private var imageAnalysis: ImageAnalysis? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private lateinit var scaleGestureDetector: ScaleGestureDetector
    private var analyzerExecutor: ExecutorService? = null

    // Auto-capture state. We require the frame analyzer to report
    // GREEN continuously for AUTO_CAPTURE_HOLD_MS *and* a Laplacian
    // variance above MIN_SHARPNESS before triggering the shutter.
    private var greenStableSince: Long = 0L
    private var autoCaptureInFlight = false
    private var lastQuality: LeafFrameAnalyzer.Quality? = null

    private val PREFS_NAME = "plantdiseases_prefs"
    private val KEY_GRID_OVERLAY = "grid_overlay_enabled"

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startCamera()
        } else {
            binding.permissionLayout.visibility = View.VISIBLE
            binding.cameraPreview.visibility = View.GONE
        }
    }

    private val galleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        uri?.let { handleGalleryImage(it) }
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentCameraBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        setupUI()
        setupPinchToZoom()
        checkCameraPermission()
        loadRecentScans()
        registerNetworkCallback()
        restoreGridOverlay()
        applyFrameQuality(LeafFrameAnalyzer.Quality.RED)
    }

    private fun setupUI() {
        binding.btnCapture.setOnClickListener { takePhoto(auto = false) }
        binding.btnGallery.setOnClickListener {
            // Freeze auto-capture while the gallery picker is alive AND
            // until either the user returns with an image (we navigate
            // away) or cancels (we re-arm in onResume).
            autoCaptureInFlight = true
            greenStableSince = 0L
            galleryLauncher.launch("image/*")
        }
        binding.btnFlash.setOnClickListener { toggleFlash() }
        binding.btnGrantPermission.setOnClickListener {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
        binding.tvScanHint.text = getString(R.string.frame_hint_red)

        binding.cameraPreview.setOnTouchListener { _, event ->
            scaleGestureDetector.onTouchEvent(event)
            if (event.action == MotionEvent.ACTION_DOWN && !scaleGestureDetector.isInProgress) {
                handleTapToFocus(event.x, event.y)
            }
            true
        }
    }

    private fun setupPinchToZoom() {
        scaleGestureDetector = ScaleGestureDetector(requireContext(), object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                val cam = camera ?: return false
                val currentZoomRatio = cam.cameraInfo.zoomState.value?.zoomRatio ?: 1f
                val newZoomRatio = (currentZoomRatio * detector.scaleFactor)
                    .coerceIn(
                        cam.cameraInfo.zoomState.value?.minZoomRatio ?: 1f,
                        cam.cameraInfo.zoomState.value?.maxZoomRatio ?: 4f
                    )
                cam.cameraControl.setZoomRatio(newZoomRatio)

                binding.tvZoom.visibility = View.VISIBLE
                binding.tvZoom.text = String.format(Locale.US, "%.1fx", newZoomRatio)
                binding.tvZoom.removeCallbacks(hideZoomRunnable)
                binding.tvZoom.postDelayed(hideZoomRunnable, 2000)
                return true
            }
        })
    }

    private val hideZoomRunnable = Runnable {
        _binding?.tvZoom?.animate()?.alpha(0f)?.setDuration(300)?.withEndAction {
            _binding?.tvZoom?.visibility = View.GONE
            _binding?.tvZoom?.alpha = 1f
        }?.start()
    }

    private fun handleTapToFocus(x: Float, y: Float) {
        val cam = camera ?: return
        val factory = binding.cameraPreview.meteringPointFactory
        val point = factory.createPoint(x, y)
        val action = FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF)
            .setAutoCancelDuration(3, java.util.concurrent.TimeUnit.SECONDS)
            .build()

        val future = cam.cameraControl.startFocusAndMetering(action)

        binding.focusIndicator.let { indicator ->
            indicator.x = x - indicator.width / 2f
            indicator.y = y - indicator.height / 2f
            indicator.visibility = View.VISIBLE
            indicator.alpha = 1f
            indicator.scaleX = 1.3f
            indicator.scaleY = 1.3f
            indicator.animate()
                .scaleX(1f)
                .scaleY(1f)
                .setDuration(200)
                .start()
        }

        future.addListener({
            try {
                val result = future.get()
                if (_binding == null) return@addListener
                if (result.isFocusSuccessful) {
                    binding.focusIndicator.backgroundTintList =
                        ColorStateList.valueOf(
                            ContextCompat.getColor(requireContext(), R.color.healthy_green)
                        )
                    Haptics.light(requireContext())
                    binding.focusIndicator.animate()
                        .alpha(0f)
                        .setDuration(500)
                        .setStartDelay(400)
                        .withEndAction {
                            _binding?.focusIndicator?.visibility = View.GONE
                            _binding?.focusIndicator?.backgroundTintList = null
                        }
                        .start()
                } else {
                    binding.focusIndicator.backgroundTintList =
                        ColorStateList.valueOf(
                            ContextCompat.getColor(requireContext(), R.color.disease_red)
                        )
                    binding.focusIndicator.animate()
                        .alpha(0f)
                        .setDuration(500)
                        .setStartDelay(600)
                        .withEndAction {
                            _binding?.focusIndicator?.visibility = View.GONE
                            _binding?.focusIndicator?.backgroundTintList = null
                        }
                        .start()
                }
            } catch (_: Exception) {
                _binding?.focusIndicator?.animate()
                    ?.alpha(0f)
                    ?.setDuration(500)
                    ?.setStartDelay(600)
                    ?.withEndAction { _binding?.focusIndicator?.visibility = View.GONE }
                    ?.start()
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    private fun checkCameraPermission() {
        when {
            ContextCompat.checkSelfPermission(
                requireContext(), Manifest.permission.CAMERA
            ) == PackageManager.PERMISSION_GRANTED -> {
                startCamera()
            }
            else -> {
                permissionLauncher.launch(Manifest.permission.CAMERA)
            }
        }
    }

    private fun startCamera() {
        binding.permissionLayout.visibility = View.GONE
        binding.cameraPreview.visibility = View.VISIBLE

        val cameraProviderFuture = ProcessCameraProvider.getInstance(requireContext())
        cameraProviderFuture.addListener({
            cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder()
                .build()
                .also {
                    it.setSurfaceProvider(binding.cameraPreview.surfaceProvider)
                }

            val rotation = view?.display?.rotation
                ?: requireActivity().windowManager.defaultDisplay?.rotation
                ?: Surface.ROTATION_0
            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .setTargetRotation(rotation)
                .build()

            // Real-time framing analyzer. STRATEGY_KEEP_ONLY_LATEST drops
            // backpressured frames so we never queue work behind the
            // preview when the device is under load. setTargetResolution
            // is deprecated in CameraX 1.3 in favour of ResolutionSelector
            // but still works and keeps the dependency surface minimal.
            val executor = analyzerExecutor ?: Executors.newSingleThreadExecutor()
                .also { analyzerExecutor = it }
            @Suppress("DEPRECATION")
            val analysisBuilder = ImageAnalysis.Builder()
                .setTargetResolution(Size(320, 240))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            imageAnalysis = analysisBuilder
                .build()
                .apply {
                    setAnalyzer(executor, LeafFrameAnalyzer { signal ->
                        view?.post { onFrameSignal(signal) }
                    })
                }

            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

            try {
                cameraProvider?.unbindAll()
                camera = cameraProvider?.bindToLifecycle(
                    viewLifecycleOwner, cameraSelector,
                    preview, imageCapture, imageAnalysis,
                )

                binding.btnFlash.visibility =
                    if (camera?.cameraInfo?.hasFlashUnit() == true) View.VISIBLE else View.GONE

            } catch (e: Exception) {
                Toast.makeText(requireContext(), getString(R.string.camera_error), Toast.LENGTH_SHORT).show()
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    private fun onFrameSignal(signal: LeafFrameAnalyzer.FrameSignal) {
        if (_binding == null || autoCaptureInFlight) return
        applyFrameQuality(signal.quality)

        // Auto-capture: green-stable for AUTO_CAPTURE_HOLD_MS AND
        // sharpness above MIN_SHARPNESS. The Laplacian-variance proxy
        // is computed on the green channel of the same downsampled
        // grid the framing logic uses, so it costs nothing extra.
        val now = SystemClock.elapsedRealtime()
        if (signal.quality == LeafFrameAnalyzer.Quality.GREEN) {
            if (greenStableSince == 0L) greenStableSince = now
            val held = now - greenStableSince
            if (held >= AUTO_CAPTURE_HOLD_MS && signal.sharpness >= MIN_SHARPNESS) {
                autoCaptureInFlight = true
                takePhoto(auto = true)
            }
        } else {
            greenStableSince = 0L
        }
    }

    private fun applyFrameQuality(quality: LeafFrameAnalyzer.Quality) {
        if (lastQuality == quality) return
        lastQuality = quality
        val hintRes = when (quality) {
            LeafFrameAnalyzer.Quality.GREEN -> R.string.frame_hint_green
            LeafFrameAnalyzer.Quality.AMBER -> R.string.frame_hint_amber
            LeafFrameAnalyzer.Quality.RED -> R.string.frame_hint_red
        }
        binding.tvScanHint.setText(hintRes)
    }

    private fun takePhoto(auto: Boolean) {
        val imageCapture = imageCapture ?: run {
            autoCaptureInFlight = false
            return
        }

        binding.btnCapture.isEnabled = false

        Haptics.medium(requireContext())
        playShutterFlash()

        val photoFile = ImageUtils.createImageFile(requireContext())
        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()

        imageCapture.takePicture(
            outputOptions,
            ContextCompat.getMainExecutor(requireContext()),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    binding.btnCapture.isEnabled = true
                    autoCaptureInFlight = false
                    greenStableSince = 0L
                    if (auto) {
                        // Stable-green + sharp: skip the green-content
                        // dialog because we already validated both.
                        navigateToAnalysis(photoFile.absolutePath)
                    } else {
                        checkPlantAndNavigate(photoFile.absolutePath)
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    binding.btnCapture.isEnabled = true
                    autoCaptureInFlight = false
                    Toast.makeText(
                        requireContext(),
                        getString(R.string.capture_error),
                        Toast.LENGTH_SHORT
                    ).show()
                }
            }
        )
    }

    private fun playShutterFlash() {
        binding.shutterFlash.visibility = View.VISIBLE
        binding.shutterFlash.alpha = 0f
        binding.shutterFlash.animate()
            .alpha(0.7f)
            .setDuration(50)
            .withEndAction {
                _binding?.shutterFlash?.animate()
                    ?.alpha(0f)
                    ?.setDuration(100)
                    ?.withEndAction {
                        _binding?.shutterFlash?.visibility = View.GONE
                    }
                    ?.start()
            }
            .start()
    }

    private fun checkPlantAndNavigate(imagePath: String) {
        binding.btnCapture.isEnabled = false
        lifecycleScope.launch {
            val hasGreen = withContext(Dispatchers.Default) {
                ImageUtils.hasGreenContent(imagePath)
            }
            if (_binding == null) return@launch
            binding.btnCapture.isEnabled = true
            if (!hasGreen) {
                MaterialAlertDialogBuilder(requireContext())
                    .setTitle(R.string.not_a_plant_title)
                    .setMessage(R.string.not_a_plant_warning)
                    .setPositiveButton(R.string.continue_anyway) { _, _ ->
                        navigateToAnalysis(imagePath)
                    }
                    .setNegativeButton(R.string.cancel, null)
                    .show()
            } else {
                navigateToAnalysis(imagePath)
            }
        }
    }

    private fun handleGalleryImage(uri: Uri) {
        val file = ImageUtils.copyUriToFile(requireContext(), uri)
        if (file != null) {
            checkPlantAndNavigate(file.absolutePath)
        } else {
            Toast.makeText(requireContext(), getString(R.string.image_load_error), Toast.LENGTH_SHORT).show()
        }
    }

    private fun navigateToAnalysis(imagePath: String) {
        // Freeze auto-capture the moment we know we're leaving —
        // startActivity() is async and the analyzer keeps producing
        // frames until AnalysisActivity is RESUMED. Without this a
        // gallery-selected image could race with a stale auto-capture
        // shutter call, double-firing the analysis flow.
        autoCaptureInFlight = true
        greenStableSince = 0L
        val intent = Intent(requireContext(), AnalysisActivity::class.java).apply {
            putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, imagePath)
        }
        startActivity(intent)
    }

    private fun toggleFlash() {
        val cam = camera ?: return
        val torchOn = cam.cameraInfo.torchState.value == TorchState.ON
        cam.cameraControl.enableTorch(!torchOn)
        binding.btnFlash.setImageResource(
            if (!torchOn) R.drawable.ic_flash_on else R.drawable.ic_flash_off
        )
    }

    private fun restoreGridOverlay() {
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val enabled = prefs.getBoolean(KEY_GRID_OVERLAY, false)
        binding.gridOverlay.visibility = if (enabled) View.VISIBLE else View.GONE
    }

    fun toggleGridOverlay() {
        val isVisible = binding.gridOverlay.visibility == View.VISIBLE
        binding.gridOverlay.visibility = if (isVisible) View.GONE else View.VISIBLE
        requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_GRID_OVERLAY, !isVisible).apply()
    }

    private fun loadRecentScans() {
        val app = requireActivity().application as PlantDiseasesApp
        lifecycleScope.launch {
            val recent = app.scanRepository.getRecentScans(5)
            if (recent.isNotEmpty()) {
                binding.recentScansLayout.visibility = View.VISIBLE
                binding.rvRecentScans.adapter = RecentScanAdapter(recent) { item ->
                    val intent = Intent(requireContext(), com.plantdiseases.app.ui.result.ResultActivity::class.java).apply {
                        putExtra(com.plantdiseases.app.ui.result.ResultActivity.EXTRA_SCAN_ID, item.id)
                    }
                    startActivity(intent)
                }
            } else {
                binding.recentScansLayout.visibility = View.GONE
            }
        }
    }

    override fun onResume() {
        super.onResume()
        loadRecentScans()
        // Wipe stale auto-capture state so coming back from another
        // screen doesn't fire the shutter while the user is still
        // re-orienting.
        greenStableSince = 0L
        autoCaptureInFlight = false
    }

    private fun registerNetworkCallback() {
        val cm = requireContext().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

        val activeNetwork = cm.getNetworkCapabilities(cm.activeNetwork)
        val isConnected = activeNetwork?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
        binding.offlineBanner.visibility = if (isConnected) View.GONE else View.VISIBLE

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                view?.post { _binding?.offlineBanner?.visibility = View.GONE }
            }

            override fun onLost(network: Network) {
                view?.post { _binding?.offlineBanner?.visibility = View.VISIBLE }
            }
        }
        networkCallback = callback

        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        cm.registerNetworkCallback(request, callback)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        // Drain any pending zoom-pill hide before the binding goes — the
        // runnable null-checks _binding, but cancelling it avoids a
        // dangling Choreographer callback on the destroyed view.
        binding.tvZoom.removeCallbacks(hideZoomRunnable)
        networkCallback?.let {
            val cm = requireContext().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            cm.unregisterNetworkCallback(it)
        }
        networkCallback = null
        analyzerExecutor?.shutdown()
        analyzerExecutor = null
        imageAnalysis = null
        _binding = null
    }

    companion object {
        // Hold time before auto-capture fires. Bumped from 1.5 s to
        // 2.0 s so the user has to actually compose the shot — a
        // glance across a green object no longer triggers it.
        private const val AUTO_CAPTURE_HOLD_MS = 2000L
        // Sharpness floor on the global green-channel Laplacian
        // variance. Hand-held indoor shots of plants score 200+,
        // severely blurred ones below 60. Raised from 120 to 220 so
        // smooth-textured greens (a wall, a couch fabric) get
        // rejected even when the analyzer is otherwise convinced.
        private const val MIN_SHARPNESS = 220.0
    }
}
