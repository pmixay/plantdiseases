package com.plantdiseases.app.ui.garden

import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.view.setPadding
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import kotlinx.coroutines.launch

class GardenFragment : Fragment() {

    private lateinit var root: FrameLayout
    private lateinit var content: LinearLayout
    private lateinit var plantsContainer: LinearLayout
    private lateinit var emptyState: LinearLayout
    private lateinit var summaryTitle: TextView
    private lateinit var summaryBody: TextView

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

        plants.forEachIndexed { index, summary ->
            val card = buildPlantCard(summary)
            card.alpha = 0f
            card.translationY = 24.dp().toFloat()
            plantsContainer.addView(card)
            card.animate()
                .alpha(1f)
                .translationY(0f)
                .setDuration(260)
                .setStartDelay((index * 45).toLong())
                .setInterpolator(DecelerateInterpolator())
                .start()
        }
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
            foreground = typedSelectable()
            setOnClickListener {
                Haptics.tick(requireContext())
                startActivity(
                    Intent(requireContext(), PlantDetailActivity::class.java)
                        .putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plant.id)
                )
            }
        }

        val row = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(16.dp())
        }
        val iconWrap = FrameLayout(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            layoutParams = LinearLayout.LayoutParams(58.dp(), 58.dp())
        }
        iconWrap.addView(ImageView(requireContext()).apply {
            setImageResource(R.drawable.ic_garden)
            setColorFilter(requireContext().getColor(R.color.primary))
            layoutParams = FrameLayout.LayoutParams(30.dp(), 30.dp(), Gravity.CENTER)
        })

        val textCol = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 14.dp() }
        }
        textCol.addView(TextView(requireContext()).apply {
            text = plant.nickname?.takeIf { it.isNotBlank() } ?: plant.name
            textSize = 18f
            setTextColor(requireContext().getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        textCol.addView(TextView(requireContext()).apply {
            text = buildString {
                append(plant.species?.takeIf { it.isNotBlank() } ?: getString(R.string.plant_species_unknown))
                append("  ")
                append(resources.getQuantityString(R.plurals.scan_count, summary.scanCount, summary.scanCount))
            }
            textSize = 13f
            setTextColor(requireContext().getColor(R.color.on_surface_secondary))
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 3.dp() }
        })
        textCol.addView(TextView(requireContext()).apply {
            text = summary.latestScan?.let {
                getString(R.string.latest_scan_format, ImageUtils.formatRelativeDate(requireContext(), it.timestamp))
            } ?: getString(R.string.no_scans_attached)
            textSize = 12f
            setTextColor(requireContext().getColor(R.color.primary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        })

        val edit = MaterialButton(requireContext(), null, com.google.android.material.R.attr.materialIconButtonStyle).apply {
            setIconResource(R.drawable.ic_care)
            iconTint = android.content.res.ColorStateList.valueOf(requireContext().getColor(R.color.primary))
            contentDescription = getString(R.string.edit_plant)
            layoutParams = LinearLayout.LayoutParams(44.dp(), 44.dp())
            setOnClickListener {
                Haptics.tick(requireContext())
                showPlantDialog(plant)
            }
        }

        row.addView(iconWrap)
        row.addView(textCol)
        row.addView(edit)
        card.addView(row)
        return card
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
        val nickname = inputField(getString(R.string.plant_nickname), existing?.nickname, InputType.TYPE_CLASS_TEXT)
        fields.addView(name.first)
        fields.addView(species.first)
        fields.addView(nickname.first)
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
            lifecycleScope.launch {
                plantRepository.savePlant(
                    plantName,
                    species.second.text?.toString(),
                    nickname.second.text?.toString(),
                    existing,
                )
                dialog.dismiss()
                loadPlants()
            }
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
