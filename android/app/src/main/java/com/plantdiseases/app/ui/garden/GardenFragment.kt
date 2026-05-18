package com.plantdiseases.app.ui.garden

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputType
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
import androidx.core.view.setPadding
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ReminderManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class GardenFragment : Fragment() {

    private lateinit var root: FrameLayout
    private lateinit var content: LinearLayout
    private lateinit var plantsContainer: LinearLayout
    private lateinit var emptyState: LinearLayout
    private lateinit var summaryTitle: TextView
    private lateinit var summaryBody: TextView
    private lateinit var addPlantFab: View

    private val plantRepository: PlantRepository
        get() = (requireActivity().application as PlantDiseasesApp).plantRepository

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        root = FrameLayout(requireContext()).apply {
            setBackgroundColor(requireContext().getColor(R.color.background))
        }

        val scroll = androidx.core.widget.NestedScrollView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            )
            clipToPadding = false
        }

        content = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 16.dp(), 16.dp(), 96.dp())
        }
        scroll.addView(content)
        root.addView(scroll)

        buildHero()
        plantsContainer = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 16.dp() }
        }
        content.addView(plantsContainer)
        buildEmptyState()
        buildFloatingAddButton()

        return root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        loadPlants()
    }

    override fun onResume() {
        super.onResume()
        if (::plantsContainer.isInitialized) loadPlants()
    }

    private fun buildHero() {
        val card = MaterialCardView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
            radius = 24.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 0
        }
        val hero = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp(), 22.dp(), 20.dp(), 20.dp())
            setBackgroundResource(R.drawable.bg_garden_hero)
        }
        val icon = ImageView(requireContext()).apply {
            setImageResource(R.drawable.ic_garden)
            setColorFilter(requireContext().getColor(R.color.on_primary))
            layoutParams = LinearLayout.LayoutParams(38.dp(), 38.dp())
        }
        summaryTitle = TextView(requireContext()).apply {
            text = getString(R.string.garden_title)
            textSize = 28f
            setTextColor(requireContext().getColor(R.color.on_primary))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp() }
        }
        summaryBody = TextView(requireContext()).apply {
            text = getString(R.string.garden_subtitle)
            textSize = 14f
            setTextColor(requireContext().getColor(R.color.on_primary_secondary))
            setLineSpacing(3.dp().toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 6.dp() }
        }
        hero.addView(icon)
        hero.addView(summaryTitle)
        hero.addView(summaryBody)
        card.addView(hero)
        content.addView(card)
    }

    private fun buildEmptyState() {
        emptyState = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(24.dp())
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 30.dp() }
        }
        emptyState.addView(ImageView(requireContext()).apply {
            setImageResource(R.drawable.illustration_empty_garden)
            layoutParams = LinearLayout.LayoutParams(190.dp(), 190.dp())
        })
        emptyState.addView(TextView(requireContext()).apply {
            text = getString(R.string.empty_garden_title)
            textSize = 22f
            gravity = Gravity.CENTER
            setTextColor(requireContext().getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        })
        emptyState.addView(TextView(requireContext()).apply {
            text = getString(R.string.empty_garden_hint)
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(requireContext().getColor(R.color.on_surface_secondary))
            setLineSpacing(4.dp().toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        })
        content.addView(emptyState)
    }

    private fun buildFloatingAddButton() {
        val fab = com.google.android.material.floatingactionbutton.ExtendedFloatingActionButton(requireContext()).apply {
            text = getString(R.string.add_plant)
            setIconResource(R.drawable.ic_garden)
            setTextColor(requireContext().getColor(R.color.on_primary))
            iconTint = android.content.res.ColorStateList.valueOf(
                requireContext().getColor(R.color.on_primary)
            )
            backgroundTintList = android.content.res.ColorStateList.valueOf(
                requireContext().getColor(R.color.primary)
            )
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.END,
            ).apply {
                setMargins(0, 0, 18.dp(), 22.dp())
            }
            setOnClickListener { showPlantDialog(null) }
        }
        addPlantFab = fab
        root.addView(fab)
    }

    private fun loadPlants() {
        lifecycleScope.launch {
            val plants = plantRepository.getPlants()
            renderPlants(plants)
        }
    }

    private fun renderPlants(plants: List<PlantRepository.PlantSummary>) {
        plantsContainer.removeAllViews()
        emptyState.visibility = if (plants.isEmpty()) View.VISIBLE else View.GONE
        plantsContainer.visibility = if (plants.isEmpty()) View.GONE else View.VISIBLE
        summaryBody.text = resources.getQuantityString(
            R.plurals.garden_summary,
            plants.size,
            plants.size,
            plants.sumOf { it.scanCount },
        )

        // Group by room. Named rooms render in alphabetical order;
        // unassigned plants land at the bottom in their own bucket so
        // the user has a visible nudge to assign rooms without the
        // app silently hiding anything. When NO plant has a room set
        // (the common first-launch case) the grouping headers don't
        // render at all and the list looks the same as before.
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
            plantsContainer.addView(view)
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
        // Unassigned bucket always renders when non-empty. The header
        // only appears when there's at least one named room to
        // distinguish from — otherwise it would just say "Unassigned"
        // above every plant, which is noise.
        if (unassigned.isNotEmpty()) {
            if (anyNamed) emit(buildRoomHeader(getString(R.string.plant_room_unassigned)))
            unassigned.forEach { emit(buildPlantCard(it)) }
        }
    }

    /** Sticky-header-style label for a room group. Renders as small
     *  uppercase text with a thin top divider so it reads as a group
     *  separator without dominating the card stack. */
    private fun buildRoomHeader(label: String): View = TextView(requireContext()).apply {
        text = label.uppercase()
        textSize = 11f
        letterSpacing = 0.08f
        setTextColor(requireContext().getColor(R.color.on_surface_secondary))
        typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
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
        val card = MaterialCardView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 12.dp() }
            radius = 18.dp().toFloat()
            cardElevation = 1.dp().toFloat()
            strokeWidth = 1.dp()
            strokeColor = requireContext().getColor(R.color.card_stroke)
            setCardBackgroundColor(requireContext().getColor(R.color.surface))
            isClickable = true
            isLongClickable = true
            foreground = typedSelectable()
            setOnClickListener {
                Haptics.tick(requireContext())
                startActivity(
                    Intent(requireContext(), PlantDetailActivity::class.java)
                        .putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plant.id)
                )
            }
            setOnLongClickListener { anchor ->
                Haptics.medium(requireContext())
                showPlantContextMenu(anchor, plant)
                true
            }
        }

        val row = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(14.dp())
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            )
        }

        // Thumbnail: user-picked cover photo first, latest scan image
        // second, generic leaf icon last. Rounded square (not circle)
        // so an actual plant photo reads as a photo, not as an avatar.
        val thumbWrap = FrameLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(64.dp(), 64.dp())
        }
        val thumb = ImageView(requireContext()).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            )
            scaleType = ImageView.ScaleType.CENTER_CROP
            background = requireContext().getDrawable(R.drawable.bg_icon_circle)
            clipToOutline = true
        }
        val thumbSource: String? = plant.coverPhotoUri
            ?: summary.latestScan?.imagePath
        if (thumbSource != null) {
            Glide.with(this@GardenFragment)
                .load(thumbSource)
                .transform(CenterCrop(), RoundedCorners(14.dp()))
                .placeholder(R.drawable.ic_garden)
                .error(R.drawable.ic_garden)
                .into(thumb)
        } else {
            thumb.setImageResource(R.drawable.ic_garden)
            thumb.setColorFilter(requireContext().getColor(R.color.primary))
            thumb.scaleType = ImageView.ScaleType.CENTER_INSIDE
            thumb.setPadding(16.dp(), 16.dp(), 16.dp(), 16.dp())
        }
        thumbWrap.addView(thumb)

        // Two-line card: title + (health · next-action) row. The
        // species line and the trailing "Today" fallback were both
        // axe-able — species is in the detail screen header, scan
        // count was duplicate of the health chip's existence, and
        // the date fallback was just visual filler when the user
        // hadn't scanned yet. Edit moves to the long-press menu so
        // the right edge stops competing with the title for width.
        val textCol = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 14.dp() }
        }
        textCol.addView(TextView(requireContext()).apply {
            text = plant.name
            textSize = 18f
            setTextColor(requireContext().getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        val healthChip = buildHealthChip(summary.latestScan)
        val nextChip = buildNextActionChip(summary)
        if (healthChip != null || nextChip != null) {
            val statusRow = LinearLayout(requireContext()).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 6.dp() }
            }
            if (healthChip != null) statusRow.addView(healthChip)
            if (nextChip != null) statusRow.addView(nextChip)
            textCol.addView(statusRow)
        } else {
            // No scans + no reminders → one quiet subtitle so the
            // card doesn't read as a single floating word. Lower
            // visual weight than the chips above.
            textCol.addView(TextView(requireContext()).apply {
                text = plant.species?.takeIf { it.isNotBlank() }
                    ?: getString(R.string.no_scans_attached)
                textSize = 12f
                setTextColor(requireContext().getColor(R.color.on_surface_secondary))
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 4.dp() }
            })
        }

        row.addView(thumbWrap)
        row.addView(textCol)
        card.addView(row)
        return card
    }

    /** Returns null when there is no scan yet — the card falls back to a
     *  generic "no scans" line in that case. Otherwise renders a
     *  colour-coded health pill (healthy = green, disease = red, NAP =
     *  neutral) with the latest verdict + confidence. */
    private fun buildHealthChip(scan: com.plantdiseases.app.data.local.ScanEntity?): View? {
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
            "watering" -> "💧"   // 💧
            "fertilize" -> "🌱"  // 🌱
            else -> "⏰"               // ⏰
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
            textSize = 11.5f
            setTextColor(fgColor)
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            setPadding(10.dp(), 4.dp(), 10.dp(), 4.dp())
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.RECTANGLE
                cornerRadius = 999f
                setColor(bgColor)
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
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
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
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
            // Render after IME settles so the bottom FAB is measured
            // against the full garden root, not the resized window.
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
                renderPlants(plantRepository.getPlants())
                stabilizeFloatingAddButton()
            }
        }
    }

    private fun hideKeyboard(anchor: View) {
        root.findFocus()?.clearFocus()
        val inputManager = requireContext()
            .getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        inputManager.hideSoftInputFromWindow(anchor.windowToken, 0)
    }

    private suspend fun awaitKeyboardClosed() {
        repeat(10) {
            val imeVisible = ViewCompat.getRootWindowInsets(root)
                ?.isVisible(WindowInsetsCompat.Type.ime()) == true
            if (!imeVisible) {
                delay(48)
                return
            }
            delay(32)
        }
        delay(96)
    }

    private fun stabilizeFloatingAddButton() {
        if (!::addPlantFab.isInitialized) return
        addPlantFab.translationX = 0f
        addPlantFab.translationY = 0f
        addPlantFab.bringToFront()
        addPlantFab.requestLayout()
        root.post {
            addPlantFab.translationX = 0f
            addPlantFab.translationY = 0f
            addPlantFab.bringToFront()
            addPlantFab.requestLayout()
        }
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

    private fun typedSelectable(): android.graphics.drawable.Drawable? {
        val outValue = TypedValue()
        requireContext().theme.resolveAttribute(android.R.attr.selectableItemBackground, outValue, true)
        return requireContext().getDrawable(outValue.resourceId)
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
