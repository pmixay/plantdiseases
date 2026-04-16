package com.plantdiseases.app.ui.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.LayoutInflater
import android.view.MotionEvent
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
import com.plantdiseases.app.util.ImageUtils
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class CameraFragment : Fragment() {

    private var _binding: FragmentCameraBinding? = null
    private val binding get() = _binding!!

    private var imageCapture: ImageCapture? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var camera: androidx.camera.core.Camera? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    // Permission launcher
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

    // Gallery picker launcher
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
        checkCameraPermission()
        loadRecentScans()
        registerNetworkCallback()
    }

    private fun setupUI() {
        // Capture button
        binding.btnCapture.setOnClickListener {
            takePhoto()
        }

        // Gallery button
        binding.btnGallery.setOnClickListener {
            galleryLauncher.launch("image/*")
        }

        // Flash toggle
        binding.btnFlash.setOnClickListener {
            toggleFlash()
        }

        // Permission button
        binding.btnGrantPermission.setOnClickListener {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }

        // Tip text
        binding.tvScanHint.text = getString(R.string.scan_hint)

        // Tap to focus on camera preview
        binding.cameraPreview.setOnTouchListener { _, event ->
            if (event.action == MotionEvent.ACTION_DOWN) {
                handleTapToFocus(event.x, event.y)
            }
            true
        }
    }

    private fun handleTapToFocus(x: Float, y: Float) {
        val cam = camera ?: return
        val factory = binding.cameraPreview.meteringPointFactory
        val point = factory.createPoint(x, y)
        val action = FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF)
            .setAutoCancelDuration(3, java.util.concurrent.TimeUnit.SECONDS)
            .build()
        cam.cameraControl.startFocusAndMetering(action)

        // Show a brief focus indicator
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
                .withEndAction {
                    indicator.animate()
                        .alpha(0f)
                        .setDuration(500)
                        .setStartDelay(600)
                        .withEndAction { indicator.visibility = View.GONE }
                        .start()
                }
                .start()
        }
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
                    it.surfaceProvider = binding.cameraPreview.surfaceProvider
                }

            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .setTargetRotation(requireView().display.rotation)
                .build()

            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

            try {
                cameraProvider?.unbindAll()
                camera = cameraProvider?.bindToLifecycle(
                    viewLifecycleOwner, cameraSelector, preview, imageCapture
                )

                // Enable torch control
                binding.btnFlash.visibility =
                    if (camera?.cameraInfo?.hasFlashUnit() == true) View.VISIBLE else View.GONE

            } catch (e: Exception) {
                Toast.makeText(requireContext(), getString(R.string.camera_error), Toast.LENGTH_SHORT).show()
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    private fun takePhoto() {
        val imageCapture = imageCapture ?: return

        binding.btnCapture.isEnabled = false

        // Vibrate on capture
        vibrateOnCapture()

        val photoFile = ImageUtils.createImageFile(requireContext())

        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()

        imageCapture.takePicture(
            outputOptions,
            ContextCompat.getMainExecutor(requireContext()),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    binding.btnCapture.isEnabled = true
                    checkPlantAndNavigate(photoFile.absolutePath)
                }

                override fun onError(exception: ImageCaptureException) {
                    binding.btnCapture.isEnabled = true
                    Toast.makeText(
                        requireContext(),
                        getString(R.string.capture_error),
                        Toast.LENGTH_SHORT
                    ).show()
                }
            }
        )
    }

    private fun vibrateOnCapture() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vibratorManager = requireContext().getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
                vibratorManager.defaultVibrator.vibrate(
                    VibrationEffect.createOneShot(50, VibrationEffect.DEFAULT_AMPLITUDE)
                )
            } else {
                @Suppress("DEPRECATION")
                val vibrator = requireContext().getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
                vibrator.vibrate(VibrationEffect.createOneShot(50, VibrationEffect.DEFAULT_AMPLITUDE))
            }
        } catch (_: Exception) {
            // Vibration not available — ignore
        }
    }

    /**
     * HSV-based plant detection: checks if at least 10% of pixels are green-ish.
     * If not enough green detected, shows warning before proceeding.
     */
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
    }

    private fun registerNetworkCallback() {
        val cm = requireContext().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

        // Check initial state
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
        networkCallback?.let {
            val cm = requireContext().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            cm.unregisterNetworkCallback(it)
        }
        networkCallback = null
        _binding = null
    }
}
