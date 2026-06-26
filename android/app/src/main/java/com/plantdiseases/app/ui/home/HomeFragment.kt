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
import com.plantdiseases.app.data.model.TrendingResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.databinding.FragmentHomeBinding
import com.plantdiseases.app.ui.camera.LightMeterActivity
import com.plantdiseases.app.ui.camera.WaterCalculatorActivity
import com.plantdiseases.app.ui.community.NearbyScansActivity
import com.plantdiseases.app.ui.guide.GuideDetailActivity
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.ui.symptom.SymptomCheckerActivity
import com.plantdiseases.app.util.DiseaseCatalog
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.Haptics
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
        // is present on first frame; loadTrending() (onResume) upgrades it to
        // real trending data when the feed has any.
        renderSpecies(binding, null, null)
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
        loadTrending()
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

    // ── Trending carousels ──────────────────────────────────────────
    private fun loadTrending() {
        viewLifecycleOwner.lifecycleScope.launch {
            // Trending is a "moving" feed — cache it 24h (ResponseCache policy) so
            // every Home onResume doesn't re-hit the network for the same region.
            val cacheKey = "trending:v2:server"
            val trending = com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, TrendingResponse::class.java)
                ?: runCatching {
                    apiClient.communityTrending()
                }.getOrNull()?.also {
                    com.plantdiseases.app.util.ResponseCache.put(
                        cacheKey,
                        it,
                        com.plantdiseases.app.util.ResponseCache.TTL_TRENDING_MS,
                    )
                }
            // When trending has no community species, fall back to the personalized
            // popular bundle (images already resolved server-side). Fetched here
            // so renderSpecies stays synchronous.
            val popular = if (trending == null || trending.topSpecies.isEmpty()) {
                loadPopularBundle()
            } else {
                null
            }
            val b = _binding ?: return@launch
            renderSpecies(b, trending, popular)
            // Popular diseases is genuinely trending-only — no bundled fallback
            // (the always-on Disease guide below already covers that need).
            if (trending == null) {
                b.sectionPopular.visibility = View.GONE
            } else {
                renderPopular(b, trending)
            }
        }
    }

    /**
     * Render the species carousel. Trending species near the user take
     * priority; when the feed has none (fresh install, sparse region, or the
     * request failed) we fall back to a bundled "popular houseplants" list so
     * the row is never empty — the same always-on contract the Disease guide
     * has.
     */
    /** Fetch the personalized popular bundle, cached 24h. Null on any failure
     *  so the caller falls back to the bundled SpeciesCatalog list. */
    private suspend fun loadPopularBundle(): PopularResponse? {
        val cacheKey = "popular:v2:server|$locale"
        return com.plantdiseases.app.util.ResponseCache
            .get(cacheKey, PopularResponse::class.java)
            ?: runCatching {
                apiClient.popular(locale = locale)
            }.getOrNull()?.takeIf { it.items.isNotEmpty() }?.also {
                com.plantdiseases.app.util.ResponseCache.put(
                    cacheKey,
                    it,
                    com.plantdiseases.app.util.ResponseCache.TTL_POPULAR_MS,
                )
            }
    }

    private fun renderSpecies(
        b: FragmentHomeBinding,
        t: TrendingResponse?,
        popular: PopularResponse?,
    ) {
        b.sectionSpecies.visibility = View.VISIBLE
        if (t != null && t.topSpecies.isNotEmpty()) {
            b.tvSpeciesTitle.setText(R.string.home_trending)
            b.tvTrendingScope.text = scopeLabel(t)
            b.tvTrendingScope.visibility = View.VISIBLE
            speciesAdapter.submit(
                t.topSpecies.map { s ->
                    SpeciesCarouselAdapter.Item(
                        scientific = s.species,
                        title = s.species,
                        subtitle = resources.getQuantityString(
                            R.plurals.home_species_count,
                            s.count,
                            s.count,
                        ),
                    )
                },
            )
        } else {
            setPopularTitle(b, popular)
            b.tvTrendingScope.visibility = View.GONE
            val tag = getString(R.string.home_species_popular_tag)
            val items = if (popular != null && popular.items.isNotEmpty()) {
                popular.items.map { p ->
                    // Pre-seed the image cache so loadSpeciesImage paints instantly,
                    // no per-species Wikipedia call.
                    speciesImageCache[p.scientific] = p.imageUrl
                    SpeciesCarouselAdapter.Item(
                        scientific = p.scientific,
                        title = if (isRu) p.commonRu else p.commonEn,
                        subtitle = when {
                            p.percent > 0 -> getString(R.string.home_popular_percent, p.percent)
                            p.count > 0 -> resources.getQuantityString(
                                R.plurals.home_species_count,
                                p.count,
                                p.count,
                            )
                            else -> tag
                        },
                    )
                }
            } else {
                // Last-resort bundled list so the row is never empty.
                SpeciesCatalog.popular.map { sp ->
                    SpeciesCarouselAdapter.Item(
                        scientific = sp.scientific,
                        title = if (isRu) sp.commonRu else sp.commonEn,
                        subtitle = tag,
                    )
                }
            }
            speciesAdapter.submit(items)
        }
    }

    private fun setPopularTitle(b: FragmentHomeBinding, popular: PopularResponse?) {
        val country = if (isRu) popular?.countryNameRu else popular?.countryNameEn
        val isCountry = popular?.scope == "country" && !country.isNullOrBlank()
        val title = if (isCountry) {
            getString(R.string.home_popular_in_country, country)
        } else {
            getString(R.string.home_popular_worldwide)
        }
        // Accent range: the country name for a country scope, else the
        // locality word(s) ("worldwide" / "во всем мире") — everything after
        // the leading "Popular" / "Популярно". The worldwide title can't be
        // matched against the server's country_name string verbatim (case /
        // wording differ across locales), so we slice off the leading word
        // instead and highlight the rest the same way the country name is.
        val start: Int
        val end: Int
        if (isCountry) {
            val s = title.indexOf(country!!)
            start = if (s >= 0) s else 0
            end = if (s >= 0) s + country.length else title.length
        } else {
            val sp = title.indexOf(' ')
            start = if (sp in 0 until title.lastIndex) sp + 1 else 0
            end = title.length
        }
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

    private fun renderPopular(b: FragmentHomeBinding, t: TrendingResponse) {
        // Only diseases we have a bundled doc for — guarantees the card has
        // an icon + localized name and the tap opens a real detail screen.
        val items = t.topDiseases.mapNotNull { d ->
            val gi = DiseaseCatalog.guideItemForClass(d.disease) ?: return@mapNotNull null
            DiseaseCarouselAdapter.Item(
                guideId = gi.id,
                iconRes = gi.iconRes,
                title = DiseaseLabels.displayName(d.disease, isRu)
                    ?: (if (isRu) gi.titleRu else gi.titleEn),
                subtitle = resources.getQuantityString(
                    R.plurals.home_disease_count,
                    d.count,
                    d.count,
                ),
            )
        }
        if (items.isEmpty()) {
            b.sectionPopular.visibility = View.GONE
            return
        }
        b.sectionPopular.visibility = View.VISIBLE
        popularAdapter.submit(items)
    }

    private fun scopeLabel(t: TrendingResponse): String {
        // Prefer the server's localized country NAME (e.g. "Spain") over the
        // raw ISO bucket (e.g. "ES"); the server attaches it alongside the
        // code, exactly as the "Popular in <country>" title uses it.
        val country = (if (isRu) t.countryNameRu else t.countryNameEn)
            ?.takeIf { it.isNotBlank() } ?: t.country
        return when (t.scope) {
            "region" -> listOfNotNull(t.region, country).joinToString(", ")
            "country" -> country ?: getString(R.string.home_trending_global)
            else -> getString(R.string.home_trending_global)
        }
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
        paint.shader = LinearGradient(
            x, 0f, x + width, 0f, colors, null, Shader.TileMode.CLAMP,
        )
        canvas.drawText(text, start, end, x, y.toFloat(), paint)
        paint.shader = previous
    }
}
