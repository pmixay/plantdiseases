package com.plantdiseases.app.ui.profile

import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.bumptech.glide.Glide
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.billing.EntitlementPolicy
import com.plantdiseases.app.billing.EntitlementRepository
import com.plantdiseases.app.billing.EntitlementState
import com.plantdiseases.app.billing.ProUi
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.FragmentProfileBinding
import com.plantdiseases.app.data.repository.ForumRepository
import com.plantdiseases.app.data.repository.CommunityAvatarSync
import com.plantdiseases.app.databinding.ItemProfileLinkRowBinding
import com.plantdiseases.app.ui.common.AvatarImage
import com.plantdiseases.app.ui.community.forum.HelperProfileActivity
import com.plantdiseases.app.ui.community.forum.ModerationActivity
import com.plantdiseases.app.ui.gallery.MyScansActivity
import com.plantdiseases.app.ui.account.AccountActivity
import com.plantdiseases.app.ui.crop.CropContract
import com.plantdiseases.app.ui.legal.PrivacyPolicyActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.pro.ProActivity
import com.plantdiseases.app.ui.widget.BarChartView
import com.plantdiseases.app.util.AutosavePrefs
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.PrivacyConsentPrefs
import com.plantdiseases.app.util.ProfileAvatarStore
import com.plantdiseases.app.util.ThemeHelper
import com.plantdiseases.app.util.applyStatusBarTopInset
import dagger.hilt.android.AndroidEntryPoint
import java.io.File
import java.util.Calendar
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import javax.inject.Inject

@AndroidEntryPoint
class ProfileFragment : Fragment() {

    @Inject
    lateinit var scanRepository: ScanRepository

    @Inject
    lateinit var notificationRepository: NotificationRepository

    @Inject
    lateinit var privacyConsentPrefs: PrivacyConsentPrefs

    @Inject
    lateinit var profileAvatarStore: ProfileAvatarStore

    @Inject
    lateinit var authRepository: AuthRepository

    @Inject
    lateinit var entitlementRepository: EntitlementRepository

    @Inject
    lateinit var forumRepository: ForumRepository

    @Inject
    lateinit var communityAvatarSync: CommunityAvatarSync

    private var _binding: FragmentProfileBinding? = null
    private val binding get() = _binding!!
    private var isFirstLoad = true
    private var loadedAvatarSessionId: String? = null
    private var activeAvatarAccountId: String? = null
    private var providerAvatarUrl: String? = null
    private var showingLocalAvatar = false
    private var pendingAvatarAccountId: String? = null
    private var pendingAvatarSourceFile: File? = null
    private var pendingAvatarOutputFile: File? = null

    private val avatarPicker = registerForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        if (uri != null) openAvatarEditor(uri)
    }

    private val avatarCropLauncher = registerForActivityResult(CropContract()) { result ->
        completeAvatarEdit(result)
    }

    // Count-up animators, cancelled in onDestroyView so they don't keep
    // ticking against a detached TextView (transient view retention + wasted
    // frames after the view is gone).
    private val countAnimators = mutableListOf<android.animation.ValueAnimator>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingAvatarAccountId = savedInstanceState?.getString(STATE_AVATAR_ACCOUNT_ID)
        pendingAvatarSourceFile = savedInstanceState?.getString(STATE_AVATAR_SOURCE)?.let(::File)
        pendingAvatarOutputFile = savedInstanceState?.getString(STATE_AVATAR_OUTPUT)?.let(::File)
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentProfileBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        binding.root.applyStatusBarTopInset()
        binding.tvAppVersion.text = "v${BuildConfig.VERSION_NAME}"
        binding.proCard.visibility =
            if (ProUi.ENABLED && BuildConfig.ACCOUNT_UI_ENABLED) View.VISIBLE else View.GONE
        binding.proCard.setOnClickListener { startActivity(ProActivity.intent(requireContext())) }
        if (BuildConfig.ACCOUNT_UI_ENABLED) {
            val openAccount = View.OnClickListener {
                startActivity(com.plantdiseases.app.ui.account.AccountActivity.intent(requireContext()))
            }
            binding.btnManageAccount.setOnClickListener(openAccount)
            binding.btnEditProfile.setOnClickListener(openAccount)
            binding.profileAvatarPicker.setOnClickListener {
                if (activeAvatarAccountId != null) {
                    avatarPicker.launch(
                        PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                    )
                }
            }
            viewLifecycleOwner.lifecycleScope.launch {
                viewLifecycleOwner.repeatOnLifecycle(androidx.lifecycle.Lifecycle.State.STARTED) {
                    authRepository.state.collect { state -> renderAccountState(authRepository, state) }
                }
            }
            viewLifecycleOwner.lifecycleScope.launch {
                viewLifecycleOwner.repeatOnLifecycle(androidx.lifecycle.Lifecycle.State.STARTED) {
                    entitlementRepository.state.collect(::renderProState)
                }
            }
        }

        // "Clear all history" now lives on the Gallery tab's top-app-bar
        // basket, next to the scans it actually wipes.

        // Light Meter was removed from this screen — it now lives
        // on the plant detail with per-plant context. The
        // corresponding cardLightMeter view was deleted from the
        // layout XML in the same pass.

        // Profile empty state CTA
        binding.btnStartScan.setOnClickListener {
            // Select the Scan bottom-nav tab (routes through MainActivity) rather
            // than navigating the camera into Profile's own back stack.
            requireActivity().findViewById<BottomNavigationView>(R.id.bottom_navigation)
                ?.selectedItemId = R.id.cameraFragment
        }

        // The tips library moved into the Guide handbook (Home → Guide →
        // App help) — Profile is settings + personal stats only now.

        binding.langEn.setOnClickListener { setLanguage("en") }
        binding.langRu.setOnClickListener { setLanguage("ru") }
        highlightActiveLanguage()

        setupThemeChips()
        setupAutosaveToggle()
        setupPrivacyConsentToggles()
        binding.replayGuidanceRow.setOnClickListener {
            (activity as? MainActivity)?.replayAppGuidance()
        }
        binding.privacyPolicyRow.setOnClickListener {
            startActivity(PrivacyPolicyActivity.intent(requireContext()))
        }
        setupCommunityRows()
        // loadStats() runs in onResume(), which fires immediately after this on
        // first show and again on every return — calling it here too would issue
        // a duplicate getStats() DB read on every fragment creation.
    }

    private fun renderAccountState(auth: AuthRepository, state: AuthState) {
        val authenticated = when (state) {
            is AuthState.Authenticated -> state
            is AuthState.Unavailable -> state.lastAuthenticated
            else -> null
        }
        // Signed in: the nickname with an edit button beside it. Otherwise the
        // hero says where the account stands and offers the way in.
        binding.tvProfileHeroSubtitle.text = when {
            authenticated != null -> getString(R.string.profile_subtitle)
            state is AuthState.Restoring -> getString(R.string.account_restoring)
            state is AuthState.Unavailable -> getString(R.string.account_temporarily_unavailable)
            state is AuthState.ProfileRequired -> getString(R.string.account_finish_profile)
            else -> getString(R.string.profile_subtitle)
        }
        binding.btnEditProfile.visibility = if (authenticated != null) View.VISIBLE else View.GONE
        binding.btnManageAccount.visibility = if (authenticated == null) View.VISIBLE else View.GONE
        binding.btnManageAccount.setText(
            when (state) {
                is AuthState.ProfileRequired -> R.string.account_finish_creation
                is AuthState.Guest -> R.string.account_sign_in_or_create
                else -> R.string.account_manage
            },
        )
        binding.tvProfileHeroTitle.text = authenticated?.profile?.nickname
            ?: getString(R.string.tab_profile)

        val accountId = authenticated?.profile?.communityActorId
        if (activeAvatarAccountId != accountId) {
            activeAvatarAccountId = accountId
            providerAvatarUrl = null
            loadedAvatarSessionId = null
        }
        binding.profileAvatarPicker.isEnabled = accountId != null
        if (accountId != null) {
            showProfileAvatar(accountId, providerAvatarUrl)
        } else {
            showProfileAvatar(null, null)
        }

        if (state is AuthState.Authenticated) {
            if (loadedAvatarSessionId != state.session.sessionId) {
                loadedAvatarSessionId = state.session.sessionId
                loadProfileAvatar(auth, state.session.sessionId)
                restoreProfileAvatar()
            }
        }
    }

    private fun renderProState(state: EntitlementState) {
        binding.proStatus.setText(
            when {
                EntitlementPolicy.isActivePro(state) -> R.string.pro_status_active
                state is EntitlementState.Checking -> R.string.pro_status_checking
                state is EntitlementState.Unavailable -> R.string.pro_status_unavailable
                else -> R.string.pro_profile_body
            },
        )
    }

    private fun loadProfileAvatar(auth: AuthRepository, expectedSessionId: String) {
        viewLifecycleOwner.lifecycleScope.launch {
            auth.me().onSuccess { profile ->
                val current = auth.state.value as? AuthState.Authenticated
                if (current?.session?.sessionId == expectedSessionId && _binding != null) {
                    providerAvatarUrl = profile.primaryProvider.avatarUrl
                    showProfileAvatar(profile.communityActorId, providerAvatarUrl)
                }
            }
        }
    }

    /**
     * The local photo and the "show in Community" choice do not survive a
     * reinstall, but the photo the member shows in Community does: bring it
     * back as the profile photo (see [CommunityAvatarSync.restore]).
     */
    private fun restoreProfileAvatar() {
        viewLifecycleOwner.lifecycleScope.launch {
            if (communityAvatarSync.restore() == CommunityAvatarSync.RestoreOutcome.RESTORED && _binding != null) {
                showProfileAvatar(activeAvatarAccountId, providerAvatarUrl)
                bindCommunityPhotoToggle()
            }
        }
    }

    /** Picks up a local photo that appeared while away (restored on the Account screen). */
    private fun refreshLocalAvatar() {
        val accountId = activeAvatarAccountId ?: return
        if (!showingLocalAvatar && profileAvatarStore.current(accountId) != null) {
            showProfileAvatar(accountId, providerAvatarUrl)
        }
    }

    private fun showProfileAvatar(accountId: String?, avatarUrl: String?) {
        val avatar = _binding?.ivProfileAvatar ?: return
        Glide.with(this).clear(avatar)
        val local = accountId?.let(profileAvatarStore::current)
        showingLocalAvatar = local != null
        val source: Any? = local ?: avatarUrl?.takeIf { it.startsWith("https://") }
        // Never touch the avatar's padding here: this runs before the first
        // measure after a recreate (language switch), which is exactly when
        // ShapeableImageView mis-applies padding changes (see AvatarImage).
        if (source == null) {
            AvatarImage.showPlaceholder(avatar)
        } else {
            AvatarImage.loadPhoto(Glide.with(this), avatar, source)
        }
    }

    private fun openAvatarEditor(uri: Uri) {
        val accountId = currentAvatarAccountId() ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            _binding?.profileAvatarPicker?.isEnabled = false
            var stagedSource: File? = null
            var editorOutput: File? = null
            try {
                val safeSource = profileAvatarStore.stageForEditing(uri).getOrElse {
                    showAvatarError()
                    return@launch
                }
                stagedSource = safeSource
                if (currentAvatarAccountId() != accountId) return@launch
                val context = requireContext()
                val cropOutput = File.createTempFile(
                    "profile_avatar_crop_",
                    ".jpg",
                    context.cacheDir,
                )
                editorOutput = cropOutput
                val sourceUri = FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    safeSource,
                )
                val outputUri = FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    cropOutput,
                )
                pendingAvatarAccountId = accountId
                pendingAvatarSourceFile = safeSource
                pendingAvatarOutputFile = cropOutput
                avatarCropLauncher.launch(
                    CropContract.Input(
                        source = sourceUri,
                        output = outputUri,
                        mode = CropContract.Mode.AVATAR,
                    ),
                )
                stagedSource = null
                editorOutput = null
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                pendingAvatarAccountId = null
                pendingAvatarSourceFile = null
                pendingAvatarOutputFile = null
                showAvatarError()
            } finally {
                stagedSource?.delete()
                editorOutput?.delete()
                _binding?.profileAvatarPicker?.isEnabled = activeAvatarAccountId != null
            }
        }
    }

    private fun completeAvatarEdit(result: CropContract.Result) {
        val accountId = pendingAvatarAccountId
        val sourceFile = pendingAvatarSourceFile
        val outputFile = pendingAvatarOutputFile
        pendingAvatarAccountId = null
        pendingAvatarSourceFile = null
        pendingAvatarOutputFile = null
        sourceFile?.delete()

        val editedUri = result.output ?: outputFile
            ?.takeIf { it.isFile && it.length() > 0L }
            ?.let(Uri::fromFile)
        if (!result.isSuccessful || accountId == null || editedUri == null) {
            deleteAvatarEditorOutput(outputFile, result.output)
            return
        }
        if (currentAvatarAccountId() != accountId) {
            deleteAvatarEditorOutput(outputFile, editedUri)
            return
        }

        viewLifecycleOwner.lifecycleScope.launch {
            _binding?.profileAvatarPicker?.isEnabled = false
            try {
                profileAvatarStore.replace(accountId, editedUri)
                    .onSuccess {
                        if (currentAvatarAccountId() == accountId) {
                            showProfileAvatar(accountId, providerAvatarUrl)
                        }
                    }
                    .onFailure { showAvatarError() }
            } finally {
                deleteAvatarEditorOutput(outputFile, editedUri)
                _binding?.profileAvatarPicker?.isEnabled = activeAvatarAccountId != null
            }
        }
    }

    private fun currentAvatarAccountId(): String? = when (val state = authRepository.state.value) {
        is AuthState.Authenticated -> state.profile.communityActorId
        is AuthState.Unavailable -> state.lastAuthenticated?.profile?.communityActorId
        else -> null
    }

    private fun deleteAvatarEditorOutput(file: File?, uri: Uri?) {
        if (file?.delete() != true && uri != null) {
            context?.let { context -> runCatching { context.contentResolver.delete(uri, null, null) } }
        }
    }

    private fun showAvatarError() {
        context?.let { Toast.makeText(it, R.string.account_avatar_update_failed, Toast.LENGTH_LONG).show() }
    }

    private fun color(res: Int): Int = ContextCompat.getColor(requireContext(), res)

    // ── Inline stats detail (7-day bars, top issues) + tips library ──────
    /** Real-scan count per day for the last 7 days (oldest → today). */
    private fun weeklyBars(scans: List<ScanHistoryItem>): List<BarChartView.Bar> {
        val labels = resources.getStringArray(R.array.weekday_initials)
        val start = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            add(Calendar.DAY_OF_YEAR, -6)
        }
        return (0 until 7).map {
            val dayStart = start.timeInMillis
            val dow = start.get(Calendar.DAY_OF_WEEK) // 1=Sun..7=Sat
            start.add(Calendar.DAY_OF_YEAR, 1)
            val dayEnd = start.timeInMillis
            BarChartView.Bar(labels[dow - 1], scans.count { it.timestamp in dayStart until dayEnd })
        }
    }

    private fun renderTopIssues(scans: List<ScanHistoryItem>) {
        val isRu = LocaleHelper.isRussian(requireContext())
        val list = binding.listTopIssues
        list.removeAllViews()
        val top = scans.filter { !it.isHealthy }
            .groupingBy { if (isRu) it.diseaseNameRu else it.diseaseName }
            .eachCount().entries
            .sortedByDescending { it.value }
            .take(4)
        binding.tvTopIssuesTitle.visibility = if (top.isEmpty()) View.GONE else View.VISIBLE
        if (top.isEmpty()) return
        val max = top.first().value
        top.forEach { (name, n) -> list.addView(issueBar(name, n, max)) }
    }

    /** One horizontal "issue" bar: label over a proportional fill track. */
    private fun issueBar(name: String, value: Int, max: Int): View {
        val ctx = requireContext()
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 6.dp(), 0, 6.dp())
        }
        val header = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(
            TextView(ctx).apply {
                text = name
                textSize = 13f
                setTextColor(color(R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            },
        )
        header.addView(
            TextView(ctx).apply {
                text = value.toString()
                textSize = 13f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(color(R.color.on_surface))
            },
        )
        column.addView(header)

        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(0x14000000)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                8.dp(),
            ).apply { topMargin = 4.dp() }
        }
        val fillWeight = value.toFloat() / max.coerceAtLeast(1)
        track.addView(
            View(ctx).apply {
                setBackgroundColor(color(R.color.disease_red))
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, fillWeight)
            },
        )
        track.addView(
            View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(
                    0,
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    (1f - fillWeight).coerceAtLeast(0f),
                )
            },
        )
        column.addView(track)
        return column
    }

    private fun Int.dp(): Int =
        (this * resources.displayMetrics.density).toInt()

    /** Highlight the active flag segment (filled green + white text); the
     *  other stays flat with secondary text. */
    private fun highlightActiveLanguage() {
        val isRu = LocaleHelper.isRussian(requireContext())
        styleLanguageSegment(binding.langEn, binding.tvLangEn, active = !isRu)
        styleLanguageSegment(binding.langRu, binding.tvLangRu, active = isRu)
    }

    private fun styleLanguageSegment(pill: android.view.View, label: android.widget.TextView, active: Boolean) {
        pill.setBackgroundResource(if (active) R.drawable.bg_segment_active else 0)
        label.setTextColor(
            androidx.core.content.ContextCompat.getColor(
                requireContext(),
                if (active) R.color.on_primary else R.color.on_surface,
            ),
        )
    }

    private fun setLanguage(lang: String) {
        if (LocaleHelper.getSavedLanguage(requireContext()) == lang) return
        // Locale is owned entirely by LocaleHelper: every activity's
        // attachBaseContext re-reads the saved language and wraps its context,
        // so persisting the new value (setLocale saves it) and recreating the
        // host is all that's needed. We must NOT also call
        // AppCompatDelegate.setApplicationLocales() — a second, framework-level
        // locale change raced our attachBaseContext wrapping and left the
        // window on a black frame (the "app turns black" bug). One mechanism,
        // one recreate.
        LocaleHelper.setLocale(requireContext(), lang)
        // Notification-centre rows persist already-rendered title/body text
        // (NotificationRepository.record takes strings, not resource ids), so
        // they would keep the previous language for the whole 30-day retention
        // while every other surface flips. The rows are transient and already
        // disposable, so drop them rather than ship a half-translated list.
        // Runs on the app scope because recreate() below cancels this
        // fragment's lifecycleScope.
        val app = requireContext().applicationContext as PlantDiseasesApp
        app.appScope.launch { notificationRepository.clearAll() }
        requireActivity().recreate()
    }

    /** Autosave-to-album toggle — scoped storage needs no permission, so the
     *  preference commits directly. */
    private fun setupAutosaveToggle() {
        val sw = binding.switchAutosave
        sw.isChecked = AutosavePrefs.enabled(requireContext())
        sw.setOnCheckedChangeListener { _, isChecked ->
            AutosavePrefs.setEnabled(requireContext(), isChecked)
        }
    }

    private fun setupPrivacyConsentToggles() {
        binding.switchTelemetryConsent.isChecked = privacyConsentPrefs.telemetryEnabled
        binding.switchTelemetryConsent.setOnCheckedChangeListener { _, enabled ->
            privacyConsentPrefs.setTelemetryEnabled(enabled)
        }
    }

    private fun setupThemeChips() {
        // Set initial selection
        val currentTheme = ThemeHelper.getSavedTheme(requireContext())
        when (currentTheme) {
            ThemeHelper.THEME_LIGHT -> binding.chipThemeLight.isChecked = true
            ThemeHelper.THEME_DARK -> binding.chipThemeDark.isChecked = true
            else -> binding.chipThemeSystem.isChecked = true
        }

        binding.chipThemeLight.setOnClickListener { applyTheme(ThemeHelper.THEME_LIGHT) }
        binding.chipThemeDark.setOnClickListener { applyTheme(ThemeHelper.THEME_DARK) }
        binding.chipThemeSystem.setOnClickListener { applyTheme(ThemeHelper.THEME_SYSTEM) }
    }

    private fun applyTheme(mode: Int) {
        if (ThemeHelper.getSavedTheme(requireContext()) == mode) return
        // ``ThemeHelper.saveTheme`` persists the choice and then calls
        // ``AppCompatDelegate.setDefaultNightMode``. With ``uiMode``
        // removed from MainActivity's configChanges in the manifest,
        // AppCompat now triggers the recreate itself — we don't need
        // (and must not add) a manual recreate or a fade animation.
        // The earlier ``decorView.animate { recreate() }`` hack used
        // to freeze the app: the alpha-0 fade plus a second recreate
        // racing against AppCompat's own dispatch landed the window
        // on a black-screen / unresponsive state on some devices.
        ThemeHelper.saveTheme(requireContext(), mode)
    }

    private fun loadStats() {
        val isRu = LocaleHelper.isRussian(requireContext())

        // Show shimmer on first load
        if (isFirstLoad) {
            binding.shimmerStats.visibility = View.VISIBLE
            binding.shimmerStats.startShimmer()
            binding.statsLayout.visibility = View.GONE
        }

        // viewLifecycleOwner scope: cancels on onDestroyView so the
        // continuation never resumes against a null binding (NPE on tab
        // switch / config change mid-query).
        viewLifecycleOwner.lifecycleScope.launch {
            val stats = scanRepository.getStats()
            // Full list (the bar chart + top-issues need per-scan data the
            // aggregate ScanStats doesn't carry).
            val real = runCatching { scanRepository.getAllScans() }
                .getOrDefault(emptyList())
                .filter { !it.isNotAPlant }

            // Hide shimmer
            if (isFirstLoad) {
                binding.shimmerStats.stopShimmer()
                binding.shimmerStats.visibility = View.GONE
                isFirstLoad = false
            }

            // `totalScans` counts plant scans only (healthy + real disease).
            // Natural NAP and inconclusive rows are excluded from these plant
            // counts even while their short-lived correction rows persist.
            if (stats.totalScans == 0) {
                binding.statsLayout.visibility = View.GONE
                binding.emptyState.visibility = View.VISIBLE
                registerOnboardingAnchors(binding.emptyState)
                return@launch
            }

            binding.statsLayout.visibility = View.VISIBLE
            binding.emptyState.visibility = View.GONE

            // 7-day activity bar chart + top recurring issues.
            binding.barWeek.setBars(
                weeklyBars(real),
                barColor = color(R.color.primary),
                labelColor = color(R.color.on_surface),
                valueColor = color(R.color.on_surface),
            )
            renderTopIssues(real)

            // The "Total Scans" tile matches the gallery row count (plant
            // scans only — rejects are never saved).
            animateCount(binding.tvTotalScans, stats.totalScans)
            animateCount(binding.tvHealthyCount, stats.healthyCount)
            animateCount(binding.tvDiseasedCount, stats.diseasedCount)

            // Health rate is over plant scans only — NAP rows don't fail a
            // plant health check, they're a UX retake prompt. The ring and the
            // centre % animate up together.
            val ratePct = if (stats.totalScans > 0) {
                stats.healthyCount * 100 / stats.totalScans
            } else {
                0
            }
            binding.tvHealthRate.text = if (stats.totalScans > 0) "$ratePct%" else "-"
            binding.progressHealthRing.max = 100
            binding.progressHealthRing.setProgressCompat(ratePct, true)

            val commonDisease = if (isRu) stats.mostCommonDiseaseRu else stats.mostCommonDisease
            binding.tvCommonDisease.text = commonDisease ?: getString(R.string.none_yet)
            registerOnboardingAnchors(binding.statsLayout)
        }
    }

    private fun registerOnboardingAnchors(statisticsAnchor: View) {
        (activity as? MainActivity)?.apply {
            registerOnboardingAnchor(OnboardingAnchorKey.PROFILE_SETTINGS, binding.settingsCard)
            registerOnboardingAnchor(OnboardingAnchorKey.PROFILE_STATISTICS, statisticsAnchor)
            registerOnboardingAnchor(
                OnboardingAnchorKey.PROFILE_REPLAY,
                binding.replayGuidanceRow,
                pauseAfterTarget = true,
            )
        }
    }

    /** Count-up animation for a stat number (0 → target over ~0.6s). */
    private fun animateCount(tv: android.widget.TextView, target: Int) {
        if (target <= 0) {
            tv.text = "0"
            return
        }
        android.animation.ValueAnimator.ofInt(0, target).apply {
            duration = 600L
            addUpdateListener { tv.text = (it.animatedValue as Int).toString() }
            countAnimators += this
            start()
        }
    }

    override fun onResume() {
        super.onResume()
        loadStats()
        refreshModerationRow()
        refreshLocalAvatar()
        restoreProfileAvatar()
        bindCommunityPhotoToggle()
    }

    /** "Show my photo in Community": off keeps the photo on the device and withdraws it. */
    private fun bindCommunityPhotoToggle() {
        val signedIn = BuildConfig.ACCOUNT_UI_ENABLED && communityAvatarSync.accountId() != null
        binding.communityPhotoRow.visibility = if (signedIn) View.VISIBLE else View.GONE
        val toggle = binding.switchCommunityPhoto
        toggle.setOnCheckedChangeListener(null)
        toggle.isChecked = signedIn && communityAvatarSync.isShown()
        toggle.setOnCheckedChangeListener { _, show ->
            viewLifecycleOwner.lifecycleScope.launch {
                val message = when (communityAvatarSync.setShown(show)) {
                    CommunityAvatarSync.Outcome.REJECTED -> R.string.community_photo_rejected
                    CommunityAvatarSync.Outcome.FAILED -> R.string.community_photo_failed
                    else -> null
                }
                if (message != null && _binding != null) {
                    Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    /** My scans (the former Gallery tab), achievements and — for staff
     *  only — the moderation tools. Moderation decisions about the user's own
     *  posts arrive in Community's notifications instead. */
    private fun setupCommunityRows() {
        bindRow(binding.rowMyScans, R.drawable.ic_gallery, R.string.profile_my_scans, R.string.profile_my_scans_subtitle) {
            startActivity(MyScansActivity.intent(requireContext()))
        }
        bindRow(
            binding.rowAchievements,
            R.drawable.ic_trophy,
            R.string.forum_my_achievements,
            R.string.profile_achievements_subtitle,
        ) {
            if (requireCommunitySignIn()) startActivity(HelperProfileActivity.intentForMe(requireContext()))
        }
        bindRow(
            binding.rowModeration,
            R.drawable.ic_shield_check,
            R.string.forum_moderation_tools,
            R.string.profile_moderation_subtitle,
        ) {
            startActivity(ModerationActivity.intent(requireContext()))
        }
    }

    private fun bindRow(
        row: ItemProfileLinkRowBinding,
        icon: Int,
        title: Int,
        subtitle: Int,
        onClick: () -> Unit,
    ) {
        row.rowIcon.setImageResource(icon)
        row.rowTitle.setText(title)
        row.rowSubtitle.setText(subtitle)
        row.root.setOnClickListener { onClick() }
    }

    private fun refreshModerationRow() {
        if (!forumRepository.isSignedIn) {
            binding.rowModeration.root.visibility = View.GONE
            return
        }
        viewLifecycleOwner.lifecycleScope.launch {
            forumRepository.staffMe().onSuccess { me ->
                _binding?.rowModeration?.root?.visibility = if (me.isStaff) View.VISIBLE else View.GONE
            }
        }
    }

    private fun requireCommunitySignIn(): Boolean {
        if (forumRepository.isSignedIn) return true
        Toast.makeText(requireContext(), R.string.community_sign_in_required, Toast.LENGTH_SHORT).show()
        startActivity(AccountActivity.intent(requireContext()))
        return false
    }

    override fun onSaveInstanceState(outState: Bundle) {
        pendingAvatarAccountId?.let { outState.putString(STATE_AVATAR_ACCOUNT_ID, it) }
        pendingAvatarSourceFile?.let { outState.putString(STATE_AVATAR_SOURCE, it.absolutePath) }
        pendingAvatarOutputFile?.let { outState.putString(STATE_AVATAR_OUTPUT, it.absolutePath) }
        super.onSaveInstanceState(outState)
    }

    override fun onDestroyView() {
        (activity as? MainActivity)?.apply {
            unregisterOnboardingAnchor(OnboardingAnchorKey.PROFILE_SETTINGS)
            unregisterOnboardingAnchor(OnboardingAnchorKey.PROFILE_STATISTICS)
            unregisterOnboardingAnchor(OnboardingAnchorKey.PROFILE_REPLAY)
        }
        Glide.with(this).clear(binding.ivProfileAvatar)
        loadedAvatarSessionId = null
        activeAvatarAccountId = null
        providerAvatarUrl = null
        super.onDestroyView()
        countAnimators.forEach { it.cancel() }
        countAnimators.clear()
        _binding = null
    }

    companion object {
        private const val STATE_AVATAR_ACCOUNT_ID = "profile.avatar.account_id"
        private const val STATE_AVATAR_SOURCE = "profile.avatar.source"
        private const val STATE_AVATAR_OUTPUT = "profile.avatar.output"
    }
}
