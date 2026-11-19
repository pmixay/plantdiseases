package com.plantdiseases.app.ui.home

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.fragment.app.Fragment
import androidx.interpolator.view.animation.FastOutSlowInInterpolator
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.PopularResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.ForumNotificationSync
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.databinding.FragmentHomeBinding
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.ui.camera.WaterCalculatorActivity
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.notifications.NotificationsActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.ui.symptom.SymptomCheckerActivity
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.SpeciesCatalog
import com.plantdiseases.app.util.applyStatusBarTopInset
import com.plantdiseases.app.util.enableWhileOnline
import com.plantdiseases.app.util.onConnectivityChanged
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import java.util.Calendar
import javax.inject.Inject

/**
 * Home — a visual discovery feed (start destination). Two hero actions, a
 * compact tools row, then the carousels that are the centerpiece: trending
 * community species (Wikipedia photos), popular diseases, and the disease
 * guide. A slim Stats & Tips teaser sits at the bottom. Tapping a card opens
 * a dedicated detail screen (SpeciesDetailActivity / GuideDetailActivity).
 */
@AndroidEntryPoint
class HomeFragment : Fragment() {

    private var _binding: FragmentHomeBinding? = null
    private val binding get() = _binding!!

    @Inject
    lateinit var apiClient: PlantApiClient

    @Inject
    lateinit var connectivity: ConnectivityObserver

    @Inject
    lateinit var notificationRepository: NotificationRepository

    @Inject
    lateinit var forumSync: ForumNotificationSync

    // Use the app's persisted UI language (LocaleHelper), NOT the device
    // locale: a Russian phone with the app switched to English must send
    // locale=en AND read the commonEn field, or the popular cards fall back
    // to the bare scientific name.
    private val isRu: Boolean get() = LocaleHelper.isRussian(requireContext())
    private val locale: String get() = if (isRu) "ru" else "en"

    private val speciesAdapter by lazy {
        SpeciesCarouselAdapter(
            onClick = { SpeciesDetailActivity.start(requireContext(), it.scientific, it.title) },
            loadImage = ::loadSpeciesImage,
        )
    }
    private val popularAdapter by lazy {
        DiseaseCarouselAdapter { GuideDetailActivity.start(requireContext(), it.guideId) }
    }
    private val guideAdapter by lazy {
        DiseaseCarouselAdapter { GuideDetailActivity.start(requireContext(), it.guideId) }
    }

    // Memoised Wikipedia image URLs so re-binding a species card (or a
    // refresh) doesn't re-hit /api/species-info for a name already resolved.
    private val speciesImageCache = mutableMapOf<String, String?>()

    /** Latest reachability reading, and whether the carousel currently holds
     *  live data. Together they decide the "saved earlier" caption: it is only
     *  honest when we are offline AND what's on screen came from the cache or
     *  the bundled list rather than this session's fetch. */
    private var isOffline = false
    private var showingLiveTrending = false

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentHomeBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.root.applyStatusBarTopInset()
        // Home keeps working offline — the carousels fall back to the cached
        // popular bundle and then to the bundled catalogs — so there is no
        // offline strip here. Instead the carousel says its data is saved
        // rather than live, and the entry points into features that genuinely
        // cannot run offline are disabled.
        viewLifecycleOwner.onConnectivityChanged(connectivity.isOnline) { online ->
            isOffline = !online
            _binding?.let { applyTrendingScopeNote(it) }
        }
        // Species ID (PlantNet) and symptom triage (scored server-side) have no
        // offline mode at all.
        viewLifecycleOwner.enableWhileOnline(
            connectivity.isOnline,
            binding.cardIdentify,
            binding.tileSymptom,
        )
        binding.tvGreeting.text = greeting()

        binding.btnNotifications.setOnClickListener {
            Haptics.tick(requireContext())
            startActivity(Intent(requireContext(), NotificationsActivity::class.java))
        }
        binding.cardScan.setOnClickListener { openScanTab() }
        binding.cardIdentify.setOnClickListener {
            startActivity(Intent(requireContext(), com.plantdiseases.app.ui.identify.SpeciesIdentifyActivity::class.java))
        }

        binding.tileLight.setOnClickListener {
            startActivity(Intent(requireContext(), LightMeterActivity::class.java))
        }
        binding.tileWater.setOnClickListener {
            startActivity(Intent(requireContext(), WaterCalculatorActivity::class.java))
        }
        binding.tileSymptom.setOnClickListener {
            startActivity(Intent(requireContext(), SymptomCheckerActivity::class.java))
        }
        binding.tileGuide.setOnClickListener { openGuide() }
        binding.tvGuideAll.setOnClickListener { openGuide() }

        binding.rvSpecies.layoutManager =
            LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false)
        binding.rvSpecies.adapter = speciesAdapter
        binding.rvPopular.layoutManager =
            LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false)
        binding.rvPopular.adapter = popularAdapter
        binding.rvGuide.layoutManager =
            LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false)
        binding.rvGuide.adapter = guideAdapter

        bindDiseaseGuide()
        // Show the bundled popular-species fallback right away so the carousel
        // is present on first frame; loadPopular() (onResume) upgrades it to
        // real scan-based data when the scope has any.
        renderSpecies(binding, null)
        maybePlayIntro(savedInstanceState)
        (activity as? MainActivity)?.apply {
            registerOnboardingAnchor(
                OnboardingAnchorKey.HOME_SCAN_IDENTIFY,
                binding.homePrimaryActions,
            )
            registerOnboardingAnchor(
                OnboardingAnchorKey.HOME_QUICK_TOOLS,
                binding.homeQuickTools,
            )
            registerOnboardingAnchor(
                OnboardingAnchorKey.HOME_NOTIFICATIONS,
                binding.btnNotifications,
            )
        }
    }

    /** Push the guide above the tab shell through the graph action that owns
     *  its forward/back motion. Both Guide entry points deliberately share
     *  this path so their animation and back-stack behaviour cannot drift. */
    private fun openGuide() {
        val controller = findNavController()
        if (controller.currentDestination?.id == R.id.shellFragment) {
            controller.navigate(R.id.action_shellFragment_to_guideFragment)
        }
    }

    /**
     * Staggered entrance for the Home feed, played once per cold start (the
     * static [introPlayed] flag lives for the process lifetime, so it fires on
     * first install and every relaunch, but NOT on tab switches or config-change
     * recreations within a session). Each visible top-level block rises a little
     * and fades in, cascading top→bottom.
     */
    private fun maybePlayIntro(savedInstanceState: Bundle?) {
        if (introPlayed || savedInstanceState != null) return
        introPlayed = true
        // Honour the system "remove animations" / reduced-motion setting.
        val animScale = Settings.Global.getFloat(
            requireContext().contentResolver,
            Settings.Global.ANIMATOR_DURATION_SCALE,
            1f,
        )
        if (animScale == 0f) return

        // The blocks to stagger are the scroll view's content column, not the
        // root's children. (Resolving this against the root silently disabled
        // the whole intro while the offline strip was the root's first child:
        // a TextView is not a ViewGroup, so the cast returned null every time.)
        val content = binding.homeScroll.getChildAt(0) as? ViewGroup ?: return
        val dy = 24f * resources.displayMetrics.density
        var shown = 0
        for (i in 0 until content.childCount) {
            val child = content.getChildAt(i)
            if (child.visibility != View.VISIBLE) continue // skip gone carousels
            child.translationY = dy
            child.alpha = 0f
            child.animate()
                .translationY(0f)
                .alpha(1f)
                .setStartDelay(shown * 70L)
                .setDuration(400L)
                .setInterpolator(FastOutSlowInInterpolator())
                .start()
            shown++
        }
    }

    override fun onResume() {
        super.onResume()
        loadPopular()
        refreshNotificationBadge()
        viewLifecycleOwner.lifecycleScope.launch {
            forumSync.sync()
            refreshNotificationBadge()
        }
    }

    /** No live observer (this app has no Flow/LiveData plumbing over Room —
     *  every screen re-reads on resume) so the badge is only as fresh as the
     *  last time Home came to the foreground; good enough for a count. */
    private fun refreshNotificationBadge() {
        viewLifecycleOwner.lifecycleScope.launch {
            val count = notificationRepository.getUnreadCount()
            (activity as? MainActivity)?.refreshHomeBadge()
            val badge = _binding?.tvNotificationsBadge ?: return@launch
            if (count <= 0) {
                badge.visibility = View.GONE
            } else {
                badge.visibility = View.VISIBLE
                badge.text = if (count > 99) "99+" else count.toString()
            }
        }
    }

    override fun onDestroyView() {
        (activity as? MainActivity)?.apply {
            unregisterOnboardingAnchor(
                OnboardingAnchorKey.HOME_SCAN_IDENTIFY,
                _binding?.homePrimaryActions,
            )
            unregisterOnboardingAnchor(
                OnboardingAnchorKey.HOME_QUICK_TOOLS,
                _binding?.homeQuickTools,
            )
            unregisterOnboardingAnchor(
                OnboardingAnchorKey.HOME_NOTIFICATIONS,
                _binding?.btnNotifications,
            )
        }
        super.onDestroyView()
        _binding = null
    }

    private companion object {
        /** Process-scoped: false at every cold start, set true after the intro
         *  plays. Static lifetime means it survives fragment view recreation
         *  (tab switches, config changes) so the animation fires once per
         *  launch, then resets naturally when the process is recreated. */
        var introPlayed = false

        /** Max cards per popular carousel (matches the server-side cap). */
        const val MAX_POPULAR_CARDS = 10
    }

    /** Switch the bottom bar to the Scan tab (same transition as a tap). */
    private fun openScanTab() {
        requireActivity()
            .findViewById<BottomNavigationView>(R.id.bottom_navigation)
            ?.selectedItemId = R.id.cameraFragment
    }

    /** Add the community scans action to the shared toolbar, scoped
     *  to this fragment's RESUMED state so the people icon only shows in the top
     *  bar while the Home tab is open. */
    private fun greeting(): String {
        val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        return getString(
            when (hour) {
                in 5..11 -> R.string.home_greeting_morning
                in 12..17 -> R.string.home_greeting_day
                in 18..22 -> R.string.home_greeting_evening
                else -> R.string.home_greeting_night
            },
        )
    }

    // ── Disease guide carousel (local, always available) ────────────
    private fun bindDiseaseGuide() {
        val items = DiseaseCatalog.diseaseGuideItems().map { gi ->
            DiseaseCarouselAdapter.Item(
                guideId = gi.id,
                iconRes = gi.iconRes,
                title = if (isRu) gi.titleRu else gi.titleEn,
                subtitle = getString(R.string.home_guide_tag),
            )
        }
        guideAdapter.submit(items)
    }

    // ── Popular carousels (plants + diseases) ───────────────────────
    // Both carousels are driven by /api/popular, which runs one ladder
    // (nearby → country → worldwide → curated placeholder) and returns
    // per-item scan shares. Location is sent only when the user opted into
    // community; otherwise the server returns the worldwide bundle.
    private fun loadPopular() {
        val appContext = requireContext().applicationContext
        viewLifecycleOwner.lifecycleScope.launch {
            val region = if (CommunityPrefs.uploadsEnabled(appContext)) {
                runCatching { RegionResolver.resolveRegion(appContext) }.getOrNull()
            } else {
                null
            }
            val cacheKey = "popular:v5:" +
                "${region?.country.orEmpty()}|${region?.lat ?: ""},${region?.lon ?: ""}|$locale"
            // Stale-while-revalidate: paint the last cached bundle immediately
            // (even if its 2-min TTL lapsed) so the main screen never blanks on
            // a slow connection, then refresh in the background only when stale.
            val cached = com.plantdiseases.app.util.ResponseCache
                .peek(cacheKey, PopularResponse::class.java)
            cached?.let {
                val b = _binding ?: return@launch
                renderSpecies(b, it.value)
                renderDiseases(b, it.value)
            }
            if (cached == null || !cached.fresh) {
                val fresh = runCatching {
                    apiClient.popular(
                        country = region?.country,
                        region = region?.locality,
                        lat = region?.lat,
                        lon = region?.lon,
                        locale = locale,
                    )
                }.getOrNull()?.takeIf { it.items.isNotEmpty() }
                if (fresh != null) {
                    com.plantdiseases.app.util.ResponseCache.put(
                        cacheKey,
                        fresh,
                        com.plantdiseases.app.util.ResponseCache.TTL_POPULAR_MS,
                    )
                    val b = _binding ?: return@launch
                    renderSpecies(b, fresh, live = true)
                    renderDiseases(b, fresh)
                } else if (cached == null) {
                    // No cache and no network → fall back to the bundled list.
                    val b = _binding ?: return@launch
                    renderSpecies(b, null)
                    renderDiseases(b, null)
                }
            }
        }
    }

    /** "P% of scans" for a real (non-placeholder) scope with a ≥1% share;
     *  blank otherwise so the carousel hides the subtitle (placeholder data
     *  or a sub-1% sliver). */
    private fun percentSubtitle(source: String?, percent: Int): String =
        if (source != null && source != "placeholder" && percent >= 1) {
            getString(R.string.home_popular_percent, percent)
        } else {
            ""
        }

    /**
     * "Saved earlier — may be out of date" under the trending carousel.
     *
     * The carousel never blanks offline (cache → bundled list), which is the
     * right behaviour but leaves the user reading month-old "trending nearby"
     * data with no way to tell. This is the one honest signal Home needs, and
     * it sits on the content it describes rather than claiming the whole
     * screen is degraded.
     */
    private fun applyTrendingScopeNote(b: FragmentHomeBinding) {
        val stale = isOffline && !showingLiveTrending
        b.tvTrendingScope.visibility = if (stale) View.VISIBLE else View.GONE
        if (stale) b.tvTrendingScope.setText(R.string.offline_saved_data_note)
    }

    private fun renderSpecies(
        b: FragmentHomeBinding,
        popular: PopularResponse?,
        live: Boolean = false,
    ) {
        b.sectionSpecies.visibility = View.VISIBLE
        setPopularTitle(b, popular)
        showingLiveTrending = live
        applyTrendingScopeNote(b)
        val items = if (popular != null && popular.items.isNotEmpty()) {
            popular.items.take(MAX_POPULAR_CARDS).map { p ->
                // Pre-seed the image cache so loadSpeciesImage paints instantly,
                // no per-species Wikipedia call. Only a real URL is seeded: a
                // null one is absence of data, not a resolved "this species has
                // no photo", and seeding it would trip loadSpeciesImage's
                // containsKey short-circuit and pin the card to its placeholder
                // for the rest of the session.
                p.imageUrl?.let { speciesImageCache[p.scientific] = it }
                SpeciesCarouselAdapter.Item(
                    scientific = p.scientific,
                    title = if (isRu) p.commonRu else p.commonEn,
                    subtitle = percentSubtitle(popular.source, p.percent),
                )
            }
        } else {
            // Last-resort bundled list so the row is never empty (server down).
            SpeciesCatalog.popular.take(MAX_POPULAR_CARDS).map { sp ->
                SpeciesCarouselAdapter.Item(
                    scientific = sp.scientific,
                    title = if (isRu) sp.commonRu else sp.commonEn,
                    subtitle = "",
                )
            }
        }
        speciesAdapter.submit(items)
    }

    private fun setPopularTitle(b: FragmentHomeBinding, popular: PopularResponse?) {
        // "Popular near <city>" (nearby) / "Popular in <country>" (country) /
        // "Popular worldwide" (no location), in the title's own colour.
        val country = (if (isRu) popular?.countryNameRu else popular?.countryNameEn)
            ?.takeIf { it.isNotBlank() }
        val region = popular?.region?.takeIf { it.isNotBlank() }
        b.tvSpeciesTitle.text = when {
            popular?.source == "nearby" && region != null -> getString(R.string.home_popular_near, region)
            country != null -> getString(R.string.home_popular_in_country, country)
            else -> getString(R.string.home_popular_worldwide)
        }
    }

    private fun renderDiseases(b: FragmentHomeBinding, popular: PopularResponse?) {
        // Same ladder as the species carousel; diseases have no curated
        // placeholder, so the section simply hides when there's no real data.
        // Only diseases we have a bundled doc for — guarantees the card has an
        // icon + localized name and the tap opens a real detail screen.
        val items = popular?.topDiseases.orEmpty().take(MAX_POPULAR_CARDS).mapNotNull { d ->
            val gi = DiseaseCatalog.guideItemForClass(d.disease) ?: return@mapNotNull null
            DiseaseCarouselAdapter.Item(
                guideId = gi.id,
                iconRes = gi.iconRes,
                title = DiseaseLabels.displayName(d.disease, isRu)
                    ?: (if (isRu) gi.titleRu else gi.titleEn),
                subtitle = percentSubtitle(popular?.source, d.percent),
            )
        }
        if (items.isEmpty()) {
            b.sectionPopular.visibility = View.GONE
            return
        }
        b.sectionPopular.visibility = View.VISIBLE
        popularAdapter.submit(items)
    }

    /** Resolve + cache a Wikipedia lead photo for a species, then load it
     *  into the card's ImageView. No-ops if the view was recycled away. */
    private fun loadSpeciesImage(scientificName: String, target: ImageView) {
        val cached = speciesImageCache[scientificName]
        if (cached != null) {
            Glide.with(target).load(com.plantdiseases.app.util.ServerConfig.photoUrl(cached))
                .placeholder(R.drawable.ic_plant_placeholder)
                // Disk-cache the bytes (like every other call site) so a slow
                // connection only ever downloads each carousel photo once.
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                // Fall back to the general plant icon when the photo can't load
                // (e.g. a species like Aloe whose lead image fails to resolve),
                // so the card never ends up blank.
                .error(R.drawable.ic_plant_placeholder)
                .into(target)
            return
        }
        if (speciesImageCache.containsKey(scientificName)) return // resolved to null (no photo)
        viewLifecycleOwner.lifecycleScope.launch {
            val url = runCatching {
                apiClient.speciesInfo(scientificName, locale)?.imageUrl
            }.getOrNull()
            speciesImageCache[scientificName] = url
            if (url != null) {
                Glide.with(target).load(com.plantdiseases.app.util.ServerConfig.photoUrl(url))
                    .placeholder(R.drawable.ic_plant_placeholder)
                    .diskCacheStrategy(DiskCacheStrategy.ALL)
                    .error(R.drawable.ic_plant_placeholder)
                    .into(target)
            }
        }
    }
}
