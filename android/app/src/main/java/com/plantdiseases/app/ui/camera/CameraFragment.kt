// The vanniktech android-image-cropper deprecated its own CropImageContract /
// CropImageContractOptions API (4.x) without shipping a replacement — the
// guidance is to keep using it or copy the contract verbatim. We keep using it,
// so the deprecation is suppressed file-wide. Remove this when migrating off
// the cropper; it is the only deprecated dependency in this file.
@file:Suppress("DEPRECATION")

package com.plantdiseases.app.ui.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.Color
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.os.bundleOf
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.canhub.cropper.CropImageContract
import com.canhub.cropper.CropImageContractOptions
import com.canhub.cropper.CropImageOptions
import com.canhub.cropper.CropImageView
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentCameraBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.AutosavePrefs
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.ModelFamilyPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Locale

class CameraFragment : Fragment() {

    private var _binding: FragmentCameraBinding? = null
    private val binding get() = _binding!!

    private var imageCapture: ImageCapture? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private lateinit var scaleGestureDetector: ScaleGestureDetector

    private var captureInFlight = false

    /** When true, the camera runs as the species-identification front-end
     *  (hosted by SpeciesIdentifyActivity): no model picker / scan-tips /
     *  community menu, and a captured+cropped photo is handed back to the
     *  host via [RESULT_IDENTIFY_CAPTURE] instead of opening disease analysis.
     *  Defaults to false, so the disease scan tab is unaffected. */
    private val identifyMode: Boolean
        get() = arguments?.getBoolean(ARG_IDENTIFY_MODE, false) ?: false

    /** Destination file for the in-flight crop. We give the cropper an
     *  explicit unique output file so each scan produces a distinct path
     *  — both for the preview (Glide caches by path) and analysis. */
    private var pendingCropFile: File? = null

    /** The pre-crop original handed to the cropper as its source (the raw
     *  camera capture or the gallery copy). It is never used downstream — the
     *  saved scan uses the cropper's output — so it is deleted once the crop
     *  returns. Tracked here so cleanup works on both the success and the
     *  cancel path. */
    private var pendingSourceFile: File? = null

    /** True when the in-flight crop originated from the in-app camera (not a
     *  gallery import). Drives autosave-to-album: we only copy photos the user
     *  actually snapped into the device gallery — re-saving a gallery pick
     *  would just duplicate it. */
    private var pendingFromCamera = false

    private val PREFS_NAME = "plantdiseases_prefs"
    private val KEY_GRID_OVERLAY = "grid_overlay_enabled"
    private val KEY_TIPS_SEEN = "scan_tips_seen"

    companion object {
        const val TAG = "CameraFragment"

        /** Set in the fragment arguments to run the camera as a species
         *  identification front-end (see [identifyMode]). */
        const val ARG_IDENTIFY_MODE = "identify_mode"

        /** Fragment-result key the host listens on; the bundle carries the
         *  cropped photo path under [RESULT_PATH]. */
        const val RESULT_IDENTIFY_CAPTURE = "camera_identify_capture"
        const val RESULT_PATH = "path"
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startCamera()
        } else {
            // Guard like every other async callback in this file — `binding`
            // is a !! getter that NPEs after onDestroyView.
            if (_binding == null) return@registerForActivityResult
            binding.permissionLayout.visibility = View.VISIBLE
            binding.cameraPreview.visibility = View.GONE
        }
    }

    private val galleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        uri?.let { handleGalleryImage(it) }
    }

    /** Crop editor (vanniktech android-image-cropper). Both the camera
     *  shutter and the gallery picker funnel their image through here
     *  before it reaches analysis. The cropper writes the result to the
     *  unique [pendingCropFile] we hand it; that exact path goes on to
     *  AnalysisActivity for both the preview and the upload. */
    private val cropLauncher = registerForActivityResult(CropImageContract()) { result ->
        if (_binding == null) return@registerForActivityResult
        // Returning from the crop activity re-arms the shutter regardless
        // of outcome; navigateToAnalysis re-locks it on success.
        captureInFlight = false
        binding.btnCapture.isEnabled = true
        // Success is defined by BYTES ON DISK, not the library's result flag.
        // The cropper writes to our customOutputUri (pendingCropFile); a
        // back-press out of the crop screen returns either a null error or a
        // cancellation error depending on the cropper version, and the old
        // `result.error != null` branch toasted "Failed to crop" on that
        // ordinary cancel. Treat "no non-empty output file" as a benign cancel:
        // stay on the camera silently and drop the staged file. Only a genuine
        // crop with bytes on disk advances to analysis.
        val cropped = pendingCropFile
        if (result.isSuccessful && cropped != null && cropped.exists() && cropped.length() > 0L) {
            if (identifyMode) {
                // Hand the cropped photo back to the host (SpeciesIdentify-
                // Activity) to run species identification on it.
                deliverIdentifyCapture(cropped.absolutePath)
            } else {
                // Autosave the kept (cropped) photo to the device gallery when the
                // user opted in AND this scan came from the camera, not a gallery
                // import. Fire-and-forget off the main thread so a slow MediaStore
                // write never stalls the hop into analysis.
                if (pendingFromCamera && AutosavePrefs.enabled(requireContext())) {
                    val toSave = cropped
                    val appContext = requireContext().applicationContext
                    val album = getString(R.string.autosave_album)
                    viewLifecycleOwner.lifecycleScope.launch(Dispatchers.IO) {
                        ImageUtils.saveImageToGallery(appContext, toSave, album)
                    }
                }
                navigateToAnalysis(cropped.absolutePath)
            }
            // The crop succeeded; the raw original is no longer needed.
            clearPendingSource()
        } else {
            if (result.error != null) Log.w(TAG, "Crop not completed", result.error)
            discardPendingCrop()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Deliver the first-run tips sheet's "Continue" tap via the Fragment
        // Result API rather than a transient lambda, so it survives a
        // config-change recreation (rotation / dark-mode / locale) while the
        // sheet is open. Registering with `this` re-arms on recreation and the
        // FragmentManager replays any pending result to the new listener.
        parentFragmentManager.setFragmentResultListener(
            ScanTipsSheet.REQUEST_KEY,
            this,
        ) { _, _ ->
            requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putBoolean(KEY_TIPS_SEEN, true).apply()
            continueCaptureFlow()
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
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
        registerNetworkCallback()
        restoreGridOverlay()
    }

    private fun setupUI() {
        binding.btnCapture.setOnClickListener { onCaptureClicked() }
        binding.btnGallery.setOnClickListener {
            galleryLauncher.launch("image/*")
        }
        binding.btnFlash.setOnClickListener { toggleFlash() }
        binding.btnGrantPermission.setOnClickListener {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }

        // The diagnosis-model picker is meaningless for species ID, so hide
        // the chip and skip its wiring in identify mode.
        if (identifyMode) {
            binding.btnModelFamily.visibility = View.GONE
        } else {
            refreshModelFamilyChip()
            // Fragment Result instead of a lambda field on the sheet: the
            // chip-refresh signal survives a config-change recreation while the
            // picker is open (re-registered here on every view creation).
            childFragmentManager.setFragmentResultListener(
                ModelPickerSheet.REQUEST_KEY,
                viewLifecycleOwner,
            ) { _, _ ->
                if (_binding != null) refreshModelFamilyChip()
            }
            binding.btnModelFamily.setOnClickListener {
                Haptics.tick(requireContext())
                ModelPickerSheet.show(childFragmentManager)
            }
            syncModelCapabilities()
        }

        binding.cameraPreview.setOnTouchListener { _, event ->
            scaleGestureDetector.onTouchEvent(event)
            if (event.action == MotionEvent.ACTION_DOWN && !scaleGestureDetector.isInProgress) {
                handleTapToFocus(event.x, event.y)
            }
            true
        }
    }

    /** Update the picker chip to show the currently-selected family. */
    private fun refreshModelFamilyChip() {
        val family = ModelFamilyPrefs.selected(requireContext())
        binding.btnModelFamily.setText(family.labelRes)
        binding.btnModelFamily.setIconResource(family.iconRes)
    }

    /** Refresh per-family availability from the server so the picker can grey
     *  out frontier when it isn't enabled, and download/update the Aqua
     *  on-device model when the server has one. Best-effort: failures keep the
     *  last-known state (frontier stays disabled; the existing Aqua model, if
     *  any, is untouched). */
    private fun syncModelCapabilities() {
        val app = requireActivity().application as PlantDiseasesApp
        viewLifecycleOwner.lifecycleScope.launch {
            app.scanRepository.fetchCapabilities().onSuccess { caps ->
                if (!isAdded) return@onSuccess
                val frontier = caps.families.firstOrNull { it.id == "frontier" }?.available ?: false
                ModelFamilyPrefs.setFrontierAvailable(requireContext(), frontier)
                // If frontier was selected but is no longer offered, the chip
                // must reflect the fallback selection.
                refreshModelFamilyChip()
            }
            // Pull the Aqua on-device model when the server hosts a new build.
            // Runs on the app scope so a tab switch mid-download doesn't cancel
            // it, and only transfers bytes when the sha actually changed.
            app.appScope.launch {
                app.scanRepository.syncAquaModel(requireContext().applicationContext)
            }
        }
    }

    private fun setupPinchToZoom() {
        scaleGestureDetector = ScaleGestureDetector(
            requireContext(),
            object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
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
            }
        )
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
        val granted = ContextCompat.checkSelfPermission(
            requireContext(),
            Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) {
            startCamera()
            return
        }
        // No auto-launch from the fragment: MainActivity batches the
        // startup permission ask (camera + location + notifications)
        // into a single RequestMultiplePermissions flow so the system
        // dialogs surface reliably. Firing another launch() here at
        // the same time used to race with that batch and Android
        // would silently auto-deny the camera prompt — landing the
        // user on this Grant-button screen without ever showing the
        // system dialog. ``onResume`` re-checks once the batch
        // completes so the camera starts as soon as the user grants.
        binding.permissionLayout.visibility = View.VISIBLE
        binding.cameraPreview.visibility = View.GONE
    }

    private fun startCamera() {
        binding.permissionLayout.visibility = View.GONE
        binding.cameraPreview.visibility = View.VISIBLE

        val cameraProviderFuture = ProcessCameraProvider.getInstance(requireContext())
        cameraProviderFuture.addListener({
            // The view may already be torn down if the user navigated away
            // during the async provider init — every binding access below
            // would NPE through the _binding!! getter. Bail out cleanly,
            // mirroring the guards already used in the other callbacks here.
            if (_binding == null || !isAdded) return@addListener
            cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder()
                .build()
                .also {
                    it.setSurfaceProvider(binding.cameraPreview.surfaceProvider)
                }

            val rotation = (
                view?.display
                    ?: ContextCompat.getDisplayOrDefault(requireContext())
                ).rotation
            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .setTargetRotation(rotation)
                .build()

            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

            try {
                cameraProvider?.unbindAll()
                camera = cameraProvider?.bindToLifecycle(
                    viewLifecycleOwner,
                    cameraSelector,
                    preview,
                    imageCapture,
                )

                // INVISIBLE (not GONE) when there's no flash unit so the
                // bottom row stays balanced and the shutter optically
                // centered — btn_flash mirrors the gallery button.
                binding.btnFlash.visibility =
                    if (camera?.cameraInfo?.hasFlashUnit() == true) View.VISIBLE else View.INVISIBLE
            } catch (e: Exception) {
                Toast.makeText(requireContext(), getString(R.string.camera_error), Toast.LENGTH_SHORT).show()
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    /** Explicit shutter click. Shows the pre-scan tips sheet the
     *  first time, otherwise falls straight through to capture. */
    private fun onCaptureClicked() {
        // The first-run scan-tips sheet is disease-scan guidance; identify
        // mode goes straight to capture.
        if (identifyMode) {
            continueCaptureFlow()
            return
        }
        val prefs = requireContext().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_TIPS_SEEN, false)) {
            // The Continue tap is handled by the Fragment Result listener
            // registered in onCreate (config-change safe).
            ScanTipsSheet.show(parentFragmentManager)
            return
        }
        continueCaptureFlow()
    }

    /** Second stage of the shutter flow. Community uploads are
     *  default-on and the toggle lives in Profile, so we skip
     *  straight to capture. */
    private fun continueCaptureFlow() {
        takePhoto()
    }

    private fun takePhoto() {
        if (captureInFlight) return
        val imageCapture = imageCapture ?: return

        captureInFlight = true
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
                    // Always re-arm the shutter state, then bail if the view
                    // is gone — touching `binding` or requireContext() after
                    // onDestroyView would crash.
                    captureInFlight = false
                    if (_binding == null || !isAdded) return
                    binding.btnCapture.isEnabled = true
                    // Server-side blur gate now lives on /api/analyze
                    // (variance-of-Laplacian floor). We no longer
                    // double-gate on green-content here — false
                    // positives on textured fabric hurt UX more than
                    // they help.
                    pendingSourceFile = photoFile
                    pendingFromCamera = true
                    // Shrink the full-res capture off the main thread before
                    // handing it to the cropper, then launch once it's done.
                    viewLifecycleOwner.lifecycleScope.launch {
                        withContext(Dispatchers.IO) {
                            ImageUtils.downscaleForCropInPlace(photoFile.absolutePath)
                        }
                        if (_binding == null || !isAdded) return@launch
                        launchCrop(Uri.fromFile(photoFile))
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    captureInFlight = false
                    if (_binding == null || !isAdded) return
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

    private fun handleGalleryImage(uri: Uri) {
        // Copy into our own files dir first so the cropper reads a stable
        // file:// uri rather than a transient content:// grant that may
        // not survive the activity hop. Runs off the main thread — a large or
        // cloud-backed (Google Photos) source can take a while to stream and
        // would ANR if copied on the UI thread.
        viewLifecycleOwner.lifecycleScope.launch {
            val file = withContext(Dispatchers.IO) {
                ImageUtils.copyUriToFile(requireContext(), uri)?.also {
                    // Shrink before cropping so the cropper lays the image out
                    // within its canvas instead of spilling under the toolbar.
                    ImageUtils.downscaleForCropInPlace(it.absolutePath)
                }
            }
            if (_binding == null || !isAdded) return@launch
            if (file != null) {
                pendingSourceFile = file
                pendingFromCamera = false
                launchCrop(Uri.fromFile(file))
            } else {
                Toast.makeText(requireContext(), getString(R.string.image_load_error), Toast.LENGTH_SHORT).show()
            }
        }
    }

    /** Open the crop editor on [sourceUri]. Free-form crop (no fixed
     *  aspect ratio); the result is written as a JPEG to a unique file we
     *  own so the preview and analysis both read the cropped image. */
    private fun launchCrop(sourceUri: Uri) {
        val outFile = File(
            File(requireContext().filesDir, "plant_images").apply { mkdirs() },
            "CROP_${System.currentTimeMillis()}.jpg",
        )
        pendingCropFile = outFile
        // Hand the cropper a content:// destination via our FileProvider, NOT
        // a raw file:// Uri. On targetSdk 24+ the cropper can't write a
        // file:// output (FileUriExposed / blocked stream), which surfaced as
        // "Failed to crop image" on every scan. The provider maps
        // filesDir/plant_images (see res/xml/file_paths.xml), so the bytes
        // still land in outFile and we read outFile.absolutePath on success.
        val outputUri = FileProvider.getUriForFile(
            requireContext(),
            "${requireContext().packageName}.fileprovider",
            outFile,
        )
        cropLauncher.launch(
            CropImageContractOptions(
                uri = sourceUri,
                cropImageOptions = CropImageOptions(
                    guidelines = CropImageView.Guidelines.ON,
                    fixAspectRatio = false,
                    customOutputUri = outputUri,
                    outputCompressFormat = Bitmap.CompressFormat.JPEG,
                    outputCompressQuality = 90,
                    // We supply the image ourselves, so hide the library's
                    // built-in source pickers.
                    imageSourceIncludeGallery = false,
                    imageSourceIncludeCamera = false,
                    activityTitle = getString(R.string.crop_title),
                    cropMenuCropButtonTitle = getString(R.string.crop_action_done),
                    // The action bar sits on the app's green gradient
                    // (see Theme.PlantDiseases.Crop), so force the title,
                    // back arrow and on-bar icons white for contrast.
                    // NOTE: do NOT set activityMenuTextColor — it whitens
                    // the flip submenu items too, which render in a white
                    // popup (white-on-white). DarkActionBar already makes
                    // the on-bar "Done" text white. Canvas backdrop follows
                    // the current day/night background.
                    activityBackgroundColor = ContextCompat.getColor(requireContext(), R.color.background),
                    toolbarTitleColor = Color.WHITE,
                    toolbarBackButtonColor = Color.WHITE,
                    activityMenuIconColor = Color.WHITE,
                ),
            )
        )
    }

    /** Delete the staged crop the user abandoned (cancelled or errored) so
     *  not-yet-analysed crops don't accumulate in filesDir/plant_images.
     *  Successful crops are kept — they become the saved scan's imagePath. */
    private fun discardPendingCrop() {
        pendingCropFile?.let { f -> runCatching { if (f.exists()) f.delete() } }
        pendingCropFile = null
        clearPendingSource()
    }

    /** Delete the pre-crop original (the raw camera capture or the gallery
     *  copy) once the cropper has written its own CROP_*.jpg. The original is
     *  only ever the cropper's input — the saved scan always uses the crop — so
     *  it is safe to remove on both the success and the cancel path. Without
     *  this, a full-resolution original leaked into filesDir/plant_images on
     *  every capture and every gallery import. */
    private fun clearPendingSource() {
        pendingSourceFile?.let { f -> runCatching { if (f.exists()) f.delete() } }
        pendingSourceFile = null
    }

    /** Identify-mode counterpart of [navigateToAnalysis]: return the cropped
     *  photo path to the host activity, which runs species identification. */
    private fun deliverIdentifyCapture(imagePath: String) {
        parentFragmentManager.setFragmentResult(
            RESULT_IDENTIFY_CAPTURE,
            bundleOf(RESULT_PATH to imagePath),
        )
    }

    private fun navigateToAnalysis(imagePath: String) {
        captureInFlight = true
        val intent = Intent(requireContext(), AnalysisActivity::class.java).apply {
            putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, imagePath)
        }
        startActivity(intent)
    }

    private fun toggleFlash() {
        val cam = camera ?: return
        val torchOn = cam.cameraInfo.torchState.value == TorchState.ON
        cam.cameraControl.enableTorch(!torchOn)
        // ``btn_flash`` is a MaterialButton (IconButton style) post-
        // redesign — set the icon via setIconResource, not the
        // ImageView-only setImageResource.
        binding.btnFlash.setIconResource(
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

    override fun onResume() {
        super.onResume()
        // Re-arm the shutter in case we came back from gallery /
        // analysis without a fresh photo (cancelled, error, etc.).
        captureInFlight = false
        // Re-sync the model-family chip from the persisted selection. Covers
        // the case where the picker sheet's callback was lost to a
        // config-change recreation while it was open — the chip would
        // otherwise keep showing the previously-selected family. (No chip in
        // identify mode.)
        if (_binding != null && !identifyMode) refreshModelFamilyChip()
        // If MainActivity's startup permission batch just finished and
        // CAMERA flipped to granted, start the preview now — onResume
        // is the first point we're sure the system dialog has been
        // dismissed and the permission state is stable.
        if (_binding != null &&
            binding.permissionLayout.visibility == View.VISIBLE &&
            ContextCompat.checkSelfPermission(
                requireContext(),
                Manifest.permission.CAMERA,
            ) == PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        }
    }

    private fun registerNetworkCallback() {
        val cm = requireContext().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

        // Seed from the current default network.
        updateOfflineBanner(hasUsableInternet(cm.getNetworkCapabilities(cm.activeNetwork)))

        // Track the DEFAULT network and react to capability changes — not just
        // onAvailable/onLost. The banner used to get stuck because reconnecting
        // (or the system re-validating internet on a network that was already
        // "available") fires onCapabilitiesChanged, not onAvailable, so the
        // GONE transition was never delivered until the app was restarted.
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                view?.post { updateOfflineBanner(hasUsableInternet(caps)) }
            }

            override fun onLost(network: Network) {
                view?.post { updateOfflineBanner(false) }
            }

            override fun onUnavailable() {
                view?.post { updateOfflineBanner(false) }
            }
        }
        networkCallback = callback
        cm.registerDefaultNetworkCallback(callback)
    }

    /** True only when the network can actually reach the internet (present AND
     *  validated by the system), so the banner reflects real reachability
     *  rather than just "a network object exists". */
    private fun hasUsableInternet(caps: NetworkCapabilities?): Boolean =
        caps != null &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)

    private fun updateOfflineBanner(online: Boolean) {
        _binding?.offlineBanner?.visibility = if (online) View.GONE else View.VISIBLE
    }

    override fun onDestroyView() {
        super.onDestroyView()
        // Explicitly release the camera the moment the Scan tab's view is torn
        // down (every tab switch, since the bottom nav destroys the inactive
        // tab). Relying on the view-lifecycle auto-unbind alone left the sensor
        // — and the OS camera-active indicator — held on the other tabs.
        // startCamera() re-acquires it when the tab is reopened.
        cameraProvider?.unbindAll()
        cameraProvider = null
        camera = null
        // Drain any pending zoom-pill hide before the binding goes — the
        // runnable null-checks _binding, but cancelling it avoids a
        // dangling Choreographer callback on the destroyed view.
        binding.tvZoom.removeCallbacks(hideZoomRunnable)
        networkCallback?.let {
            val cm = requireContext().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            cm.unregisterNetworkCallback(it)
        }
        networkCallback = null
        _binding = null
    }
}
