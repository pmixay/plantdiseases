package com.plantdiseases.app.ui.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.util.Size
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.*
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.os.bundleOf
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.FragmentCameraBinding
import com.plantdiseases.app.ui.analysis.AnalysisActivity
import com.plantdiseases.app.ui.crop.CropContract
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStepId
import com.plantdiseases.app.util.AquaClassifier
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.ModelFamilyPrefs
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.applyStatusBarTopInset
import com.plantdiseases.app.util.onConnectivityChanged
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.koin.android.ext.android.inject
import java.io.File
import java.util.Locale

class CameraFragment : Fragment() {

    private var _binding: FragmentCameraBinding? = null
    private val binding get() = _binding!!

    private val scanRepository: ScanRepository by inject()
    private val connectivity: ConnectivityObserver by inject()

    private var imageCapture: ImageCapture? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private lateinit var scaleGestureDetector: ScaleGestureDetector

    private var captureInFlight = false

    /** Latest reachability reading. Offline the scan screen is limited to the
     *  on-device model — or, without one, to explaining why it can't scan. */
    private var isOffline = false

    /** Monotonic timestamp of the last accepted shutter tap. A fast double-tap
     *  used to fire two captures / two croppers (and two first-run tips sheets)
     *  because the downstream `captureInFlight` guard isn't armed until the
     *  async capture starts and isn't armed at all on the tips-sheet path. This
     *  debounces the click itself, before any branch. */
    private var lastCaptureTapAt = 0L
    private val captureDebounceMs = 800L

    /** Whether we've already put the CAMERA dialog in front of the user during
     *  this fragment's life. The camera is opened from onResume, which runs
     *  again every time the system dialog closes — asking unconditionally would
     *  trap a user who denied it in a dialog loop. The permission_layout's own
     *  Grant button is the retry path. */
    private var permissionAsked = false

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

    companion object {
        const val TAG = "CameraFragment"

        private const val PREFS_NAME = "plantdiseases_prefs"
        private const val KEY_GRID_OVERLAY = "grid_overlay_enabled"

        /** Set in the fragment arguments to run the camera as a species
         *  identification front-end (see [identifyMode]). */
        const val ARG_IDENTIFY_MODE = "identify_mode"

        /** Fragment-result key the host listens on; the bundle carries the
         *  cropped photo path under [RESULT_PATH]. */
        const val RESULT_IDENTIFY_CAPTURE = "camera_identify_capture"
        const val RESULT_PATH = "path"

        private const val KEY_PENDING_CROP = "pending_crop_file"
        private const val KEY_PENDING_SOURCE = "pending_source_file"
        private const val KEY_PENDING_FROM_CAMERA = "pending_from_camera"
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        // Guard like every other async callback in this file — `binding`
        // is a !! getter that NPEs after onDestroyView.
        if (_binding == null) return@registerForActivityResult
        // Only the panels here: dismissing the system dialog resumes us, and
        // onResume opens the preview. Starting it here as well would bind the
        // use cases twice.
        binding.permissionLayout.visibility = if (granted) View.GONE else View.VISIBLE
        binding.cameraPreview.visibility = if (granted) View.VISIBLE else View.GONE
        (activity as? MainActivity)?.resumeOnboardingAfterExternalUi()
    }

    private val galleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri == null) {
            (activity as? MainActivity)?.resumeOnboardingAfterExternalUi()
        } else {
            (activity as? MainActivity)?.moveOnboardingTo(OnboardingStepId.RESULT_DIAGNOSIS)
            handleGalleryImage(uri)
        }
    }

    /** Crop editor (our own [CropContract] / CropActivity). Both the camera
     *  shutter and the gallery picker funnel their image through here
     *  before it reaches analysis. The cropper writes the result to the
     *  unique [pendingCropFile] we hand it; that exact path goes on to
     *  AnalysisActivity for both the preview and the upload. */
    private val cropLauncher = registerForActivityResult(CropContract()) { result ->
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
                // Device-gallery autosave no longer happens here: it moved to
                // AnalysisActivity, AFTER the verdict, so not-a-plant / blurry
                // rejects never land in the user's photo library. We only pass
                // the camera-vs-import origin along (autosave stays camera-only).
                navigateToAnalysis(cropped.absolutePath)
            }
            // The crop succeeded; the raw original is no longer needed.
            clearPendingSource()
        } else {
            if (result.error != null) Log.w(TAG, "Crop not completed", result.error)
            discardPendingCrop()
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
        // The shared toolbar is hidden on this tab (see MainActivity), so the
        // preview now runs edge-to-edge under the status bar. Pad the root by
        // the status-bar inset so the offline banner / model-family chip
        // don't sit under the clock — the live preview loses that same
        // sliver from the top, same as it always did under the toolbar.
        binding.root.applyStatusBarTopInset()

        // Restore the in-flight crop/capture handoff so a process-death while
        // the cropper or camera is foregrounded doesn't orphan the result.
        savedInstanceState?.let { state ->
            pendingCropFile = state.getString(KEY_PENDING_CROP)?.let { File(it) }
            pendingSourceFile = state.getString(KEY_PENDING_SOURCE)?.let { File(it) }
            pendingFromCamera = state.getBoolean(KEY_PENDING_FROM_CAMERA, false)
        }

        setupUI()
        setupPinchToZoom()
        // NB: the camera is deliberately *not* opened here — see onResume.
        // In identify mode the host (SpeciesIdentifyActivity) shows the shared
        // OfflineNoticeView over this preview, so skip this fragment's own
        // banner there rather than stacking two strips at the top.
        if (!identifyMode) binding.offlineBanner.observe(viewLifecycleOwner, connectivity.isOnline)
        // Tracked in both modes: identify is server-only, so the shutter guard
        // needs the state even though the host owns identify's notice.
        viewLifecycleOwner.onConnectivityChanged(connectivity.isOnline) { online ->
            isOffline = !online
            if (_binding != null && !identifyMode) applyOfflineScanState()
        }
        restoreGridOverlay()
        registerOnboardingAnchors()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        // captureInFlight is deliberately not persisted: a capture cannot
        // survive process-death, so it must reset to false on restore.
        pendingCropFile?.let { outState.putString(KEY_PENDING_CROP, it.absolutePath) }
        pendingSourceFile?.let { outState.putString(KEY_PENDING_SOURCE, it.absolutePath) }
        outState.putBoolean(KEY_PENDING_FROM_CAMERA, pendingFromCamera)
    }

    private fun setupUI() {
        binding.btnCapture.setOnClickListener { onCaptureClicked() }
        binding.btnGallery.setOnClickListener {
            // Same offline rule as the shutter — an imported photo needs the
            // same model the captured one does.
            if (refuseOffline()) return@setOnClickListener
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

    /** Update the picker chip to show the family a capture would actually
     *  run on — which offline is Aqua, not whatever the user last picked. */
    private fun refreshModelFamilyChip() {
        val family = effectiveFamily()
        binding.btnModelFamily.setText(family.labelRes)
        binding.btnModelFamily.setIconResource(family.iconRes)
    }

    /**
     * The family this capture will really use. Offline, the two server tiers
     * cannot run at all, so an installed on-device model takes over — decided
     * here, before the shutter, rather than after a wasted image re-encode and
     * a connect timeout downstream.
     */
    private fun effectiveFamily(): ModelFamily {
        val selected = ModelFamilyPrefs.selected(requireContext())
        return if (isOffline && !selected.onDevice && AquaClassifier.isAvailable(requireContext())) {
            ModelFamily.AQUA
        } else {
            selected
        }
    }

    /**
     * Reflect reachability on the scan screen. Offline the app is limited to
     * the on-device model, so the banner says which of the two situations the
     * user is in — a limited scan still works, or scanning is unavailable —
     * and the picker chip switches to the family that will actually run.
     */
    private fun applyOfflineScanState() {
        binding.offlineBanner.setText(
            if (AquaClassifier.isAvailable(requireContext())) {
                R.string.offline_notice_scan_on_device
            } else {
                R.string.offline_notice_scan_unavailable
            },
        )
        refreshModelFamilyChip()
    }

    /** Refresh per-family availability from the server so the picker can grey
     *  out frontier when it isn't enabled, and download/update the Aqua
     *  on-device model when the server has one. Best-effort: failures keep the
     *  last-known state (frontier stays disabled; the existing Aqua model, if
     *  any, is untouched). */
    private fun syncModelCapabilities() {
        // The Application is still needed here for appScope (an app-lifecycle
        // CoroutineScope, not an injectable singleton).
        val app = requireActivity().application as PlantDiseasesApp
        viewLifecycleOwner.lifecycleScope.launch {
            scanRepository.fetchCapabilities().onSuccess { caps ->
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
                scanRepository.syncAquaModel(requireContext().applicationContext)
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

    /**
     * Open the preview, asking for CAMERA first if we don't have it yet.
     *
     * Driven from onResume rather than onViewCreated because the Scan tab is now
     * a page of the shell's ViewPager2: its view is built — and kept — while the
     * tab sits offscreen so a swipe has a real preview to slide in, and only the
     * page the user actually lands on is promoted to RESUMED. Opening the camera
     * from onViewCreated would now do it while the user is still looking at
     * Home, and asking for the permission there would fire the system dialog
     * over Home. Both are gone once RESUMED is the trigger.
     */
    private fun openCameraOrAskPermission() {
        val granted = ContextCompat.checkSelfPermission(
            requireContext(),
            Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) {
            startCamera()
            return
        }
        binding.permissionLayout.visibility = View.VISIBLE
        binding.cameraPreview.visibility = View.GONE
        // Ask only once the user has actually landed on the Scan page, and only
        // once per fragment — see [permissionAsked].
        if (!permissionAsked) {
            permissionAsked = true
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    /** Unbind every use case and drop the provider. Idempotent. */
    private fun releaseCamera() {
        cameraProvider?.unbindAll()
        cameraProvider = null
        camera = null
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
            // Likewise if the user swiped off the Scan page while the provider
            // was initialising: onPause's release has already run, and the bind
            // below would re-open the sensor behind it with nothing left to
            // close it. The view survives an offscreen page, so the guard above
            // does not cover this.
            if (!viewLifecycleOwner.lifecycle.currentState
                    .isAtLeast(Lifecycle.State.RESUMED)
            ) {
                return@addListener
            }
            // Everything from here — including cameraProviderFuture.get() and
            // the bind — is wrapped: on some devices (notably a chunk of the
            // Samsung fleet) provider init or bindToLifecycle throws, and an
            // uncaught throw here crashed the Scan tab ("error on camera").
            // Fail into the graceful error card instead.
            try {
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
                // Cap the capture to ~3 MP (2048×1536). The whole pipeline
                // downscales to 2048 px on the long edge anyway
                // (ImageUtils.downscaleForCropInPlace), so an uncapped full-res
                // shot on a 50–108 MP Samsung sensor just burned seconds on
                // encode + file write + re-decode for zero quality gain — the
                // "lagging camera" symptom. Closest-lower-then-higher keeps it
                // to a real supported size.
                val resolutionSelector = ResolutionSelector.Builder()
                    .setResolutionStrategy(
                        ResolutionStrategy(
                            Size(2048, 1536),
                            ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER,
                        ),
                    )
                    .build()
                imageCapture = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                    .setResolutionSelector(resolutionSelector)
                    .setTargetRotation(rotation)
                    .build()

                val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

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
                Log.e(TAG, "Camera init/bind failed", e)
                if (_binding == null || !isAdded) return@addListener
                Toast.makeText(requireContext(), getString(R.string.camera_error), Toast.LENGTH_SHORT).show()
            }
        }, ContextCompat.getMainExecutor(requireContext()))
    }

    /** Explicit shutter click. Contextual guidance now owns first-use tips. */
    private fun onCaptureClicked() {
        // Swallow a rapid second tap before it can branch into a duplicate
        // capture / cropper / tips sheet.
        val now = SystemClock.elapsedRealtime()
        if (now - lastCaptureTapAt < captureDebounceMs) return
        lastCaptureTapAt = now
        if (refuseOffline()) return
        continueCaptureFlow()
    }

    /**
     * True when this photo has nowhere to go: offline in identify mode (always
     * server-side) or offline with no on-device model installed. Says so up
     * front rather than letting the user frame, crop and then wait out a
     * connect timeout for an error dialog.
     */
    private fun refuseOffline(): Boolean {
        if (!isOffline) return false
        if (!identifyMode && AquaClassifier.isAvailable(requireContext())) return false
        Toast.makeText(
            requireContext(),
            if (identifyMode) {
                R.string.offline_action_unavailable
            } else {
                R.string.offline_scan_needs_model
            },
            Toast.LENGTH_LONG,
        ).show()
        return true
    }

    /** Second stage of the shutter flow. Community sharing is opt-in and
     *  gated downstream in the uploader (the toggle lives in Profile), so
     *  capture itself proceeds unconditionally. */
    private fun continueCaptureFlow() {
        takePhoto()
    }

    private fun takePhoto() {
        if (captureInFlight) return
        val imageCapture = imageCapture ?: return
        if (!identifyMode) {
            (activity as? MainActivity)?.moveOnboardingTo(OnboardingStepId.RESULT_DIAGNOSIS)
        }

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
                    // Do NOT re-arm the shutter here. Capture continues into an
                    // async downscale and then launchCrop(); re-enabling the
                    // button in this gap let a fast second tap fire a whole
                    // second capture + a second cropper. The shutter stays
                    // locked (captureInFlight = true, button disabled) until the
                    // crop returns — cropLauncher re-arms it on every outcome.
                    if (_binding == null || !isAdded) return
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
        // Hand the cropper a content:// destination via our FileProvider. The
        // provider maps filesDir/plant_images (see res/xml/file_paths.xml), so
        // CropActivity streams the JPEG through contentResolver.openOutputStream
        // into outFile, and we read outFile.absolutePath on success.
        val outputUri = FileProvider.getUriForFile(
            requireContext(),
            "${requireContext().packageName}.fileprovider",
            outFile,
        )
        // Free-form crop, JPEG q90, rule-of-thirds guides — all intrinsic to
        // the custom crop UI now, so the launch only carries source + output.
        cropLauncher.launch(CropContract.Input(source = sourceUri, output = outputUri))
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
        // Only when reachability overrode the user's pick — otherwise let
        // analysis read the preference itself, as it always has.
        val override = effectiveFamily().takeIf { it != ModelFamilyPrefs.selected(requireContext()) }
        val intent = Intent(requireContext(), AnalysisActivity::class.java).apply {
            putExtra(AnalysisActivity.EXTRA_IMAGE_PATH, imagePath)
            // Camera-origin flag: gates the post-verdict device-gallery
            // autosave in AnalysisActivity (gallery imports are never
            // autosaved — the user already has that photo).
            putExtra(AnalysisActivity.EXTRA_FROM_CAMERA, pendingFromCamera)
            // Offline, hand analysis the on-device family explicitly. Without
            // it, a scan on the default (server) family walks the whole server
            // path first — decode, resize, re-encode, connect timeout — before
            // failing over to the very model we already know it will use.
            override?.let { putExtra(AnalysisActivity.EXTRA_FORCE_FAMILY, it.id) }
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
        // RESUMED is now the one signal that means "the camera is on screen":
        // the pager resumes only the page the user settled on, and an activity
        // host (species identify) resumes for as long as it's foreground. So
        // this is where the preview opens — on arrival at the Scan page, on
        // return from the cropper/gallery, and once the permission dialog is
        // dismissed and state is stable.
        if (_binding != null) {
            openCameraOrAskPermission()
            (activity as? MainActivity)?.resumeOnboardingAfterExternalUi()
        }
    }

    override fun onPause() {
        super.onPause()
        // Release the sensor the moment this stops being the page on screen.
        //
        // This used to hang off onDestroyView, which worked only because the
        // bottom nav destroyed the inactive tab on every switch. As a pager page
        // the Scan tab is never destroyed and never even stops — offscreen pages
        // sit at STARTED — so onDestroyView/ON_STOP can't be the release trigger
        // any more, and the sensor (with the OS camera-active indicator) would
        // stay held while the user browsed Home. Pairing the release with
        // onResume's open restores the invariant: bound exactly while RESUMED.
        //
        // This costs nothing on a swipe: ViewPager2 only hands RESUMED to the
        // new page once the scroll goes idle, so the preview stays live under
        // the finger for the whole gesture rather than blanking mid-drag.
        releaseCamera()
    }

    override fun onDestroyView() {
        if (!identifyMode) {
            (activity as? MainActivity)?.apply {
                unregisterOnboardingAnchor(OnboardingAnchorKey.CAMERA_PERMISSION, _binding?.btnGrantPermission)
                unregisterOnboardingAnchor(OnboardingAnchorKey.CAMERA_PREVIEW, _binding?.cameraPreview)
                unregisterOnboardingAnchor(OnboardingAnchorKey.CAMERA_GALLERY, _binding?.btnGallery)
                unregisterOnboardingAnchor(OnboardingAnchorKey.CAMERA_SHUTTER, _binding?.btnCapture)
            }
        }
        super.onDestroyView()
        // Belt and braces — onPause has already released it on every path that
        // gets here. Kept so the "no provider outlives the view" guarantee
        // doesn't depend on lifecycle ordering.
        releaseCamera()
        // Drain any pending zoom-pill hide before the binding goes — the
        // runnable null-checks _binding, but cancelling it avoids a
        // dangling Choreographer callback on the destroyed view.
        binding.tvZoom.removeCallbacks(hideZoomRunnable)
        _binding = null
    }

    private fun registerOnboardingAnchors() {
        if (identifyMode) return
        (activity as? MainActivity)?.apply {
            registerOnboardingAnchor(
                OnboardingAnchorKey.CAMERA_PERMISSION,
                binding.btnGrantPermission,
                available = { binding.btnGrantPermission.isShown },
                pauseAfterTarget = true,
            )
            registerOnboardingAnchor(
                OnboardingAnchorKey.CAMERA_PREVIEW,
                binding.cameraPreview,
                available = { binding.cameraPreview.isShown },
            )
            registerOnboardingAnchor(
                OnboardingAnchorKey.CAMERA_GALLERY,
                binding.btnGallery,
                pauseAfterTarget = true,
            )
            registerOnboardingAnchor(
                OnboardingAnchorKey.CAMERA_SHUTTER,
                binding.btnCapture,
                pauseAfterTarget = true,
            )
        }
    }
}
