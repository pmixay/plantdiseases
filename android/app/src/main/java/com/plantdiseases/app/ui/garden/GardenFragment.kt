package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
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
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.databinding.FragmentGardenBinding
import com.plantdiseases.app.databinding.ItemPlantCardBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ReminderManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Garden tab. The screen scaffold (hero, plant list container, empty
 * state and FAB) lives in ``fragment_garden.xml``; each plant row is
 * inflated from ``item_plant_card.xml``. Only the genuinely dynamic
 * parts — thumbnail loading, the health dot, the status chips and the
 * add/edit dialog — are still built in code.
 */
class GardenFragment : Fragment() {

    private var _binding: FragmentGardenBinding? = null
    private val binding get() = _binding!!

    private val plantRepository: PlantRepository
        get() = (requireActivity().application as PlantDiseasesApp).plantRepository

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
        binding.gardenAddFab.setOnClickListener {
            Haptics.medium(requireContext())
            showPlantDialog(null)
        }
        binding.gardenEmptyCta.setOnClickListener {
            Haptics.medium(requireContext())
            showPlantDialog(null)
        }
        loadPlants()
    }

    override fun onResume() {
        super.onResume()
        if (_binding != null) loadPlants()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    private fun loadPlants() {
        lifecycleScope.launch {
            val plants = plantRepository.getPlants()
            if (_binding != null) renderPlants(plants)
        }
    }

    private fun renderPlants(plants: List<PlantRepository.PlantSummary>) {
        val container = binding.gardenPlantsContainer
        container.removeAllViews()
        binding.gardenEmptyState.visibility = if (plants.isEmpty()) View.VISIBLE else View.GONE
        container.visibility = if (plants.isEmpty()) View.GONE else View.VISIBLE
        binding.gardenSummaryBody.text = resources.getQuantityString(
            R.plurals.garden_summary,
            plants.size,
            plants.size,
            plants.sumOf { it.scanCount },
        )

        // Group by room. Named rooms render alphabetically; unassigned
        // plants land at the bottom in their own bucket so the user has
        // a visible nudge to assign rooms without anything being hidden.
        // When NO plant has a room (the common first-launch case) the
        // grouping headers don't render at all.
        val byRoom = plants.groupBy {
            it.plant.room?.trim()?.takeIf { r -> r.isNotEmpty() }
        }
        val named = byRoom.entries
            .filter { it.key != null }
            .sortedBy { it.key!!.lowercase() }
        val unassigned = byRoom[null].orEmpty()
        val anyNamed = named.isNotEmpty()
        var animIndex = 0
        val emit: (View) -> Unit = { view ->
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
        }
        named.forEach { (room, bucket) ->
            emit(buildRoomHeader(room!!))
            bucket.forEach { emit(buildPlantCard(it)) }
        }
        if (unassigned.isNotEmpty()) {
            if (anyNamed) emit(buildRoomHeader(getString(R.string.plant_room_unassigned)))
            unassigned.forEach { emit(buildPlantCard(it)) }
        }
    }

    /** Sticky-header-style label for a room group: small uppercase
     *  text that reads as a separator without dominating the stack. */
    private fun buildRoomHeader(label: String): View = TextView(requireContext()).apply {
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
                .transform(CenterCrop(), RoundedCorners(18.dp()))
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
        val speciesText = plant.species?.takeIf { it.isNotBlank() }
            ?: plant.commonName?.takeIf { it.isNotBlank() }
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
            healthChip?.let { row.plantStatusRow.addView(it) }
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
        // Pulled slightly outside the thumb so the white halo reads as a
        // separate badge rather than a corner pixel.
        (dot.layoutParams as? FrameLayout.LayoutParams)?.let { lp ->
            lp.topMargin = (-2).dp()
            lp.marginEnd = (-2).dp()
            dot.layoutParams = lp
        }
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
                    scan.diseaseName,
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
        val deltaDays = ((action.dueAt - nowMs) / (24L * 60L * 60L * 1000L)).toInt()
        val icon = when (action.type) {
            "watering" -> "💧"
            "fertilize" -> "🌱"
            else -> "⏰"
        }
        val label = when {
            deltaDays <= 0 -> getString(R.string.garden_card_action_overdue, icon)
            deltaDays == 1 -> getString(R.string.garden_card_action_tomorrow, icon)
            else -> getString(R.string.garden_card_action_in_days, icon, deltaDays)
        }
        val (bg, fg) = if (deltaDays <= 0) {
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
                cornerRadius = 999f
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
                        showPlantDialog(plant); true
                    }
                    2 -> {
                        confirmDeletePlant(plant); true
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
                    ReminderManager.cancelAll(requireContext(), plant.id)
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
        header.addView(FrameLayout(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_circle_semi)
            layoutParams = LinearLayout.LayoutParams(52.dp(), 52.dp())
            addView(ImageView(requireContext()).apply {
                setImageResource(R.drawable.ic_garden)
                setColorFilter(requireContext().getColor(R.color.on_primary))
                layoutParams = FrameLayout.LayoutParams(28.dp(), 28.dp(), Gravity.CENTER)
            })
        })
        header.addView(LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 14.dp() }
            addView(TextView(requireContext()).apply {
                text = getString(if (existing == null) R.string.add_plant else R.string.edit_plant)
                textSize = 20f
                setTextColor(requireContext().getColor(R.color.on_primary))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            })
            addView(TextView(requireContext()).apply {
                text = getString(
                    if (existing == null) R.string.plant_dialog_add_subtitle
                    else R.string.plant_dialog_edit_subtitle
                )
                textSize = 13f
                setTextColor(requireContext().getColor(R.color.on_primary_secondary))
                setLineSpacing(3.dp().toFloat(), 1f)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 3.dp() }
            })
        })

        val fields = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(18.dp(), 18.dp(), 18.dp(), 4.dp())
        }
        val name = inputField(getString(R.string.plant_name), existing?.name, InputType.TYPE_CLASS_TEXT)
        val species = inputField(getString(R.string.plant_species), existing?.species, InputType.TYPE_CLASS_TEXT)
        val room = inputField(getString(R.string.plant_room), existing?.room, InputType.TYPE_CLASS_TEXT)
        fields.addView(name.first)
        fields.addView(species.first)
        fields.addView(room.first)
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
                    room = room.second.text?.toString(),
                )
                awaitKeyboardClosed()
                if (_binding != null) {
                    renderPlants(plantRepository.getPlants())
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

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
