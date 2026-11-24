package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.widget.ImageView
import android.widget.PopupMenu
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.bumptech.glide.Glide
import com.bumptech.glide.load.DecodeFormat
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.RoomEntity
import com.plantdiseases.app.data.local.gardenChanges
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.databinding.ActivityRoomDetailBinding
import com.plantdiseases.app.databinding.ItemPlantCardBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.PlantHealth
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * One room's plant collection (or, when [EXTRA_ROOM_ID] is
 * [UNASSIGNED_ID], the Unassigned bucket). Reuses item_plant_card.xml —
 * the Garden tab no longer renders plant rows directly, so this screen is
 * their only remaining home; extended with a chevron (Garden refactor
 * phase 6).
 */
@AndroidEntryPoint
class RoomDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityRoomDetailBinding
    private var roomId: Long = UNASSIGNED_ID
    private var currentRoom: RoomEntity? = null

    @Inject
    lateinit var plantRepository: PlantRepository

    @Inject
    lateinit var roomRepository: RoomRepository

    @Inject
    lateinit var database: AppDatabase

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        roomId = intent.getLongExtra(EXTRA_ROOM_ID, UNASSIGNED_ID)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityRoomDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.roomDetailScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.roomDetailAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.roomDetailScroll.updatePadding(bottom = bars.bottom)
            insets
        }

        binding.roomDetailToolbar.setNavigationOnClickListener { finish() }
        binding.roomDetailToolbar.inflateMenu(R.menu.room_detail_menu)
        binding.roomDetailToolbar.setOnMenuItemClickListener { item -> onMenuItem(item) }
        binding.roomDetailEmptyCta.setOnClickListener {
            Haptics.medium(this)
            openAddPlantDialog()
        }
        followDatabase()
    }

    /** The room follows the database, like the garden tab: any committed write
     *  (a plant added, edited or deleted here, an edit or a task done in plant
     *  detail, a reminder action) re-renders it, and coming back re-reads it.
     *  Started once from onCreate, so the first load is the first emission and
     *  there is no onResume load. collectLatest drops a read a newer change
     *  has made stale. */
    private fun followDatabase() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                database.gardenChanges().collectLatest { loadRoom() }
            }
        }
    }

    private fun onMenuItem(item: MenuItem): Boolean = when (item.itemId) {
        R.id.action_add_plant_to_room -> {
            openAddPlantDialog()
            true
        }
        R.id.action_rename_room -> {
            currentRoom?.let { showRoomNameDialog(roomRepository, it) {} }
            true
        }
        R.id.action_delete_room -> {
            currentRoom?.let {
                confirmDeleteRoomDialog(roomRepository, it) {
                    finish()
                }
            }
            true
        }
        else -> false
    }

    private fun openAddPlantDialog() {
        lifecycleScope.launch {
            val rooms = roomRepository.getRoomsForPicker()
            showAddEditPlantDialog(
                plantRepository,
                roomRepository,
                rooms,
                existing = null,
                preselectedRoomId = if (roomId == UNASSIGNED_ID) null else roomId,
            ) {}
        }
    }

    private suspend fun loadRoom() {
        val isUnassigned = roomId == UNASSIGNED_ID
        currentRoom = if (isUnassigned) null else roomRepository.getRoom(roomId)
        if (!isUnassigned && currentRoom == null) {
            // Deleted (here or from elsewhere): nothing left to show.
            finish()
            return
        }
        val allPlants = plantRepository.getPlants()
        val plants = allPlants.filter { it.plant.roomId == (if (isUnassigned) null else roomId) }
        render(plants, isUnassigned)
    }

    private fun render(plants: List<PlantRepository.PlantSummary>, isUnassigned: Boolean) {
        val name = currentRoom?.name ?: getString(R.string.plant_room_unassigned)
        binding.roomDetailTitle.text = name
        binding.roomDetailSubtitle.text =
            resources.getQuantityString(R.plurals.room_plants_count, plants.size, plants.size)

        binding.roomDetailToolbar.menu.findItem(R.id.action_rename_room)?.isVisible = !isUnassigned
        binding.roomDetailToolbar.menu.findItem(R.id.action_delete_room)?.isVisible = !isUnassigned

        binding.roomDetailPlantsContainer.removeAllViews()
        binding.roomDetailEmptyState.visibility = if (plants.isEmpty()) View.VISIBLE else View.GONE
        binding.roomDetailPlantsContainer.visibility = if (plants.isEmpty()) View.GONE else View.VISIBLE
        plants.forEach { binding.roomDetailPlantsContainer.addView(buildPlantRow(it)) }
    }

    private fun buildPlantRow(summary: PlantRepository.PlantSummary): View {
        val plant = summary.plant
        val row = ItemPlantCardBinding.inflate(layoutInflater, binding.roomDetailPlantsContainer, false)

        row.plantThumb.clipToOutline = true
        val thumbSource = plant.coverPhotoUri ?: summary.latestScan?.imagePath
        if (thumbSource != null) {
            row.plantThumb.scaleType = ImageView.ScaleType.CENTER_CROP
            row.plantThumb.setPadding(0, 0, 0, 0)
            row.plantThumb.colorFilter = null
            Glide.with(this)
                .load(thumbSource)
                .format(DecodeFormat.PREFER_RGB_565)
                .transform(CenterCrop(), RoundedCorners(resources.getDimensionPixelSize(R.dimen.radius_md)))
                .placeholder(R.drawable.ic_garden)
                .error(R.drawable.ic_garden)
                .into(row.plantThumb)
        } else {
            row.plantThumb.setImageResource(R.drawable.ic_garden)
            row.plantThumb.setColorFilter(getColor(R.color.primary))
            row.plantThumb.scaleType = ImageView.ScaleType.CENTER_INSIDE
        }
        applyHealthDot(row.plantHealthDot, summary.health.status)

        row.plantName.text = plant.name
        row.plantSubtitle.text = healthSummaryLine(summary.health)
        row.plantSubtitle.setTextColor(getColor(PlantHealthUi.colorRes(summary.health.status)))

        row.plantCard.setOnClickListener {
            Haptics.tick(this)
            startActivity(
                Intent(this, PlantDetailActivity::class.java)
                    .putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plant.id)
            )
        }
        row.plantCard.setOnLongClickListener {
            Haptics.medium(this)
            showPlantContextMenu(row.plantCard, plant)
            true
        }
        return row.root
    }

    private fun applyHealthDot(dot: View, status: PlantHealth.Status) {
        if (status == PlantHealth.Status.UNKNOWN) {
            dot.visibility = View.GONE
            return
        }
        dot.background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(getColor(PlantHealthUi.colorRes(status)))
            setStroke(2.dpPx(), getColor(R.color.surface))
        }
        dot.visibility = View.VISIBLE
    }

    /** Short "label, N%" line for the plant row — the room's subtitle
     *  leads with health instead of repeating the plant's own name. Without
     *  a score (too few recent scans) it falls back to just the label
     *  ("No scans yet", "Too few recent scans", "Treatment") rather than a
     *  blank line. */
    private fun healthSummaryLine(health: PlantHealth.Verdict): String {
        val label = getString(PlantHealthUi.labelRes(health))
        return health.score?.let { "$label, $it%" } ?: label
    }

    private fun showPlantContextMenu(anchor: View, plant: PlantEntity) {
        PopupMenu(this, anchor).apply {
            menu.add(0, 1, 0, getString(R.string.edit_plant))
            menu.add(0, 2, 1, getString(R.string.delete_plant))
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    1 -> {
                        lifecycleScope.launch {
                            val rooms = roomRepository.getRoomsForPicker()
                            showAddEditPlantDialog(plantRepository, roomRepository, rooms, plant) {}
                        }
                        true
                    }
                    2 -> {
                        confirmDeletePlantDialog(plantRepository, plant) {}
                        true
                    }
                    else -> false
                }
            }
            show()
        }
    }

    private fun Int.dpPx(): Int = (this * resources.displayMetrics.density).toInt()

    companion object {
        const val EXTRA_ROOM_ID = "extra_room_id"
        const val UNASSIGNED_ID = -1L
    }
}
