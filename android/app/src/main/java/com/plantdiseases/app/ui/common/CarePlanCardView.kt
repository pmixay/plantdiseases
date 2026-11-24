package com.plantdiseases.app.ui.common

import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.databinding.CarePlanCardBinding
import com.plantdiseases.app.databinding.CarePlanPillBinding

/**
 * Self-contained Care Plan card: a plain card with a summary section and an expandable details section.
 *
 * Renders one of three states — loading, error, or filled — and exposes
 * `bind*` callers from any host. Polished design lives entirely here so
 * Result and Plant Detail screens both look identical.
 */
class CarePlanCardView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : MaterialCardView(context, attrs, defStyle) {

    private val binding: CarePlanCardBinding

    private var onRetryClick: (() -> Unit)? = null
    private var onApplyToReminders: ((CarePlanResponse) -> Unit)? = null
    private var lastPlan: CarePlanResponse? = null
    private var sectionExpanded = false
    private var detailsExpanded = false
    private var hasDetails = false

    /**
     * A plan for a specific diagnosis is a *cure* plan; the species baseline
     * (disease = "healthy") is a *care* plan. Hosts set this before binding.
     */
    var isCurePlan: Boolean = false
        set(value) {
            field = value
            binding.carePlanTitle.setText(if (value) R.string.cure_plan_title else R.string.care_plan_title)
            updateAccordionAccessibility()
        }

    /**
     * Cleared by hosts that want the plan at a glance and nothing more
     * (Species detail): "Show details" disappears and the verbose v2 panel —
     * red flags, setup checklist, checkpoints, season note — is never built.
     * Those belong on a plant you actually own, not on a species page whose
     * job is "what does keeping this alive look like?".
     */
    var detailsEnabled: Boolean = true
        set(value) {
            field = value
            applyDetailsState()
        }

    init {
        radius = resources.getDimension(R.dimen.radius_lg)
        cardElevation = 0f
        strokeWidth = resources.getDimensionPixelSize(R.dimen.glass_stroke_width)
        strokeColor = context.getColor(R.color.card_stroke)
        useCompatPadding = false
        setContentPadding(0, 0, 0, 0)
        setCardBackgroundColor(context.getColor(R.color.surface))

        // care_plan_card.xml stacks two sections on one surface: the summary
        // (pills, key action, follow-up) and the verbose details the user
        // expands. Both use on_surface text on the card background.
        binding = CarePlanCardBinding.inflate(LayoutInflater.from(context), this, true)
        binding.carePlanHeader.setOnClickListener { setSectionExpanded(!sectionExpanded) }
        binding.carePlanRetry.setOnClickListener { onRetryClick?.invoke() }
        binding.carePlanApply.setOnClickListener {
            lastPlan?.let { onApplyToReminders?.invoke(it) }
        }
        binding.carePlanDetailsToggle.setOnClickListener { toggleDetails() }
        setSectionExpanded(expanded = false, animate = false)
    }

    private fun setSectionExpanded(expanded: Boolean, animate: Boolean = true) {
        sectionExpanded = expanded
        ExpandableSectionAnimator.update(
            container = binding.carePlanStack,
            expanded = expanded,
            chevron = binding.carePlanChevron,
            animate = animate,
        ) {
            binding.carePlanStatus.visibility = if (expanded) View.VISIBLE else View.GONE
            applyDetailsState()
        }
        updateAccordionAccessibility()
    }

    /**
     * Single source of truth for the detail panel. The toggle only exists
     * while details are collapsible; the panel itself additionally needs the
     * card's own section open (it is a sibling of the summary section, not a child
     * of the container the accordion hides).
     */
    private fun applyDetailsState() {
        val available = detailsEnabled && hasDetails
        binding.carePlanDetailsToggle.visibility = if (available) View.VISIBLE else View.GONE
        val show = available && sectionExpanded && detailsExpanded
        binding.carePlanDetails.visibility = if (show) View.VISIBLE else View.GONE
    }

    private fun updateAccordionAccessibility() {
        binding.carePlanHeader.contentDescription = context.getString(
            R.string.species_info_toggle_content_description,
            binding.carePlanTitle.text?.toString().orEmpty(),
            context.getString(
                if (sectionExpanded) R.string.care_plan_collapse else R.string.care_plan_expand,
            ),
        )
    }

    private fun toggleDetails() {
        detailsExpanded = !detailsExpanded
        ExpandableSectionAnimator.update(
            container = binding.carePlanStack,
            expanded = detailsExpanded,
        ) {
            applyDetailsState()
        }
        binding.carePlanDetailsToggle.text = context.getString(
            if (detailsExpanded) {
                R.string.care_plan_hide_details
            } else {
                R.string.care_plan_show_details
            },
        )
        binding.carePlanDetailsToggle.setIconResource(
            if (detailsExpanded) {
                R.drawable.ic_chevron_up
            } else {
                R.drawable.ic_chevron_down
            },
        )
    }

    private fun resetDetailsExpansion() {
        detailsExpanded = false
        hasDetails = false
        binding.carePlanDetailsToggle.text = context.getString(R.string.care_plan_show_details)
        binding.carePlanDetailsToggle.setIconResource(R.drawable.ic_chevron_down)
        applyDetailsState()
    }

    fun showLoading(speciesHint: String?) {
        binding.carePlanSubtitle.text = speciesSubtitle(speciesHint)
        resetDetailsExpansion()
        binding.carePlanLoading.visibility = View.VISIBLE
        binding.carePlanError.visibility = View.GONE
        binding.carePlanPills.visibility = View.GONE
        binding.carePlanNote.visibility = View.GONE
        binding.carePlanFollowUp.visibility = View.GONE
        binding.carePlanApply.visibility = View.GONE
        binding.carePlanDetailsToggle.visibility = View.GONE
        binding.carePlanDetails.visibility = View.GONE
    }

    fun showError(onRetry: () -> Unit, message: CharSequence? = null) {
        onRetryClick = onRetry
        resetDetailsExpansion()
        binding.carePlanLoading.visibility = View.GONE
        binding.carePlanPills.visibility = View.GONE
        binding.carePlanNote.visibility = View.GONE
        binding.carePlanFollowUp.visibility = View.GONE
        binding.carePlanApply.visibility = View.GONE
        binding.carePlanDetailsToggle.visibility = View.GONE
        binding.carePlanDetails.visibility = View.GONE
        // Reset error-state text in case a previous showAwaitingScan() call
        // overrode it on this same instance.
        binding.carePlanErrorBody.text = message ?: context.getString(R.string.care_plan_unavailable)
        binding.carePlanRetry.text = context.getString(R.string.care_plan_retry)
        binding.carePlanError.visibility = View.VISIBLE
    }

    /**
     * Empty / awaiting-scan state. Shown on plant-detail screens for plants
     * that haven't been scanned yet — the server can produce a "monitor
     * weekly" generic plan for ``disease=unknown`` but the UX is honest
     * here: without a diagnosis we can't tell the user anything actionable
     * about how to treat the plant. The retry button doubles as a "scan
     * now" CTA via the supplied callback.
     */
    fun showAwaitingScan(speciesHint: String?, onScanNow: () -> Unit) {
        binding.carePlanSubtitle.text = speciesSubtitle(speciesHint)
        resetDetailsExpansion()
        binding.carePlanLoading.visibility = View.GONE
        binding.carePlanPills.visibility = View.GONE
        binding.carePlanNote.visibility = View.GONE
        binding.carePlanFollowUp.visibility = View.GONE
        binding.carePlanApply.visibility = View.GONE
        binding.carePlanDetailsToggle.visibility = View.GONE
        binding.carePlanDetails.visibility = View.GONE
        binding.carePlanError.visibility = View.VISIBLE
        binding.carePlanErrorBody.text = context.getString(R.string.care_plan_awaiting_scan)
        binding.carePlanRetry.text = context.getString(R.string.care_plan_scan_now)
        onRetryClick = onScanNow
    }

    fun bind(
        plan: CarePlanResponse,
        speciesDisplay: String?,
        showApplyToReminders: Boolean,
        onApplyToReminders: ((CarePlanResponse) -> Unit)? = null,
        // True for a locally-computed plan on an offline (Aqua) scan — the
        // subtitle then flags it as a basic offline plan instead of
        // pretending it's the server's species-tailored one.
        offlineBasic: Boolean = false,
    ) {
        lastPlan = plan
        this.onApplyToReminders = onApplyToReminders
        binding.carePlanSubtitle.text = if (offlineBasic) {
            context.getString(R.string.care_plan_subtitle_offline)
        } else {
            speciesSubtitle(speciesDisplay ?: plan.species.takeIf { plan.hasSpecies })
        }

        binding.carePlanLoading.visibility = View.GONE
        binding.carePlanError.visibility = View.GONE

        bindPill(
            binding.carePlanPillWatering,
            R.drawable.ic_watering,
            R.string.care_plan_watering,
            wateringValueText(plan),
        )
        bindPill(
            binding.carePlanPillFertilising,
            R.drawable.ic_treatment,
            R.string.care_plan_fertilising,
            context.resources.getString(R.string.care_plan_fertilising_weeks, plan.fertiliseWeeks),
        )
        bindPill(
            binding.carePlanPillLight,
            R.drawable.ic_light,
            R.string.care_plan_light,
            lightTierLabel(plan.lightTier),
        )
        bindPill(
            binding.carePlanPillHumidity,
            R.drawable.ic_warning,
            R.string.care_plan_humidity,
            plan.humidityTarget,
        )
        binding.carePlanPills.visibility = View.VISIBLE

        // Disease note (only if non-empty)
        val note = plan.keyAction.trim()
        if (note.isNotEmpty()) {
            binding.carePlanNoteBody.text = note
            binding.carePlanNote.visibility = View.VISIBLE
        } else {
            binding.carePlanNote.visibility = View.GONE
        }

        binding.carePlanFollowUp.text = context.resources.getString(
            R.string.care_plan_follow_up,
            plan.followUpDays
        )
        binding.carePlanFollowUp.visibility = View.VISIBLE

        binding.carePlanApply.visibility =
            if (showApplyToReminders && onApplyToReminders != null) View.VISIBLE else View.GONE

        // Build v2-field details panel and toggle visibility based on
        // whether any v2 field is populated.
        // Not built at all when details are off — the panel is the expensive
        // half of a bind (a 14-row checklist and a timeline), and a host that
        // will never reveal it should not pay for it.
        val rendered = detailsEnabled && applyV2Details(plan)
        detailsExpanded = false
        hasDetails = rendered
        binding.carePlanDetailsToggle.text = context.getString(R.string.care_plan_show_details)
        binding.carePlanDetailsToggle.setIconResource(R.drawable.ic_chevron_down)
        applyDetailsState()
    }

    /** Render the rich v2 care-plan fields into the details panel.
     *  Returns ``true`` when at least one section had content (i.e.
     *  the toggle should be visible), ``false`` when the plan is
     *  v1-only and nothing extra needs revealing.
     *
     *  Layout order is most-actionable-first:
     *    1. Red flags  (warning card, top so it isn't buried)
     *    2. Climate    (lux / humidity / temperature, as three chips)
     *    3. Treatment checklist
     *    4. Sanitation
     *    5. Isolation chip (when required)
     *    6. Placement / soil / NPK / acclimation / watering method
     *    7. Checkpoints (timeline)
     *    8. Season note (italic) */
    private fun applyV2Details(plan: CarePlanResponse): Boolean {
        binding.carePlanDetails.removeAllViews()
        var any = false

        // 1. RED FLAGS — surfaced first because the whole point of the
        // details panel is "tell me what to watch for". Buried at the
        // bottom (the old layout) made them the hardest-to-find
        // section despite being the most important.
        plan.redFlags?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            binding.carePlanDetails.addView(redFlagsCard(items))
            any = true
        }

        // 2. CLIMATE — lux, humidity, temperature compress into three
        // small pills on a single row. The user wants "what range?"
        // not a verbose label per field.
        val climatePills = climateChipRow(plan)
        if (climatePills != null) {
            binding.carePlanDetails.addView(climatePills)
            any = true
        }

        // 3. TREATMENT CHECKLIST — what to actually do, in order.
        plan.treatment?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            binding.carePlanDetails.addView(
                checklistSection(
                    R.string.care_plan_detail_treatment,
                    R.drawable.ic_treatment,
                    items,
                )
            )
            any = true
        }

        // 4. SANITATION — secondary actions, still a checklist shape.
        plan.sanitation?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            binding.carePlanDetails.addView(
                checklistSection(
                    R.string.care_plan_detail_sanitation,
                    R.drawable.ic_warning,
                    items,
                )
            )
            any = true
        }

        // 5. ISOLATION — boolean → single chip; only show when true.
        if (plan.isolation) {
            binding.carePlanDetails.addView(isolationChip())
            any = true
        }

        // 6. SETUP — placement / soil / NPK / acclimation / watering
        // method roll into one labelled "Setup" subsection because
        // they're all infrequent reference data, not daily actions.
        val setupRows = buildSetupRows(plan)
        if (setupRows.isNotEmpty()) {
            binding.carePlanDetails.addView(setupSection(setupRows))
            any = true
        }

        // 7. CHECKPOINTS — vertical timeline with day badge per item.
        plan.checkpoints?.filter { it.check.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            binding.carePlanDetails.addView(checkpointsTimeline(items))
            any = true
        }

        // 8. SEASON NOTE — italic, dim; this is reference context not
        // an action.
        plan.seasonNote?.takeIf { it.isNotBlank() }?.let {
            binding.carePlanDetails.addView(seasonNote(it))
            any = true
        }

        return any
    }

    /** Red-flags warning card. Coloured background + alert icon so
     *  the user can't miss it. */
    private fun redFlagsCard(items: List<String>): View {
        val outer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.RECTANGLE
                cornerRadius = 14.dp().toFloat()
                setColor(context.getColor(R.color.disease_red_bg))
            }
            setPadding(14.dp(), 12.dp(), 14.dp(), 14.dp())
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp() }
        }
        outer.addView(
            sectionHeader(
                R.string.care_plan_detail_red_flags,
                R.drawable.ic_warning,
                tint = context.getColor(R.color.disease_red),
            )
        )
        items.forEach { item ->
            outer.addView(
                TextView(context).apply {
                    text = "•  $item"
                    textSize = 15f
                    setTextColor(context.getColor(R.color.disease_red))
                    setLineSpacing(4.dp().toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 4.dp() }
                }
            )
        }
        return outer
    }

    /** Returns null when neither lux, humidity nor temperature are
     *  populated — in that case the climate section just doesn't
     *  render. */
    private fun climateChipRow(plan: CarePlanResponse): View? {
        val chips = mutableListOf<View>()
        plan.lightLuxTarget?.takeIf { it.min > 0 || it.max > 0 }?.let { lux ->
            chips.add(
                metricChip(
                    R.drawable.ic_light,
                    context.getString(R.string.care_plan_light).uppercase(),
                    context.resources.getString(R.string.care_plan_lux_range, lux.min, lux.max),
                )
            )
        }
        plan.humidityBand?.takeIf { it.min > 0 || it.max > 0 }?.let { hum ->
            chips.add(
                metricChip(
                    R.drawable.ic_warning,
                    context.getString(R.string.care_plan_humidity).uppercase(),
                    context.resources.getString(R.string.care_plan_humidity_range, hum.min, hum.max),
                )
            )
        }
        plan.temperatureC?.let { temp ->
            val day = temp.day?.takeIf { it.size == 2 }
            val night = temp.night?.takeIf { it.size == 2 }
            val parts = buildList {
                day?.let { add(context.resources.getString(R.string.care_plan_temp_day, it[0], it[1])) }
                night?.let { add(context.resources.getString(R.string.care_plan_temp_night, it[0], it[1])) }
            }
            if (parts.isNotEmpty()) {
                chips.add(
                    metricChip(
                        R.drawable.ic_treatment,
                        context.getString(R.string.care_plan_detail_temperature).uppercase(),
                        parts.joinToString(", "),
                    )
                )
            }
        }
        if (chips.isEmpty()) return null
        val outer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 18.dp() }
        }
        chips.forEach { chip ->
            outer.addView(chip)
            (chip.layoutParams as? LinearLayout.LayoutParams)?.topMargin = 8.dp()
        }
        // First chip has no top-margin (sits flush under the section gap).
        (chips.first().layoutParams as? LinearLayout.LayoutParams)?.topMargin = 0
        return outer
    }

    /** One climate row — icon + tiny all-caps label + value text. */
    private fun metricChip(iconRes: Int, label: String, value: String): View {
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.RECTANGLE
                cornerRadius = 12.dp().toFloat()
                setStroke(1.dp(), context.getColor(R.color.card_stroke))
            }
            setPadding(12.dp(), 10.dp(), 12.dp(), 10.dp())
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        row.addView(
            ImageView(context).apply {
                setImageResource(iconRes)
                setColorFilter(context.getColor(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(18.dp(), 18.dp())
            }
        )
        val text = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 10.dp() }
        }
        text.addView(
            TextView(context).apply {
                this.text = label
                textSize = 12f
                letterSpacing = 0.08f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.DEFAULT_BOLD
            }
        )
        text.addView(
            TextView(context).apply {
                this.text = value
                textSize = 15f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 1.dp() }
            }
        )
        row.addView(text)
        return row
    }

    /** Section with a header row + bullet items. Uses on_surface
     *  colours throughout so it stays readable on the white / dark
     *  base surface, regardless of theme. */
    private fun checklistSection(
        labelRes: Int,
        iconRes: Int,
        items: List<String>,
    ): View {
        val outer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 20.dp() }
        }
        outer.addView(sectionHeader(labelRes, iconRes))
        items.forEach { item ->
            outer.addView(
                TextView(context).apply {
                    text = "•  $item"
                    textSize = 15f
                    setTextColor(context.getColor(R.color.on_surface))
                    setLineSpacing(4.dp().toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 5.dp() }
                }
            )
        }
        return outer
    }

    /** Single-line isolation pill — boolean disease-care signal. */
    private fun isolationChip(): View {
        val chip = TextView(context).apply {
            text = "⚠  " + context.getString(R.string.care_plan_isolation_required)
            textSize = 13f
            setTextColor(context.getColor(R.color.disease_red))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            setPadding(12.dp(), 8.dp(), 12.dp(), 8.dp())
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.RECTANGLE
                cornerRadius = 999f
                setColor(context.getColor(R.color.disease_red_bg))
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp() }
        }
        return chip
    }

    private data class SetupRow(val labelRes: Int, val iconRes: Int, val value: String)

    /** Map the server's watering-method enum to a localized, human-readable
     *  sentence. Unknown/free-text values pass through unchanged. */
    private fun humanizeWateringMethod(raw: String): String {
        val res = when (raw.trim().lowercase()) {
            "bottom_water" -> R.string.watering_method_bottom_water
            "bottom_water_soak" -> R.string.watering_method_bottom_water_soak
            "let_top_inch_dry" -> R.string.watering_method_let_top_inch_dry
            "soak_and_drain" -> R.string.watering_method_soak_and_drain
            else -> return raw
        }
        return context.getString(res)
    }

    private fun buildSetupRows(plan: CarePlanResponse): List<SetupRow> = buildList {
        plan.placement?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_placement, R.drawable.ic_garden, it))
        }
        plan.soilMix?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_soil_mix, R.drawable.ic_garden, it))
        }
        plan.fertiliserNpk?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_npk, R.drawable.ic_treatment, it))
        }
        // A short, concrete example of what to actually apply — the NPK ratio
        // alone doesn't tell a beginner what to buy or how strong to mix it.
        if (plan.fertiliseWeeks > 0) {
            add(
                SetupRow(
                    R.string.care_plan_detail_fertilise_howto,
                    R.drawable.ic_treatment,
                    context.getString(R.string.care_plan_fertilise_howto_value),
                ),
            )
        }
        plan.wateringMethod?.takeIf { it.isNotBlank() }?.let {
            add(
                SetupRow(
                    R.string.care_plan_detail_watering_method,
                    R.drawable.ic_watering,
                    humanizeWateringMethod(it),
                )
            )
        }
        plan.acclimation?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_acclimation, R.drawable.ic_clock, it))
        }
    }

    private fun setupSection(rows: List<SetupRow>): View {
        val outer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 20.dp() }
        }
        outer.addView(
            sectionHeader(
                R.string.care_plan_detail_setup,
                R.drawable.ic_garden,
            )
        )
        rows.forEach { row ->
            outer.addView(setupRow(row))
        }
        return outer
    }

    private fun setupRow(row: SetupRow): View {
        val container = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
        container.addView(
            TextView(context).apply {
                text = context.getString(row.labelRes)
                textSize = 12f
                letterSpacing = 0.06f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            }
        )
        container.addView(
            TextView(context).apply {
                text = row.value
                textSize = 15f
                setTextColor(context.getColor(R.color.on_surface))
                setLineSpacing(3.dp().toFloat(), 1f)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 2.dp() }
            }
        )
        return container
    }

    private fun checkpointsTimeline(
        items: List<com.plantdiseases.app.data.model.CarePlanCheckpoint>,
    ): View {
        val outer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 20.dp() }
        }
        outer.addView(
            sectionHeader(
                R.string.care_plan_detail_checkpoints,
                R.drawable.ic_clock,
            )
        )
        items.forEach { cp ->
            val row = LinearLayout(context).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 8.dp() }
            }
            // Day badge — circular green pill with "D + N".
            row.addView(
                TextView(context).apply {
                    text = context.resources.getString(R.string.care_plan_day_badge, cp.day)
                    textSize = 13f
                    setTextColor(context.getColor(R.color.on_primary))
                    typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                    gravity = Gravity.CENTER
                    setPadding(0, 0, 0, 0)
                    minWidth = 42.dp()
                    background = android.graphics.drawable.GradientDrawable().apply {
                        shape = android.graphics.drawable.GradientDrawable.RECTANGLE
                        cornerRadius = 999f
                        setColor(context.getColor(R.color.primary))
                    }
                    val v = 5.dp()
                    setPadding(8.dp(), v, 8.dp(), v)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    )
                }
            )
            row.addView(
                TextView(context).apply {
                    text = cp.check
                    textSize = 15f
                    setTextColor(context.getColor(R.color.on_surface))
                    setLineSpacing(3.dp().toFloat(), 1f)
                    layoutParams = LinearLayout.LayoutParams(
                        0,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                        1f,
                    ).apply { marginStart = 12.dp() }
                }
            )
            outer.addView(row)
        }
        return outer
    }

    private fun seasonNote(text: String): View = TextView(context).apply {
        this.text = text
        textSize = 12f
        setTextColor(context.getColor(R.color.on_surface))
        setTypeface(typeface, Typeface.ITALIC)
        setLineSpacing(2.dp().toFloat(), 1f)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 16.dp() }
    }

    private fun sectionHeader(
        labelRes: Int,
        iconRes: Int,
        tint: Int = context.getColor(R.color.primary),
    ): View {
        val header = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(
            ImageView(context).apply {
                setImageResource(iconRes)
                setColorFilter(tint)
                layoutParams = LinearLayout.LayoutParams(16.dp(), 16.dp())
            }
        )
        header.addView(
            TextView(context).apply {
                text = context.getString(labelRes).uppercase()
                textSize = 12f
                letterSpacing = 0.08f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.DEFAULT_BOLD
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = 8.dp() }
            }
        )
        return header
    }

    private fun bindPill(
        pill: CarePlanPillBinding,
        @DrawableRes icon: Int,
        @StringRes label: Int,
        value: String,
    ) {
        pill.carePlanPillIcon.setImageResource(icon)
        pill.carePlanPillLabel.setText(label)
        pill.carePlanPillValue.text = value.ifBlank { "-" }
    }

    private fun wateringValueText(plan: CarePlanResponse): String = when {
        plan.wateringDaysIsAsNeeded -> context.getString(R.string.care_plan_watering_as_needed)
        plan.wateringDaysInt != null -> context.resources.getString(
            R.string.care_plan_watering_days,
            plan.wateringDaysInt
        )
        else -> "-"
    }

    private fun lightTierLabel(tier: String): String = when (tier) {
        "low_light" -> context.getString(R.string.care_plan_light_low)
        "medium_light" -> context.getString(R.string.care_plan_light_medium)
        "direct_sun" -> context.getString(R.string.care_plan_light_direct)
        else -> context.getString(R.string.care_plan_light_bright)
    }

    private fun speciesSubtitle(species: String?): String =
        if (species.isNullOrBlank() || species.equals("unknown", ignoreCase = true)) {
            context.getString(R.string.care_plan_subtitle_unknown)
        } else {
            context.getString(R.string.care_plan_subtitle_species, species)
        }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics,
    ).toInt()
}
