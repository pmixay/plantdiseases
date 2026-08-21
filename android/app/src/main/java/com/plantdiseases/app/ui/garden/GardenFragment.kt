package com.plantdiseases.app.ui.garden

import android.content.Intent
import android.os.Bundle
import android.text.format.DateFormat
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
import com.google.android.material.snackbar.Snackbar
import com.plantdiseases.app.MainActivity
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.RoomEntity
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.databinding.FragmentGardenBinding
import com.plantdiseases.app.databinding.ItemCareTaskBinding
import com.plantdiseases.app.databinding.ItemRoomCardBinding
import com.plantdiseases.app.ui.garden.care.CareCalendarActivity
import com.plantdiseases.app.ui.garden.care.CareTaskActivity
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingAnchorKey
import com.plantdiseases.app.util.CareQueue
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ReminderManager
import com.plantdiseases.app.util.ReminderPresets
import com.plantdiseases.app.util.RoomPresets
import com.plantdiseases.app.util.applyStatusBarTopInset
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject

/**
 * Garden tab. A single surface — the do-today queue over room cards, each
 * holding a plant collection — with no pane switcher. The care month grid
 * is its own screen ([CareCalendarActivity], opened from the button at the
 * top of this one); the lunar calendar is per-plant and lives on the plant
 * detail screen only. The scaffold is ``fragment_garden.xml``; room rows
 * come from ``item_room_card.xml``. See docs/ROADMAP.md "Garden refactor".
 */
@AndroidEntryPoint
class GardenFragment : Fragment() {

    private var _binding: FragmentGardenBinding? = null
    private val binding get() = _binding!!

    private var allSummaries: List<PlantRepository.PlantSummary> = emptyList()
    private var allRooms: List<RoomRepository.RoomSummary> = emptyList()
    private var todayTasks: List<CareRepository.CareTaskView> = emptyList()

    /** True until the first load renders. Gates the entry fade to the
     *  genuine first paint and lets onResume skip the redundant reload that
     *  fires right after onViewCreated's preload. */
    private var isFirstLoad = true

    private var searchVisible = false

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
        binding.gardenEmptyAddRoom.setOnClickListener {
            Haptics.medium(requireContext())
            showCreateRoomDialog()
        }
        binding.gardenSearchToggle.setOnClickListener {
            searchVisible = !searchVisible
            if (!searchVisible) binding.gardenSearch.text = null
            renderGarden(animate = false)
            if (searchVisible) binding.gardenSearch.requestFocus()
        }
        binding.gardenDoTodayCalendar.setOnClickListener {
            Haptics.tick(requireContext())
            openCareCalendar()
        }
        binding.gardenCareCalendarButton.setOnClickListener {
            Haptics.tick(requireContext())
            openCareCalendar()
        }

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

    override fun onDestroyView() {
        (activity as? MainActivity)?.apply {
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_HERO)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_PLANTS)
            unregisterOnboardingAnchor(OnboardingAnchorKey.GARDEN_ADD)
        }
        super.onDestroyView()
        _binding = null
    }

    /** Emptiness is a first-class input here: the rooms section is VISIBLE
     *  by default in XML and nothing else ever hides it, so deleting the
     *  last plant used to leave the previous render's room cards (and
     *  "+ Add room") stacked on top of the empty state. */
    private fun applyContentVisibility(animate: Boolean = false) {
        val hasGardenContent = allSummaries.isNotEmpty() || allRooms.isNotEmpty()
        binding.gardenRoomsPane.visibility = if (hasGardenContent) View.VISIBLE else View.GONE
        binding.gardenDoTodayCard.visibility = if (allSummaries.isNotEmpty()) View.VISIBLE else View.GONE
        renderRoomsPane(animate)
        updateFabVisibility()
    }

    private fun updateFabVisibility() {
        val show = allSummaries.isNotEmpty() || allRooms.isNotEmpty()
        binding.gardenAddFab.visibility = if (show) View.VISIBLE else View.GONE
        if (show) binding.gardenAddFab.extend()
    }

    private fun loadGarden(animate: Boolean = true) {
        lifecycleScope.launch {
            val plantsRequest = async { plantRepository.getPlants() }
            val roomsRequest = async { roomRepository.getRooms() }
            val todayRequest = async { careRepository.today() }
            val plants = plantsRequest.await()
            val rooms = roomsRequest.await()
            val currentTasks = todayRequest.await()
            if (_binding != null) {
                allSummaries = plants
                allRooms = rooms
                todayTasks = currentTasks
                renderGarden(animate = animate && isFirstLoad)
                isFirstLoad = false
            }
        }
    }

    /** Screen chrome (empty state / FAB / summary / search visibility)
     *  follows the FULL garden; the rooms pane itself follows the filter. */
    private fun renderGarden(animate: Boolean) {
        val hasAnyPlant = allSummaries.isNotEmpty()
        val hasGardenContent = hasAnyPlant || allRooms.isNotEmpty()
        binding.gardenEmptyState.visibility = if (hasGardenContent) View.GONE else View.VISIBLE
        binding.gardenCareCalendarButton.visibility = if (hasGardenContent) View.VISIBLE else View.GONE
        updateFabVisibility()

        val scanTotal = allSummaries.sumOf { it.scanCount }
        binding.gardenSummaryBody.text = if (hasGardenContent) {
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

        binding.gardenSearchLayout.visibility = if (searchVisible) View.VISIBLE else View.GONE

        // Re-evaluated on every load — the rooms section's own visibility
        // depends on whether any plant is left.
        applyContentVisibility(animate)
        renderDoToday()
        registerOnboardingAnchors(!hasGardenContent)
    }

    /** The filter is subordinate to the visible control. `searchVisible` is
     *  not persisted, but `EditText.getFreezesText()` is unconditionally
     *  true, so after a recreate (theme/language change, process death) the
     *  restored query text would otherwise keep filtering the rooms pane
     *  with no search box in sight. */
    private fun currentQuery(): String =
        if (searchVisible) binding.gardenSearch.text?.toString()?.trim().orEmpty() else ""

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

    // ─── Do today ─────────────────────────────────────────────────────

    private fun renderDoToday() {
        val container = binding.gardenDoTodayList
        container.removeAllViews()
        val nowMs = System.currentTimeMillis()
        val todayStart = CareQueue.dayStartMs(CareQueue.epochDayOf(nowMs))
        val overdueCount = todayTasks.count { it.task.dueAt < nowMs }
        val taskCount = resources.getQuantityString(
            R.plurals.care_tasks_count,
            todayTasks.size,
            todayTasks.size,
        )
        binding.gardenDoTodaySubtitle.text = when {
            todayTasks.isEmpty() -> getString(R.string.calendar_all_caught_up)
            overdueCount == 0 -> taskCount
            else -> getString(
                R.string.garden_do_today_summary,
                taskCount,
                resources.getQuantityString(
                    R.plurals.garden_do_today_overdue_count,
                    overdueCount,
                    overdueCount,
                ),
            )
        }
        todayTasks.forEach { task ->
            container.addView(buildTodayRow(task, todayStart))
        }
    }

    private fun buildTodayRow(
        view: CareRepository.CareTaskView,
        todayStart: Long,
    ): View {
        val row = ItemCareTaskBinding.inflate(layoutInflater, binding.gardenDoTodayList, false)
        val isFromEarlierDay = view.task.dueAt < todayStart
        row.careTaskTime.text = if (isFromEarlierDay) {
            SimpleDateFormat("d MMM", Locale.getDefault()).format(Date(view.task.dueAt))
        } else {
            DateFormat.getTimeFormat(requireContext()).format(Date(view.task.dueAt))
        }
        if (isFromEarlierDay) {
            row.careTaskTime.setTextColor(requireContext().getColor(R.color.disease_red))
        }
        row.careTaskIcon.setImageResource(
            if (view.task.type == "treatment") {
                R.drawable.ic_treatment
            } else {
                ReminderPresets.iconRes(null, view.task.type)
            },
        )
        row.careTaskTitle.text = when {
            view.task.type == "treatment" -> view.task.title
            view.task.title == view.task.type ->
                getString(ReminderPresets.fallbackLabelRes(view.task.type))
            else -> view.task.title
        }
        row.careTaskSubtitle.text = view.roomName?.let { "${view.plantName} · $it" } ?: view.plantName
        row.careTaskCheckbox.isChecked = false
        row.careTaskCheckbox.contentDescription = getString(R.string.task_mark_done)
        row.careTaskCheckbox.setOnClickListener {
            row.careTaskCheckbox.isEnabled = false
            Haptics.medium(requireContext())
            completeTodayTask(view)
        }
        row.root.setOnClickListener {
            Haptics.tick(requireContext())
            openTaskDetail(view)
        }
        return row.root
    }

    private fun completeTodayTask(view: CareRepository.CareTaskView) {
        val appContext = requireContext().applicationContext
        lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            val record = withContext(NonCancellable) {
                val completed = careRepository.complete(view)
                if (completed != CareRepository.CompletionRecord.NoChange) {
                    rescheduleRecurring(appContext, view, afterCare = true)
                }
                completed
            }
            loadGarden(animate = false)
            val currentBinding = _binding ?: return@launch
            Snackbar.make(currentBinding.gardenRoot, R.string.task_mark_done, Snackbar.LENGTH_LONG)
                .setAction(R.string.task_undo) {
                    lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
                        withContext(NonCancellable) {
                            careRepository.undo(record)
                            if (record != CareRepository.CompletionRecord.NoChange) {
                                rescheduleRecurring(appContext, view, afterCare = false)
                            }
                        }
                        loadGarden(animate = false)
                    }
                }
                .show()
        }
    }

    private suspend fun rescheduleRecurring(
        context: android.content.Context,
        view: CareRepository.CareTaskView,
        afterCare: Boolean,
    ) {
        val id = view.task.id as? CareQueue.CareTask.TaskId.Recurring ?: return
        val reminder = careRepository.getReminder(id.reminderId) ?: return
        if (!reminder.enabled) return
        if (afterCare) {
            ReminderManager.rescheduleAfterCare(
                context = context,
                plantId = reminder.plantId,
                plantName = view.plantName,
                type = reminder.type,
                intervalDays = reminder.intervalDays,
                hour = reminder.hour,
                minute = reminder.minute,
                reminderId = reminder.id,
            )
        } else {
            ReminderManager.restoreAfterUndo(context, reminder, view.plantName)
        }
    }

    private fun openTaskDetail(view: CareRepository.CareTaskView) {
        val intent = Intent(requireContext(), CareTaskActivity::class.java)
            .putExtra(CareTaskActivity.EXTRA_IS_DONE, false)
        when (val id = view.task.id) {
            is CareQueue.CareTask.TaskId.Step ->
                intent
                    .putExtra(CareTaskActivity.EXTRA_TASK_KIND, "step")
                    .putExtra(CareTaskActivity.EXTRA_STEP_ID, id.stepId)
            is CareQueue.CareTask.TaskId.Recurring ->
                intent
                    .putExtra(CareTaskActivity.EXTRA_TASK_KIND, "recurring")
                    .putExtra(CareTaskActivity.EXTRA_REMINDER_ID, id.reminderId)
                    .putExtra(CareTaskActivity.EXTRA_DUE_AT, view.task.dueAt)
        }
        startActivity(intent)
    }

    // ─── Rooms pane ─────────────────────────────────────────────────────

    private fun renderRoomsPane(animate: Boolean) {
        val container = binding.gardenRoomsContainer
        // Clear BEFORE the empty early-return: leaving the previous render's
        // cards attached also leaves registerOnboardingAnchors anchoring
        // GARDEN_PLANTS onto a stale card.
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

    // ─── Care calendar ────────────────────────────────────────────────

    /** The month grid is its own screen now, not a pane of this one. */
    private fun openCareCalendar() {
        startActivity(CareCalendarActivity.intent(requireContext()))
    }

    // ─── Add plant (FAB / empty-state CTA) ───────────────────────────────

    private fun openAddPlantDialog() {
        requireActivity().showAddEditPlantDialog(plantRepository, roomRepository, allRooms, existing = null) {
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
        const val UNASSIGNED_ROOM_ID = -1L
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
