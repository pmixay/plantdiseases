package com.plantdiseases.app.ui.garden

import android.content.Intent
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import androidx.core.view.children
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DecodeFormat
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.tabs.TabLayout
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.RoomEntity
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.databinding.FragmentGardenBinding
import com.plantdiseases.app.databinding.ItemRoomCardBinding
import com.plantdiseases.app.ui.garden.care.CareCalendarView
import com.plantdiseases.app.ui.garden.care.CareDayActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.ui.widget.LunarCalendarView
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.CareSchedule
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.PlantCategoryClassifier
import com.plantdiseases.app.util.RoomPresets
import com.plantdiseases.app.util.applyStatusBarTopInset
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import javax.inject.Inject

/**
 * Garden tab. Two panes behind a segmented control — Rooms (room cards,
 * each holding a plant collection) and Care calendar (month grid + a
 * "Today" summary card) — replacing the old flat plant list. The screen
 * scaffold lives in ``fragment_garden.xml``; room rows come from
 * ``item_room_card.xml``. See docs/ROADMAP.md "Garden refactor".
 */
@AndroidEntryPoint
class GardenFragment : Fragment() {

    private var _binding: FragmentGardenBinding? = null
    private val binding get() = _binding!!

    private var allSummaries: List<PlantRepository.PlantSummary> = emptyList()
    private var allRooms: List<RoomRepository.RoomSummary> = emptyList()

    /** True until the first load renders. Gates the entry fade to the
     *  genuine first paint and lets onResume skip the redundant reload that
     *  fires right after onViewCreated's preload. */
    private var isFirstLoad = true

    private var searchVisible = false
    private var selectedPane = PANE_ROOMS
    private var selectedCalendarSubPane = SUBPANE_CARE

    @Inject
    lateinit var plantRepository: PlantRepository

    @Inject
    lateinit var roomRepository: RoomRepository

    @Inject
    lateinit var careRepository: CareRepository

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
        selectedPane = savedInstanceState?.getInt(KEY_PANE, PANE_ROOMS) ?: PANE_ROOMS
        selectedCalendarSubPane = savedInstanceState?.getInt(KEY_CALENDAR_SUBPANE, SUBPANE_CARE) ?: SUBPANE_CARE

        binding.gardenAddFab.setOnClickListener {
            Haptics.medium(requireContext())
            openAddPlantDialog()
        }
        binding.gardenEmptyCta.setOnClickListener {
            Haptics.medium(requireContext())
            openAddPlantDialog()
        }
        binding.gardenAddRoom.setOnClickListener {
            Haptics.medium(requireContext())
            showCreateRoomDialog()
        }
        binding.gardenSearchToggle.setOnClickListener {
            searchVisible = !searchVisible
            if (!searchVisible) binding.gardenSearch.text = null
            renderGarden(animate = false)
            if (searchVisible) binding.gardenSearch.requestFocus()
        }

        setupSegmentedControl()
        setupCalendarSegmentedControl()

        binding.gardenSearch.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) {
                if (_binding != null && (allSummaries.isNotEmpty() || allRooms.isNotEmpty())) {
                    renderRoomsPane(animate = false)
                }
            }
        })

        binding.gardenScroll.setOnScrollChangeListener { _, _, scrollY, _, oldScrollY ->
            val fab = _binding?.gardenAddFab ?: return@setOnScrollChangeListener
            if (fab.visibility != View.VISIBLE) return@setOnScrollChangeListener
            when {
                scrollY <= 0 && !fab.isExtended -> fab.extend()
                scrollY > oldScrollY + 4 && fab.isExtended -> fab.shrink()
                scrollY < oldScrollY - 4 && !fab.isExtended -> fab.extend()
            }
        }

        loadGarden()
    }

    override fun onResume() {
        super.onResume()
        if (_binding != null && !isFirstLoad) loadGarden(animate = false)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(KEY_PANE, selectedPane)
        outState.putInt(KEY_CALENDAR_SUBPANE, selectedCalendarSubPane)
    }

    override fun onDestroyView() {
        (activity as? MainActivity)?.apply {
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_HERO)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_PLANTS)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_ADD)
        }
        super.onDestroyView()
        _binding = null
        // gardenCalendarContainer/gardenLunarContainer (and these views)
        // belong to the view we just tore down. Without this reset,
        // ensureCalendarViewAttached()/ensureLunarViewAttached() early-return
        // on the next onCreateView and that pane renders empty until the
        // Fragment instance itself is recreated.
        calendarView = null
        lunarView = null
    }

    private fun setupSegmentedControl() {
        val tabs = binding.gardenSegment
        tabs.removeAllTabs()
        tabs.addTab(tabs.newTab().setText(R.string.garden_tab_rooms))
        tabs.addTab(tabs.newTab().setText(R.string.garden_tab_calendar))
        tabs.getTabAt(selectedPane)?.select()
        applyPaneVisibility()
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                selectedPane = tab.position
                applyPaneVisibility()
                if (selectedPane == PANE_CALENDAR) loadCalendarPane()
            }

            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
    }

    /** Care/Lunar inner toggle inside the Calendars pane — same TabLayout
     *  widget/styling as [setupSegmentedControl]'s outer control. */
    private fun setupCalendarSegmentedControl() {
        val tabs = binding.gardenCalendarSegment
        tabs.removeAllTabs()
        tabs.addTab(tabs.newTab().setText(R.string.garden_calendar_tab_care))
        tabs.addTab(tabs.newTab().setText(R.string.garden_calendar_tab_lunar))
        tabs.getTabAt(selectedCalendarSubPane)?.select()
        applyCalendarSubPaneVisibility()
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                selectedCalendarSubPane = tab.position
                applyCalendarSubPaneVisibility()
            }

            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
    }

    private fun applyCalendarSubPaneVisibility() {
        binding.gardenCareSubpane.visibility = if (selectedCalendarSubPane == SUBPANE_CARE) View.VISIBLE else View.GONE
        val onLunar = selectedCalendarSubPane == SUBPANE_LUNAR
        binding.gardenLunarContainer.visibility = if (onLunar && allSummaries.isNotEmpty()) View.VISIBLE else View.GONE
        binding.gardenLunarEmpty.visibility = if (onLunar && allSummaries.isEmpty()) View.VISIBLE else View.GONE
        if (onLunar && allSummaries.isNotEmpty()) refreshLunarAggregate()
    }

    private fun applyPaneVisibility() {
        binding.gardenRoomsPane.visibility = if (selectedPane == PANE_ROOMS) View.VISIBLE else View.GONE
        binding.gardenCalendarPane.visibility = if (selectedPane == PANE_CALENDAR) View.VISIBLE else View.GONE
        // Searching a month grid means nothing — the toggle and box only
        // apply to the Rooms pane.
        binding.gardenSearchToggle.visibility = if (selectedPane == PANE_ROOMS) View.VISIBLE else View.INVISIBLE
        if (selectedPane == PANE_CALENDAR) {
            binding.gardenSearchLayout.visibility = View.GONE
        } else {
            renderRoomsPane(animate = false)
        }
    }

    private fun loadGarden(animate: Boolean = true) {
        lifecycleScope.launch {
            val plants = plantRepository.getPlants()
            val rooms = roomRepository.getRooms()
            if (_binding != null) {
                allSummaries = plants
                allRooms = rooms
                renderGarden(animate = animate && isFirstLoad)
                isFirstLoad = false
                if (selectedPane == PANE_CALENDAR) loadCalendarPane()
            }
        }
    }

    /** Screen chrome (empty state / FAB / summary / search visibility)
     *  follows the FULL garden; the rooms pane itself follows the filter. */
    private fun renderGarden(animate: Boolean) {
        val hasAnyPlant = allSummaries.isNotEmpty()
        binding.gardenEmptyState.visibility = if (hasAnyPlant) View.GONE else View.VISIBLE
        binding.gardenSegment.visibility = if (hasAnyPlant) View.VISIBLE else View.GONE
        if (!hasAnyPlant) {
            binding.gardenAddFab.visibility = View.GONE
        } else {
            binding.gardenAddFab.visibility = View.VISIBLE
            binding.gardenAddFab.extend()
        }

        val scanTotal = allSummaries.sumOf { it.scanCount }
        binding.gardenSummaryBody.text = if (hasAnyPlant) {
            getString(
                R.string.garden_subtitle_combined,
                resources.getQuantityString(R.plurals.room_count, allRooms.size, allRooms.size),
                resources.getQuantityString(
                    R.plurals.garden_summary,
                    allSummaries.size,
                    allSummaries.size,
                    resources.getQuantityString(R.plurals.scan_count, scanTotal, scanTotal),
                ),
            )
        } else {
            getString(R.string.garden_subtitle)
        }

        binding.gardenSearchLayout.visibility = if (searchVisible && selectedPane == PANE_ROOMS) {
            View.VISIBLE
        } else {
            View.GONE
        }

        if (hasAnyPlant && selectedPane == PANE_ROOMS) renderRoomsPane(animate)
        registerOnboardingAnchors(!hasAnyPlant)
    }

    private fun currentQuery(): String = binding.gardenSearch.text?.toString()?.trim().orEmpty()

    private fun registerOnboardingAnchors(empty: Boolean) {
        val host = activity as? MainActivity ?: return
        val listAnchor = if (empty) {
            binding.gardenEmptyState
        } else {
            binding.gardenRoomsContainer.children.firstOrNull() ?: binding.gardenRoomsContainer
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

    // ─── Rooms pane ─────────────────────────────────────────────────────

    private fun renderRoomsPane(animate: Boolean) {
        if (allSummaries.isEmpty()) return
        val container = binding.gardenRoomsContainer
        container.removeAllViews()
        val query = currentQuery()

        val plantsByRoom: Map<Long?, List<PlantRepository.PlantSummary>> =
            allSummaries.groupBy { it.plant.roomId }

        val matchingRooms = allRooms.filter { summary ->
            matchesRoomQuery(summary, plantsByRoom[summary.room.id].orEmpty(), query)
        }
        val unassigned = plantsByRoom[null].orEmpty()
        val unassignedMatches = query.isEmpty() ||
            unassigned.any { it.plant.name.contains(query, ignoreCase = true) }

        val noUnassignedMatch = unassigned.isEmpty() || !unassignedMatches
        if (matchingRooms.isEmpty() && noUnassignedMatch && query.isNotEmpty()) {
            container.addView(
                TextView(requireContext()).apply {
                    text = getString(R.string.garden_search_empty, query)
                    textSize = 14f
                    setTextColor(requireContext().getColor(R.color.on_surface_secondary))
                    gravity = Gravity.CENTER
                    setPadding(8.dp(), 24.dp(), 8.dp(), 24.dp())
                }
            )
            binding.gardenAddRoom.visibility = View.GONE
            return
        }
        binding.gardenAddRoom.visibility = View.VISIBLE

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

        if (matchingRooms.isEmpty() && query.isEmpty()) {
            // Plants exist, no rooms yet — not an error state, a nudge.
            container.addView(buildNoRoomsHint())
        }
        matchingRooms.forEach { room ->
            emit(buildRoomCard(room))
        }
        if (unassigned.isNotEmpty() && unassignedMatches) {
            val unassignedSummary = RoomRepository.RoomSummary(
                room = RoomEntity(
                    id = UNASSIGNED_ROOM_ID,
                    name = getString(R.string.plant_room_unassigned),
                ),
                plantCount = unassigned.size,
                thumbnails = unassigned.mapNotNull {
                    it.plant.coverPhotoUri ?: it.latestScan?.imagePath
                }.take(3),
            )
            emit(buildRoomCard(unassignedSummary, isUnassigned = true))
        }
    }

    private fun matchesRoomQuery(
        room: RoomRepository.RoomSummary,
        plants: List<PlantRepository.PlantSummary>,
        query: String,
    ): Boolean {
        if (query.isEmpty()) return true
        if (room.room.name.contains(query, ignoreCase = true)) return true
        return plants.any { it.plant.name.contains(query, ignoreCase = true) }
    }

    private fun buildNoRoomsHint(): View = LinearLayout(requireContext()).apply {
        orientation = LinearLayout.VERTICAL
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = 12.dp() }
        addView(
            TextView(requireContext()).apply {
                text = getString(R.string.room_why_hint)
                textSize = 13f
                setTextColor(requireContext().getColor(R.color.on_surface_secondary))
                setLineSpacing(3.dp().toFloat(), 1f)
                setPadding(4.dp(), 0, 4.dp(), 8.dp())
            }
        )
    }

    private fun buildRoomCard(
        summary: RoomRepository.RoomSummary,
        isUnassigned: Boolean = false,
    ): View {
        val row = ItemRoomCardBinding.inflate(layoutInflater, binding.gardenRoomsContainer, false)
        row.roomName.text = summary.room.name
        row.roomPlantCount.text = resources.getQuantityString(
            R.plurals.room_plants_count,
            summary.plantCount,
            summary.plantCount,
        )
        row.roomIcon.setImageResource(RoomPresets.iconRes(summary.room.icon))

        row.roomThumbRow.removeAllViews()
        if (summary.thumbnails.isEmpty()) {
            row.roomThumbRow.visibility = View.GONE
            row.roomEmptyHintRow.visibility = View.VISIBLE
        } else {
            row.roomThumbRow.visibility = View.VISIBLE
            row.roomEmptyHintRow.visibility = View.GONE
            summary.thumbnails.forEachIndexed { index, source ->
                row.roomThumbRow.addView(buildThumb(source, marginStart = index > 0))
            }
            val overflow = summary.plantCount - summary.thumbnails.size
            if (overflow > 0) {
                row.roomThumbRow.addView(buildOverflowPill(overflow))
            }
        }

        row.root.setOnClickListener {
            Haptics.tick(requireContext())
            startActivity(
                Intent(requireContext(), RoomDetailActivity::class.java)
                    .putExtra(
                        RoomDetailActivity.EXTRA_ROOM_ID,
                        if (isUnassigned) RoomDetailActivity.UNASSIGNED_ID else summary.room.id,
                    )
            )
        }
        if (!isUnassigned) {
            row.root.setOnLongClickListener {
                Haptics.medium(requireContext())
                showRoomContextMenu(row.root, summary.room)
                true
            }
        }
        return row.root
    }

    private fun buildThumb(source: String, marginStart: Boolean): View = ImageView(requireContext()).apply {
        layoutParams = LinearLayout.LayoutParams(56.dp(), 56.dp()).apply {
            if (marginStart) this.marginStart = 8.dp()
        }
        scaleType = ImageView.ScaleType.CENTER_CROP
        background = requireContext().getDrawable(R.drawable.bg_thumb_rounded)
        clipToOutline = true
        Glide.with(this@GardenFragment)
            .load(source)
            .format(DecodeFormat.PREFER_RGB_565)
            .transform(CenterCrop(), RoundedCorners(resources.getDimensionPixelSize(R.dimen.radius_md)))
            .placeholder(R.drawable.ic_garden)
            .error(R.drawable.ic_garden)
            .into(this)
    }

    private fun buildOverflowPill(count: Int): View = TextView(requireContext()).apply {
        text = getString(R.string.room_plus_more, count)
        layoutParams = LinearLayout.LayoutParams(56.dp(), 56.dp()).apply { marginStart = 8.dp() }
        gravity = Gravity.CENTER
        textSize = 13f
        setTextColor(requireContext().getColor(R.color.on_surface_secondary))
        setBackgroundResource(R.drawable.bg_thumb_rounded)
    }

    private fun showRoomContextMenu(anchor: View, room: RoomEntity) {
        PopupMenu(requireContext(), anchor).apply {
            menu.add(0, 1, 0, getString(R.string.room_rename))
            menu.add(0, 2, 1, getString(R.string.room_delete))
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> {
                        requireActivity().showRoomNameDialog(roomRepository, room) { loadGarden(animate = false) }
                        true
                    }
                    2 -> {
                        requireActivity().confirmDeleteRoomDialog(roomRepository, room) { loadGarden(animate = false) }
                        true
                    }
                    else -> false
                }
            }
            show()
        }
    }

    private fun showCreateRoomDialog() {
        requireActivity().showRoomNameDialog(roomRepository, existing = null) { loadGarden(animate = true) }
    }

    // ─── Care-calendar pane ───────────────────────────────────────────────

    private var calendarView: CareCalendarView? = null
    private var calendarMonthCursor: Calendar = Calendar.getInstance()

    private fun loadCalendarPane() {
        ensureCalendarViewAttached()
        loadMonth(calendarMonthCursor)
        loadTodayCard()
        // Also refreshes the Lunar sub-pane if it's the one currently
        // selected — allSummaries may have changed (a plant added/removed)
        // since the last time this pane was shown.
        applyCalendarSubPaneVisibility()
    }

    private fun ensureCalendarViewAttached() {
        if (calendarView != null) return
        val view = CareCalendarView(requireContext()).apply {
            onDaySelected = { epochDay -> openDay(epochDay * CareSchedule.DAY_MS) }
            onMonthChanged = { next ->
                calendarMonthCursor = next
                loadMonth(next)
            }
        }
        binding.gardenCalendarContainer.addView(view)
        calendarView = view
        buildCalendarLegend()
    }

    // ─── Lunar sub-pane ─────────────────────────────────────────────────

    private var lunarView: LunarCalendarView? = null

    private fun ensureLunarViewAttached() {
        if (lunarView != null) return
        val view = LunarCalendarView(requireContext())
        binding.gardenLunarContainer.addView(view)
        lunarView = view
    }

    /** Feeds every plant the user owns into the aggregate view — cheap
     *  enough to just recompute on every reveal: allSummaries is already
     *  in memory (loaded by loadGarden()), classify() is pure, and a
     *  garden's plant count is never large enough for this to matter. */
    private fun refreshLunarAggregate() {
        ensureLunarViewAttached()
        lunarView?.setAggregate(
            allSummaries.map { summary ->
                LunarCalendarView.AggregateEntry(
                    label = summary.plant.name,
                    category = PlantCategoryClassifier.classify(summary.plant.scientificName, summary.plant.commonName),
                )
            },
        )
    }

    private fun buildCalendarLegend() {
        binding.gardenCalendarLegend.removeAllViews()
        // Same soft-tint swatches the Lunar Calendar's own legend uses
        // (bg_lunar_day_bad/neutral/good) — the day cells below now fill
        // with these same drawables, so the key should show what's on the
        // grid instead of the old strong disease_red/warning_amber/healthy_green dots.
        listOf(
            R.drawable.bg_lunar_day_bad to R.string.calendar_legend_overdue,
            R.drawable.bg_lunar_day_neutral to R.string.calendar_legend_pending,
            R.drawable.bg_lunar_day_good to R.string.calendar_legend_done,
        ).forEach { (drawableRes, labelRes) ->
            binding.gardenCalendarLegend.addView(
                LinearLayout(requireContext()).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply {
                        marginStart = 10.dp()
                        marginEnd = 10.dp()
                    }
                    addView(
                        View(requireContext()).apply {
                            layoutParams = LinearLayout.LayoutParams(12.dp(), 12.dp())
                            setBackgroundResource(drawableRes)
                        }
                    )
                    addView(
                        TextView(requireContext()).apply {
                            text = getString(labelRes)
                            textSize = 11f
                            setTextColor(requireContext().getColor(R.color.on_surface_secondary))
                            setPadding(4.dp(), 0, 0, 0)
                        }
                    )
                }
            )
        }
    }

    private fun loadMonth(monthCursor: Calendar) {
        lifecycleScope.launch {
            val monthStart = (monthCursor.clone() as Calendar).apply {
                set(Calendar.DAY_OF_MONTH, 1)
                set(Calendar.HOUR_OF_DAY, 0)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
            val monthEnd = (monthStart.clone() as Calendar).apply { add(Calendar.MONTH, 1) }
            val summary = careRepository.month(monthStart.timeInMillis, monthEnd.timeInMillis, System.currentTimeMillis())
            if (_binding != null) {
                calendarView?.showMonth(monthStart, summary, selectedEpochDay = null)
            }
        }
    }

    private fun loadTodayCard() {
        lifecycleScope.launch {
            val todayStart = Calendar.getInstance().apply {
                set(Calendar.HOUR_OF_DAY, 0)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }.timeInMillis
            val tasks = careRepository.day(todayStart)
            val pendingCount = tasks.count { it.task.status == CareQueue.CareTask.Status.PENDING }
            if (_binding == null) return@launch
            val dateLabel = SimpleDateFormat("d MMMM", Locale.getDefault()).format(Date(todayStart))
            binding.gardenTodayTitle.text = getString(R.string.calendar_today_prefix, dateLabel)
            binding.gardenTodaySubtitle.text = if (pendingCount == 0) {
                getString(R.string.calendar_all_caught_up)
            } else {
                resources.getQuantityString(R.plurals.care_tasks_count, pendingCount, pendingCount)
            }
            binding.gardenTodayCard.setOnClickListener {
                Haptics.tick(requireContext())
                openDay(todayStart)
            }
        }
    }

    private fun openDay(dayStartMs: Long) {
        startActivity(
            Intent(requireContext(), CareDayActivity::class.java)
                .putExtra(CareDayActivity.EXTRA_DAY_START_MS, dayStartMs)
        )
    }

    // ─── Add plant (FAB / empty-state CTA) ───────────────────────────────

    private fun openAddPlantDialog() {
        requireActivity().showAddEditPlantDialog(plantRepository, allRooms, existing = null) {
            if (_binding != null) {
                loadGarden(animate = true)
                stabilizeFloatingAddButton()
            }
        }
    }

    private fun stabilizeFloatingAddButton() {
        val fab = _binding?.gardenAddFab ?: return
        fab.bringToFront()
        fab.requestLayout()
    }

    private companion object {
        const val PANE_ROOMS = 0
        const val PANE_CALENDAR = 1
        const val KEY_PANE = "garden_selected_pane"
        const val SUBPANE_CARE = 0
        const val SUBPANE_LUNAR = 1
        const val KEY_CALENDAR_SUBPANE = "garden_selected_calendar_subpane"
        const val UNASSIGNED_ROOM_ID = -1L
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
