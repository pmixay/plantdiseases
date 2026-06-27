package com.plantdiseases.app.ui.home

import android.content.Intent
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.os.Bundle
import android.provider.Settings
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ReplacementSpan
import android.view.LayoutInflater
import android.view.Menu
import android.view.MenuInflater
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import androidx.core.content.ContextCompat
import androidx.core.view.MenuProvider
import androidx.fragment.app.Fragment
import androidx.interpolator.view.animation.FastOutSlowInInterpolator
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.recyclerview.widget.LinearLayoutManager
import com.bumptech.glide.Glide
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.PopularResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.databinding.FragmentHomeBinding
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.ui.camera.WaterCalculatorActivity
import com.plantdiseases.app.ui.community.NearbyScansActivity
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.ui.symptom.SymptomCheckerActivity
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.RegionResolver
import com.plantdiseases.app.util.SpeciesCatalog
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import java.util.Calendar
import java.util.Locale

/**
 * Home — a visual discovery feed (start destination). Two hero actions, a
 * compact tools row, then the carousels that are the centerpiece: trending
 * community species (Wikipedia photos), popular diseases, and the disease
 * guide. A slim Stats & Tips teaser sits at the bottom. Tapping a card opens
 * a dedicated detail screen (SpeciesDetailActivity / GuideDetailActivity).
 */
class HomeFragment : Fragment() {

    private var _binding: FragmentHomeBinding? = null
    private val binding get() = _binding!!

    private val apiClient: PlantApiClient by inject()

    private val isRu: Boolean get() = Locale.getDefault().language == "ru"
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
        binding.tvGreeting.text = greeting()

        setupCommunityMenu()
        binding.cardScan.setOnClickListener { openScanTab() }
        binding.cardIdentify.setOnClickListener {
            startActivity(Intent(requireContext(), com.plantdiseases.app.ui.identify.SpeciesIdentifyActivity::class.java))
        }

        binding.tileTools.setOnClickListener { showToolsSheet() }
        binding.tileSymptom.setOnClickListener {
            startActivity(Intent(requireContext(), SymptomCheckerActivity::class.java))
        }
        binding.tileGuide.setOnClickListener { findNavController().navigate(R.id.guideFragment) }
        binding.tvGuideAll.setOnClickListener { findNavController().navigate(R.id.guideFragment) }

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

        val content = binding.root.getChildAt(0) as? ViewGroup ?: return
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
    }

    override fun onDestroyView() {
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

    /** Group the light meter + water calculator behind a single "Tools" entry
     *  so the home tool row stays uncluttered. The two utilities (logic in
     *  LightMeterActivity / WaterCalculatorActivity, both unit-tested) are
     *  unchanged — only the entry point is consolidated. */
    private fun showToolsSheet() {
        val sheet = com.google.android.material.bottomsheet.BottomSheetDialog(requireContext())
        val view = layoutInflater.inflate(R.layout.sheet_tools, null)
        view.findViewById<View>(R.id.row_light).setOnClickListener {
            startActivity(Intent(requireContext(), LightMeterActivity::class.java))
            sheet.dismiss()
        }
        view.findViewById<View>(R.id.row_water).setOnClickListener {
            startActivity(Intent(requireContext(), WaterCalculatorActivity::class.java))
            sheet.dismiss()
        }
        sheet.setContentView(view)
        sheet.show()
    }

    /** Add the community scans action to the shared toolbar, scoped
     *  to this fragment's RESUMED state so the people icon only shows in the top
     *  bar while the Home tab is open. */
    private fun setupCommunityMenu() {
        requireActivity().addMenuProvider(
            object : MenuProvider {
                override fun onCreateMenu(menu: Menu, menuInflater: MenuInflater) {
                    menuInflater.inflate(R.menu.community_menu, menu)
                    // Tint to match the toolbar title (theme-aware); menu icons
                    // otherwise fall back to a default control colour.
                    menu.findItem(R.id.action_nearby_scans)?.icon
                        ?.setTint(ContextCompat.getColor(requireContext(), R.color.toolbar_title))
                }

                override fun onMenuItemSelected(item: MenuItem): Boolean {
                    if (item.itemId != R.id.action_nearby_scans) return false
                    Haptics.tick(requireContext())
                    startActivity(Intent(requireContext(), NearbyScansActivity::class.java))
                    return true
                }
            },
            viewLifecycleOwner,
            Lifecycle.State.RESUMED,
        )
    }

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
            val cacheKey = "popular:v4:" +
                "${region?.country.orEmpty()}|${region?.lat ?: ""},${region?.lon ?: ""}|$locale"
            val popular = com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, PopularResponse::class.java)
                ?: runCatching {
                    apiClient.popular(
                        country = region?.country,
                        region = region?.locality,
                        lat = region?.lat,
                        lon = region?.lon,
                        locale = locale,
                    )
                }.getOrNull()?.takeIf { it.items.isNotEmpty() }?.also {
                    com.plantdiseases.app.util.ResponseCache.put(
                        cacheKey,
                        it,
                        com.plantdiseases.app.util.ResponseCache.TTL_POPULAR_MS,
                    )
                }
            val b = _binding ?: return@launch
            renderSpecies(b, popular)
            renderDiseases(b, popular)
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

    private fun renderSpecies(b: FragmentHomeBinding, popular: PopularResponse?) {
        b.sectionSpecies.visibility = View.VISIBLE
        setPopularTitle(b, popular)
        b.tvTrendingScope.visibility = View.GONE
        val items = if (popular != null && popular.items.isNotEmpty()) {
            popular.items.take(MAX_POPULAR_CARDS).map { p ->
                // Pre-seed the image cache so loadSpeciesImage paints instantly,
                // no per-species Wikipedia call.
                speciesImageCache[p.scientific] = p.imageUrl
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
        // "Popular worldwide" (no location). The place word always gets the
        // green gradient.
        val country = (if (isRu) popular?.countryNameRu else popular?.countryNameEn)
            ?.takeIf { it.isNotBlank() }
        val region = popular?.region?.takeIf { it.isNotBlank() }
        val accent: String
        val title: String
        when {
            popular?.source == "nearby" && region != null -> {
                accent = region
                title = getString(R.string.home_popular_near, region)
            }
            country != null -> {
                accent = country
                title = getString(R.string.home_popular_in_country, country)
            }
            else -> {
                // Worldwide (community off / no scans anywhere). Accent the
                // word(s) after the leading "Popular" / "Популярно".
                title = getString(R.string.home_popular_worldwide)
                val sp = title.indexOf(' ')
                accent = if (sp in 0 until title.lastIndex) title.substring(sp + 1) else title
            }
        }
        // Gradient-accent the place name (everything matched in the title).
        val s = title.indexOf(accent)
        val start = if (s >= 0) s else 0
        val end = if (s >= 0) s + accent.length else title.length
        val spannable = SpannableString(title)
        if (end > start) {
            val gradient = intArrayOf(
                ContextCompat.getColor(requireContext(), R.color.gradient_start),
                ContextCompat.getColor(requireContext(), R.color.gradient_mid),
                ContextCompat.getColor(requireContext(), R.color.gradient_end),
            )
            spannable.setSpan(
                GradientTextSpan(gradient),
                start,
                end,
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        }
        b.tvSpeciesTitle.text = spannable
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
            Glide.with(target).load(cached).placeholder(R.drawable.ic_plant_placeholder).into(target)
            return
        }
        if (speciesImageCache.containsKey(scientificName)) return // resolved to null (no photo)
        viewLifecycleOwner.lifecycleScope.launch {
            val url = runCatching {
                apiClient.speciesInfo(scientificName, locale)?.imageUrl
            }.getOrNull()
            speciesImageCache[scientificName] = url
            if (url != null) {
                Glide.with(target).load(url)
                    .placeholder(R.drawable.ic_plant_placeholder).into(target)
            }
        }
    }
}

/**
 * Paints its character range with a horizontal multi-stop gradient (used for
 * the green accent word in the Home "Popular in <country>" / "Popular
 * worldwide" title). A [ReplacementSpan] is used rather than a plain shader on
 * the whole TextView so the gradient is aligned to — and clamped within — the
 * exact accented substring, regardless of where it sits on the line.
 */
private class GradientTextSpan(private val colors: IntArray) : ReplacementSpan() {
    override fun getSize(
        paint: Paint,
        text: CharSequence,
        start: Int,
        end: Int,
        fm: Paint.FontMetricsInt?,
    ): Int {
        if (fm != null) {
            val pfm = paint.fontMetricsInt
            fm.ascent = pfm.ascent
            fm.descent = pfm.descent
            fm.top = pfm.top
            fm.bottom = pfm.bottom
        }
        return kotlin.math.ceil(paint.measureText(text, start, end).toDouble()).toInt()
    }

    override fun draw(
        canvas: Canvas,
        text: CharSequence,
        start: Int,
        end: Int,
        x: Float,
        top: Int,
        y: Int,
        bottom: Int,
        paint: Paint,
    ) {
        val width = paint.measureText(text, start, end)
        val previous = paint.shader
        paint.shader = LinearGradient(x, 0f, x + width, 0f, colors, null, Shader.TileMode.CLAMP)
        canvas.drawText(text, start, end, x, y.toFloat(), paint)
        paint.shader = previous
    }
}
