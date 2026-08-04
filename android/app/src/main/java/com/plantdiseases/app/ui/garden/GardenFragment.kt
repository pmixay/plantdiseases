package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputFilter
import android.text.InputType
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.view.inputmethod.InputMethodManager
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.children
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DecodeFormat
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.card.MaterialCardView
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.databinding.FragmentGardenBinding
import com.plantdiseases.app.databinding.ItemPlantCardBinding
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.util.CareSchedule
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.util.applyStatusBarTopInset
import com.plantdiseases.app.util.localizedSpeciesName
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Garden tab. The screen scaffold (hero, plant list container, empty
 * state and FAB) lives in ``fragment_garden.xml``; each plant row is
 * inflated from ``item_plant_card.xml``. Only the genuinely dynamic
 * parts — thumbnail loading, the health dot, the status chips and the
 * add/edit dialog — are still built in code.
 */
@AndroidEntryPoint
class GardenFragment : Fragment() {

    private var _binding: FragmentGardenBinding? = null
    private val binding get() = _binding!!

    /** Full, unfiltered garden as last loaded from Room. The search box
     *  filters a render off this list without re-querying. */
    private var allSummaries: List<PlantRepository.PlantSummary> = emptyList()

    /** True until the first plant load renders. Gates the entry fade to the
     *  genuine first paint and lets onResume skip the redundant reload that
     *  fires right after onViewCreated's preload. */
    private var isFirstLoad = true

    /** Plant count above which the search box appears. */
    private val searchVisibleThreshold = 6

    @Inject
    lateinit var plantRepository: PlantRepository

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentGardenBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.gardenScroll.applyStatusBarTopInset()
        binding.gardenAddFab.setOnClickListener {
            Haptics.medium(requireContext())
            showPlantDialog(null)
        }
        binding.gardenEmptyCta.setOnClickListener {
            Haptics.medium(requireContext())
            showPlantDialog(null)
        }
        // Live search over the loaded garden. No debounce needed — the
        // filter runs against the in-memory list, not Room. Skip the
        // stagger animation so typing doesn't make the list flash.
        binding.gardenSearch.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) {
                // Skip the watcher fire from view-state restoration that can
                // land before the first Room load — renderGarden would show
                // the empty-garden chrome for a frame. (With an empty garden
                // the search box is hidden, so nothing is lost.)
                if (_binding != null && allSummaries.isNotEmpty()) {
                    renderGarden(animate = false)
                }
            }
        })
        // Collapse the extended FAB to an icon while scrolling down so it
        // stops sitting over plant-card text; re-extend at the top / on the
        // way back up.
        binding.gardenScroll.setOnScrollChangeListener { _, _, scrollY, _, oldScrollY ->
            val fab = _binding?.gardenAddFab ?: return@setOnScrollChangeListener
            if (fab.visibility != View.VISIBLE) return@setOnScrollChangeListener
            when {
                scrollY <= 0 && !fab.isExtended -> fab.extend()
                scrollY > oldScrollY + 4 && fab.isExtended -> fab.shrink()
                scrollY < oldScrollY - 4 && !fab.isExtended -> fab.extend()
            }
        }
        // Preload here so this tab is already populated while it sits off-screen
        // as the pager's neighbour and slides in showing plants, not a blank
        // list. The old double fade-in is avoided by the isFirstLoad guard in
        // onResume below, not by leaving the load out.
        loadPlants()
    }

    override fun onResume() {
        super.onResume()
        // onViewCreated's preload runs the first (fading) render; this fires
        // straight after it, while isFirstLoad is still true → skip, no double
        // fade. On real returns it refreshes with animate=false so the visible
        // list doesn't replay the entry animation.
        if (_binding != null && !isFirstLoad) loadPlants(animate = false)
    }

    override fun onDestroyView() {
        (activity as? MainActivity)?.apply {
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_HERO)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_PLANTS)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_ADD)
        }
        super.onDestroyView()
        _binding = null
    }

    private fun loadPlants(animate: Boolean = true) {
        lifecycleScope.launch {
            val plants = plantRepository.getPlants()
            if (_binding != null) {
                allSummaries = plants
                // Fade on the first paint; a refresh passes animate=false so the
                // already-visible list re-renders without replaying the fade.
                renderGarden(animate = animate && isFirstLoad)
                isFirstLoad = false
            }
        }
    }

    /** Current trimmed search query, or empty when the box is hidden/blank. */
    private fun currentQuery(): String =
        binding.gardenSearch.text?.toString()?.trim().orEmpty()

    /** Case-insensitive match across name, species and common/scientific name. */
    private fun matchesQuery(summary: PlantRepository.PlantSummary, query: String): Boolean {
        if (query.isEmpty()) return true
        val p = summary.plant
        return p.name.contains(query, ignoreCase = true) ||
            p.species?.contains(query, ignoreCase = true) == true ||
            p.commonName?.contains(query, ignoreCase = true) == true ||
            p.scientificName?.contains(query, ignoreCase = true) == true
    }

    /** Re-render the whole tab from [allSummaries]: screen chrome (empty
     *  state / FAB / summary / search visibility) follows the FULL garden;
     *  the list itself follows the search filter. */
    private fun renderGarden(animate: Boolean) {
        val all = allSummaries
        binding.gardenEmptyState.visibility = if (all.isEmpty()) View.VISIBLE else View.GONE
        binding.gardenPlantsContainer.visibility = if (all.isEmpty()) View.GONE else View.VISIBLE
        // When the garden is empty the centred "Add your first plant" CTA is
        // the only call to action — hide the floating button so the two
        // don't compete (and re-extend it for the populated state).
        if (all.isEmpty()) {
            binding.gardenAddFab.visibility = View.GONE
        } else {
            binding.gardenAddFab.visibility = View.VISIBLE
            binding.gardenAddFab.extend()
        }
        // Two independent counts, two independently-inflected plurals:
        // garden_summary's quantity follows the PLANT count, so the scan
        // part is pre-pluralised via scan_count and passed in as %2$s
        // (1 plant used to force the plant-form onto "N сканов" in RU).
        val scanTotal = all.sumOf { it.scanCount }
        binding.gardenSummaryBody.text = resources.getQuantityString(
            R.plurals.garden_summary,
            all.size,
            all.size,
            resources.getQuantityString(R.plurals.scan_count, scanTotal, scanTotal),
        )
        // The search box earns its vertical space only once the list is big
        // enough to need it. Keep it visible while a query is active even if
        // the threshold check would hide it (e.g. a plant was just deleted).
        val query = currentQuery()
        binding.gardenSearchLayout.visibility =
            if (all.size > searchVisibleThreshold || query.isNotEmpty()) {
                View.VISIBLE
            } else {
                View.GONE
            }

        renderPlantList(all.filter { matchesQuery(it, query) }, query, animate)
        registerOnboardingAnchors(all.isEmpty())
    }

    private fun registerOnboardingAnchors(empty: Boolean) {
        val host = activity as? MainActivity ?: return
        val listAnchor = if (empty) {
            binding.gardenEmptyState
        } else {
            binding.gardenPlantsContainer.children
                .firstOrNull { it is MaterialCardView }
                ?: binding.gardenPlantsContainer
        }
        val addAnchor = if (empty) binding.gardenEmptyCta else binding.gardenAddFab
        host.registerOnboardingAnchor(OnboardingAnchorKey.GARDEN_HERO, binding.gardenHero)
        host.registerOnboardingAnchor(OnboardingAnchorKey.GARDEN_PLANTS, listAnchor)
        host.registerOnboardingAnchor(
            OnboardingAnchorKey.GARDEN_ADD,
            addAnchor,
            pauseAfterTarget = true,
            onInlineAction = host::startGardenAttachScanGuidance,
        )
    }

    private fun renderPlantList(
        plants: List<PlantRepository.PlantSummary>,
        query: String,
        animate: Boolean,
    ) {
        val container = binding.gardenPlantsContainer
        container.removeAllViews()
        if (plants.isEmpty() && query.isNotEmpty()) {
            // Search matched nothing — quiet inline message, NOT the big
            // empty-garden illustration (the garden itself isn't empty).
            container.addView(
                TextView(requireContext()).apply {
                    text = getString(R.string.garden_search_empty, query)
                    textSize = 14f
                    setTextColor(requireContext().getColor(R.color.on_surface_secondary))
                    gravity = Gravity.CENTER
                    setPadding(8.dp(), 24.dp(), 8.dp(), 24.dp())
                }
            )
            return
        }

        var animIndex = 0
        val emit: (View) -> Unit = { view ->
            if (animate) {
                view.alpha = 0f
                view.translationY = 24.dp().toFloat()
                container.addView(view)
                view.animate()
                    .alpha(1f)
                    .translationY(0f)
                    .setDuration(260)
                    .setStartDelay((animIndex * 45).toLong())
                    .setInterpolator(DecelerateInterpolator())
                    .start()
                animIndex++
            } else {
                container.addView(view)
            }
        }

        // "Needs attention" first: plants whose soonest enabled reminder is
        // genuinely overdue surface in their own section at the top so they
        // aren't buried halfway down the list. Everything else follows in
        // creation order (the query default) below.
        val nowMs = System.currentTimeMillis()
        val (due, rest) = plants.partition { summary ->
            summary.nextAction?.let { CareSchedule.isOverdue(it.dueAt, nowMs) } == true
        }
        if (due.isNotEmpty()) {
            emit(buildSectionHeader(getString(R.string.garden_section_attention)))
            due.sortedBy { it.nextAction?.dueAt ?: Long.MAX_VALUE }
                .forEach { emit(buildPlantCard(it)) }
        }

        rest.forEach { emit(buildPlantCard(it)) }
    }

    /** Sticky-header-style section label: small uppercase text that reads as
     *  a separator without dominating the stack. */
    private fun buildSectionHeader(label: String): View = TextView(requireContext()).apply {
        text = label.uppercase()
        textSize = 11f
        letterSpacing = 0.08f
        setTextColor(requireContext().getColor(R.color.on_surface_secondary))
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply {
            topMargin = 14.dp()
            bottomMargin = 6.dp()
        }
        setPadding(4.dp(), 0, 4.dp(), 0)
    }

    private fun buildPlantCard(summary: PlantRepository.PlantSummary): View {
        val plant = summary.plant
        val ctx = requireContext()
        val row = ItemPlantCardBinding.inflate(layoutInflater, binding.gardenPlantsContainer, false)

        // ── Thumbnail: cover photo first, latest scan second, leaf icon
        // last. clipToOutline against the oval background keeps it round.
        row.plantThumb.clipToOutline = true
        val thumbSource: String? = plant.coverPhotoUri ?: summary.latestScan?.imagePath
        if (thumbSource != null) {
            row.plantThumb.scaleType = ImageView.ScaleType.CENTER_CROP
            row.plantThumb.setPadding(0, 0, 0, 0)
            row.plantThumb.colorFilter = null
            Glide.with(this)
                .load(thumbSource)
                // RGB_565 halves the per-card thumbnail RAM on the garden list.
                .format(DecodeFormat.PREFER_RGB_565)
                .transform(
                    CenterCrop(),
                    // Match the bg_thumb_rounded backdrop / design radius scale.
                    RoundedCorners(resources.getDimensionPixelSize(R.dimen.radius_md)),
                )
                .placeholder(R.drawable.ic_garden)
                .error(R.drawable.ic_garden)
                .into(row.plantThumb)
        } else {
            row.plantThumb.setImageResource(R.drawable.ic_garden)
            row.plantThumb.setColorFilter(ctx.getColor(R.color.primary))
            row.plantThumb.scaleType = ImageView.ScaleType.CENTER_INSIDE
            row.plantThumb.setPadding(22.dp(), 22.dp(), 22.dp(), 22.dp())
        }
        applyHealthDot(row.plantHealthDot, summary.latestScan)

        // ── Name → species → status chips. The status row carries the
        // health pill + soonest reminder; both optional so the card
        // collapses gracefully on a freshly-added plant.
        row.plantName.text = plant.name
        val speciesText = localizedSpeciesName(
            plant.scientificName,
            plant.species ?: plant.commonName,
            LocaleHelper.isRussian(ctx),
        )
        if (speciesText != null) {
            row.plantSpecies.text = speciesText
            row.plantSpecies.visibility = View.VISIBLE
        } else {
            row.plantSpecies.visibility = View.GONE
        }

        row.plantStatusRow.removeAllViews()
        val healthChip = buildHealthChip(summary.latestScan)
        val nextChip = buildNextActionChip(summary)
        if (healthChip != null || nextChip != null) {
            healthChip?.let { hc ->
                // When BOTH chips are present, let the (variable-length)
                // health/disease chip absorb the squeeze and ellipsize via
                // layout_weight, so the fixed next-action chip always keeps
                // its full intrinsic width. A long localised disease badge
                // ("Краевой ожог (недолив)") used to squeeze the trailing
                // reminder chip down to a bare "…" blue stub.
                if (nextChip != null) {
                    (hc.layoutParams as? LinearLayout.LayoutParams)?.apply {
                        width = 0
                        weight = 1f
                    }
                }
                row.plantStatusRow.addView(hc)
            }
            nextChip?.let { row.plantStatusRow.addView(it) }
            row.plantStatusRow.visibility = View.VISIBLE
        } else {
            row.plantStatusRow.visibility = View.GONE
            if (speciesText == null) {
                // No scans, no reminders, no species — one quiet subtitle
                // so the card doesn't read as a single floating word.
                row.plantSpecies.text = getString(R.string.no_scans_attached)
                row.plantSpecies.visibility = View.VISIBLE
            }
        }

        row.plantCard.setOnClickListener {
            Haptics.tick(ctx)
            startActivity(
                Intent(ctx, PlantDetailActivity::class.java)
                    .putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plant.id)
            )
        }
        row.plantCard.setOnLongClickListener {
            Haptics.medium(ctx)
            showPlantContextMenu(row.plantCard, plant)
            true
        }
        return row.root
    }

    /** Tiny circular health indicator on the thumbnail's top-right
     *  corner. Green = healthy, red = disease, blue = NAP. Hidden when
     *  there's no scan to read state from. */
    private fun applyHealthDot(dot: View, scan: ScanEntity?) {
        if (scan == null) {
            dot.visibility = View.GONE
            return
        }
        val ctx = requireContext()
        val isNotPlant = scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
            scan.diseaseName.equals("Не растение", ignoreCase = true)
        val dotColor = when {
            isNotPlant -> ctx.getColor(R.color.neutral_blue)
            scan.isHealthy -> ctx.getColor(R.color.healthy_green)
            else -> ctx.getColor(R.color.disease_red)
        }
        dot.background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(dotColor)
            setStroke(2.dp(), ctx.getColor(R.color.surface))
        }
        // Sits inside the thumbnail's top-end corner (margin from XML) —
        // the old negative offsets left it half-floating off the image.
        dot.visibility = View.VISIBLE
    }

    /** Colour-coded health pill (healthy = green, disease = red, NAP =
     *  neutral) with the latest verdict + confidence. null when there's
     *  no scan yet. */
    private fun buildHealthChip(scan: ScanEntity?): View? {
        if (scan == null) return null
        val ctx = requireContext()
        val isNotPlant = scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
            scan.diseaseName.equals("Не растение", ignoreCase = true)
        val (bgColor, fgColor, text) = when {
            isNotPlant -> Triple(
                ctx.getColor(R.color.neutral_blue_bg),
                ctx.getColor(R.color.neutral_blue),
                getString(R.string.status_not_a_plant),
            )
            scan.isHealthy -> Triple(
                ctx.getColor(R.color.healthy_green_bg),
                ctx.getColor(R.color.healthy_green),
                getString(
                    R.string.garden_card_health_healthy,
                    (scan.confidence * 100f).toInt(),
                ),
            )
            else -> Triple(
                ctx.getColor(R.color.disease_red_bg),
                ctx.getColor(R.color.disease_red),
                getString(
                    R.string.garden_card_health_disease,
                    // RU UI shows the localised verdict, mirroring
                    // ResultActivity — diseaseName is always English.
                    if (LocaleHelper.isRussian(ctx)) scan.diseaseNameRu else scan.diseaseName,
                ),
            )
        }
        return chip(text, bgColor, fgColor)
    }

    /** Soonest reminder due ("💧 in 2 days" / "💧 overdue"). null when
     *  there is no enabled reminder on the plant. */
    private fun buildNextActionChip(summary: PlantRepository.PlantSummary): View? {
        val action = summary.nextAction ?: return null
        val ctx = requireContext()
        val nowMs = System.currentTimeMillis()
        val overdue = CareSchedule.isOverdue(action.dueAt, nowMs)
        val deltaDays = CareSchedule.daysUntil(action.dueAt, nowMs)
        val icon = when (action.type) {
            "watering" -> "💧"
            "fertilize" -> "🌱"
            else -> "⏰"
        }
        val label = when {
            // Only a genuinely-past reminder is "overdue". Integer-day
            // truncation previously flagged anything due within the next 24h
            // (deltaDays == 0) as overdue before it had even arrived.
            overdue -> getString(R.string.garden_card_action_overdue, icon)
            deltaDays <= 1 -> getString(R.string.garden_card_action_tomorrow, icon)
            else -> getString(R.string.garden_card_action_in_days, icon, deltaDays)
        }
        val (bg, fg) = if (overdue) {
            ctx.getColor(R.color.disease_red_bg) to ctx.getColor(R.color.disease_red)
        } else {
            ctx.getColor(R.color.neutral_blue_bg) to ctx.getColor(R.color.neutral_blue)
        }
        val chip = chip(label, bg, fg)
        (chip.layoutParams as? LinearLayout.LayoutParams)?.marginStart = 6.dp()
        return chip
    }

    private fun chip(text: String, bgColor: Int, fgColor: Int): TextView =
        TextView(requireContext()).apply {
            this.text = text
            textSize = 12f
            setTextColor(fgColor)
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            setPadding(12.dp(), 6.dp(), 12.dp(), 6.dp())
            background = GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                // Pill radius from the design-system scale rather than a
                // magic constant, so the chips match the other rounded
                // surfaces on the card.
                cornerRadius = resources.getDimension(R.dimen.radius_round)
                setColor(bgColor)
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
        }

    private fun showPlantContextMenu(anchor: View, plant: PlantEntity) {
        PopupMenu(requireContext(), anchor).apply {
            menu.add(0, 1, 0, getString(R.string.edit_plant))
            menu.add(0, 2, 1, getString(R.string.delete_plant))
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> {
                        showPlantDialog(plant)
                        true
                    }
                    2 -> {
                        confirmDeletePlant(plant)
                        true
                    }
                    else -> false
                }
            }
            show()
        }
    }

    private fun confirmDeletePlant(plant: PlantEntity) {
        MaterialAlertDialogBuilder(requireContext())
            .setTitle(plant.name)
            .setMessage(R.string.delete_plant_confirm)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.delete) { _, _ ->
                lifecycleScope.launch {
                    // Generic reminders carry their row id in the unique
                    // work name, so the rows must be fetched before the
                    // CASCADE delete wipes them.
                    ReminderManager.cancelAll(
                        requireContext(),
                        plant.id,
                        plantRepository.getReminders(plant.id),
                    )
                    plantRepository.deletePlant(plant.id)
                    Toast.makeText(
                        requireContext(),
                        R.string.plant_deleted,
                        Toast.LENGTH_SHORT,
                    ).show()
                    loadPlants()
                }
            }
            .show()
    }

    private fun showPlantDialog(existing: PlantEntity?) {
        val container = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, 0)
        }

        val header = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(18.dp(), 18.dp(), 18.dp(), 18.dp())
            setBackgroundResource(R.drawable.bg_garden_hero)
        }
        header.addView(
            FrameLayout(requireContext()).apply {
                setBackgroundResource(R.drawable.bg_circle_semi)
                layoutParams = LinearLayout.LayoutParams(52.dp(), 52.dp())
                addView(
                    ImageView(requireContext()).apply {
                        setImageResource(R.drawable.ic_garden)
                        setColorFilter(requireContext().getColor(R.color.on_hero))
                        layoutParams = FrameLayout.LayoutParams(28.dp(), 28.dp(), Gravity.CENTER)
                    }
                )
            }
        )
        header.addView(
            LinearLayout(requireContext()).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                    .apply { marginStart = 14.dp() }
                addView(
                    TextView(requireContext()).apply {
                        text = getString(if (existing == null) R.string.add_plant else R.string.edit_plant)
                        textSize = 20f
                        setTextColor(requireContext().getColor(R.color.on_hero))
                        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                    }
                )
                addView(
                    TextView(requireContext()).apply {
                        text = getString(
                            if (existing == null) {
                                R.string.plant_dialog_add_subtitle
                            } else {
                                R.string.plant_dialog_edit_subtitle
                            }
                        )
                        textSize = 13f
                        setTextColor(requireContext().getColor(R.color.on_hero_secondary))
                        setLineSpacing(3.dp().toFloat(), 1f)
                        layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            LinearLayout.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = 3.dp() }
                    }
                )
            }
        )

        val fields = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(18.dp(), 18.dp(), 18.dp(), 4.dp())
        }
        val name = inputField(getString(R.string.plant_name), existing?.name, InputType.TYPE_CLASS_TEXT)
        val species = inputField(getString(R.string.plant_species), existing?.species, InputType.TYPE_CLASS_TEXT)
        fields.addView(name.first)
        fields.addView(species.first)
        container.addView(header)
        container.addView(fields)

        val dialog = MaterialAlertDialogBuilder(requireContext())
            .setView(container)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.save, null)
            .show()

        dialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val plantName = name.second.text?.toString()?.trim().orEmpty()
            if (plantName.isEmpty()) {
                name.first.error = getString(R.string.plant_name_required)
                return@setOnClickListener
            }
            hideKeyboard(dialog.window?.decorView ?: name.second)
            dialog.dismiss()
            lifecycleScope.launch {
                plantRepository.savePlant(
                    plantName,
                    species.second.text?.toString(),
                    existing,
                )
                awaitKeyboardClosed()
                if (_binding != null) {
                    allSummaries = plantRepository.getPlants()
                    renderGarden(animate = true)
                    stabilizeFloatingAddButton()
                }
            }
        }
    }

    private fun hideKeyboard(anchor: View) {
        binding.gardenRoot.findFocus()?.clearFocus()
        val inputManager = requireContext()
            .getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        inputManager.hideSoftInputFromWindow(anchor.windowToken, 0)
    }

    private suspend fun awaitKeyboardClosed() {
        repeat(10) {
            val imeVisible = ViewCompat.getRootWindowInsets(binding.gardenRoot)
                ?.isVisible(WindowInsetsCompat.Type.ime()) == true
            if (!imeVisible) {
                delay(48)
                return
            }
            delay(32)
        }
        delay(96)
    }

    /** The FAB lives in XML now with a fixed bottom-end gravity, but we
     *  still nudge it back to front after a dialog + IME cycle so it
     *  can't end up underneath a freshly re-rendered list. */
    private fun stabilizeFloatingAddButton() {
        val fab = _binding?.gardenAddFab ?: return
        fab.bringToFront()
        fab.requestLayout()
    }

    private fun inputField(
        hint: String,
        value: String?,
        type: Int,
    ): Pair<TextInputLayout, TextInputEditText> {
        val input = TextInputEditText(requireContext()).apply {
            filters = arrayOf(InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH))
            setText(value.orEmpty())
            inputType = type
            setSingleLine(true)
        }
        val layout = TextInputLayout(requireContext()).apply {
            this.hint = hint
            addView(input)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 10.dp() }
        }
        return layout to input
    }

    private companion object {
        private const val TEXT_FIELD_MAX_LENGTH = 1024
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
