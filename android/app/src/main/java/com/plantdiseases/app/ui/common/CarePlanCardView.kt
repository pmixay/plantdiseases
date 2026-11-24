package com.plantdiseases.app.ui.common

import android.content.Context
import android.util.AttributeSet
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.core.view.isVisible
import androidx.core.widget.ImageViewCompat
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanCheckpoint
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.TemperatureBand
import com.plantdiseases.app.databinding.CarePlanCardBinding
import com.plantdiseases.app.databinding.CarePlanChecklistItemBinding
import com.plantdiseases.app.databinding.CarePlanChecklistSectionBinding
import com.plantdiseases.app.databinding.CarePlanCheckpointRowBinding
import com.plantdiseases.app.databinding.CarePlanDetailsBinding
import com.plantdiseases.app.databinding.CarePlanMetricChipBinding
import com.plantdiseases.app.databinding.CarePlanPillBinding
import com.plantdiseases.app.databinding.CarePlanRedFlagItemBinding
import com.plantdiseases.app.databinding.CarePlanSectionHeaderBinding
import com.plantdiseases.app.databinding.CarePlanSetupRowBinding

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

    private val inflater = LayoutInflater.from(context)
    private val binding: CarePlanCardBinding

    private val details: CarePlanDetailsBinding
        get() = binding.carePlanDetails

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
        binding = CarePlanCardBinding.inflate(inflater, this, true)
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
        binding.carePlanDetails.root.visibility = if (show) View.VISIBLE else View.GONE
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
        binding.carePlanDetails.root.visibility = View.GONE
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
        binding.carePlanDetails.root.visibility = View.GONE
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
        binding.carePlanDetails.root.visibility = View.GONE
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

    /** Fill the rich v2 care-plan sections of the details panel (care_plan_details.xml)
     *  and show only those the plan has content for.
     *  Returns ``true`` when at least one section had content (i.e.
     *  the toggle should be visible), ``false`` when the plan is
     *  v1-only and nothing extra needs revealing.
     *
     *  Layout order is fixed in the XML, most-actionable-first:
     *    1. Red flags  (warning card, top so it isn't buried)
     *    2. Climate    (lux / humidity / temperature, as three chips)
     *    3. Treatment checklist
     *    4. Sanitation
     *    5. Isolation chip (when required)
     *    6. Placement / soil / NPK / acclimation / watering method
     *    7. Checkpoints (timeline)
     *    8. Season note (italic)
     *
     *  Every section binds (the list is built eagerly) so a section the plan no
     *  longer fills is hidden, not left over from the previous bind. */
    private fun applyV2Details(plan: CarePlanResponse): Boolean = listOf(
        bindRedFlags(plan.redFlags.nonBlankItems()),
        bindClimate(plan),
        bindChecklist(
            details.carePlanTreatment,
            R.string.care_plan_detail_treatment,
            R.drawable.ic_treatment,
            plan.treatment.nonBlankItems(),
        ),
        bindChecklist(
            details.carePlanSanitation,
            R.string.care_plan_detail_sanitation,
            R.drawable.ic_warning,
            plan.sanitation.nonBlankItems(),
        ),
        bindIsolation(plan.isolation),
        bindSetup(buildSetupRows(plan)),
        bindCheckpoints(plan.checkpoints.orEmpty().filter { it.check.isNotBlank() }),
        bindSeasonNote(plan.seasonNote),
    ).any { it }

    private fun List<String>?.nonBlankItems(): List<String> = orEmpty().filter { it.isNotBlank() }

    private fun bindHeader(
        header: CarePlanSectionHeaderBinding,
        @StringRes label: Int,
        @DrawableRes icon: Int,
    ) {
        header.carePlanSectionIcon.setImageResource(icon)
        header.carePlanSectionLabel.setText(label)
    }

    /** One bullet per item, each inflated from [inflateRow] into [container]. */
    private fun fillBullets(
        container: LinearLayout,
        items: List<String>,
        inflateRow: (LinearLayout) -> TextView,
    ) {
        container.removeAllViews()
        items.forEach { item ->
            container.addView(inflateRow(container).apply { text = BULLET_PREFIX + item })
        }
    }

    /** Red-flags warning card: coloured background + alert icon so the user
     *  can't miss it. */
    private fun bindRedFlags(items: List<String>): Boolean {
        details.carePlanRedFlags.isVisible = items.isNotEmpty()
        if (items.isEmpty()) return false
        val header = details.carePlanRedFlagsHeader
        bindHeader(header, R.string.care_plan_detail_red_flags, R.drawable.ic_warning)
        ImageViewCompat.setImageTintList(
            header.carePlanSectionIcon,
            context.getColorStateList(R.color.disease_red),
        )
        fillBullets(details.carePlanRedFlagsItems, items) {
            CarePlanRedFlagItemBinding.inflate(inflater, it, false).root
        }
        return true
    }

    /** Hidden when neither lux, humidity nor temperature are populated. */
    private fun bindClimate(plan: CarePlanResponse): Boolean {
        val lux = plan.lightLuxTarget?.takeIf { it.min > 0 || it.max > 0 }
        val humidity = plan.humidityBand?.takeIf { it.min > 0 || it.max > 0 }
        val shown = listOf(
            bindChip(
                details.carePlanChipLight,
                R.drawable.ic_light,
                R.string.care_plan_light,
                lux?.let { context.resources.getString(R.string.care_plan_lux_range, it.min, it.max) },
            ),
            bindChip(
                details.carePlanChipHumidity,
                R.drawable.ic_warning,
                R.string.care_plan_humidity,
                humidity?.let {
                    context.resources.getString(R.string.care_plan_humidity_range, it.min, it.max)
                },
            ),
            bindChip(
                details.carePlanChipTemperature,
                R.drawable.ic_treatment,
                R.string.care_plan_detail_temperature,
                temperatureText(plan.temperatureC),
            ),
        ).any { it }
        details.carePlanClimate.isVisible = shown
        return shown
    }

    private fun temperatureText(temp: TemperatureBand?): String? {
        val day = temp?.day?.takeIf { it.size == 2 }
        val night = temp?.night?.takeIf { it.size == 2 }
        val parts = buildList {
            day?.let { add(context.resources.getString(R.string.care_plan_temp_day, it[0], it[1])) }
            night?.let { add(context.resources.getString(R.string.care_plan_temp_night, it[0], it[1])) }
        }
        return parts.takeIf { it.isNotEmpty() }?.joinToString(", ")
    }

    /** One climate row: icon, all-caps label and [value]; hidden when [value] is null. */
    private fun bindChip(
        chip: CarePlanMetricChipBinding,
        @DrawableRes icon: Int,
        @StringRes label: Int,
        value: String?,
    ): Boolean {
        chip.root.isVisible = value != null
        if (value == null) return false
        chip.carePlanChipIcon.setImageResource(icon)
        chip.carePlanChipLabel.setText(label)
        chip.carePlanChipValue.text = value
        return true
    }

    /** Header + bullet items. Uses on_surface throughout so it stays readable
     *  on the white / dark base surface, regardless of theme. */
    private fun bindChecklist(
        section: CarePlanChecklistSectionBinding,
        @StringRes label: Int,
        @DrawableRes icon: Int,
        items: List<String>,
    ): Boolean {
        section.root.isVisible = items.isNotEmpty()
        if (items.isEmpty()) return false
        bindHeader(section.carePlanChecklistHeader, label, icon)
        fillBullets(section.carePlanChecklistItems, items) {
            CarePlanChecklistItemBinding.inflate(inflater, it, false).root
        }
        return true
    }

    /** Single-line isolation pill: boolean disease-care signal, shown only when true. */
    private fun bindIsolation(required: Boolean): Boolean {
        details.carePlanIsolation.isVisible = required
        if (required) {
            details.carePlanIsolation.text =
                ISOLATION_PREFIX + context.getString(R.string.care_plan_isolation_required)
        }
        return required
    }

    private data class SetupRow(val labelRes: Int, val value: String)

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
            add(SetupRow(R.string.care_plan_detail_placement, it))
        }
        plan.soilMix?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_soil_mix, it))
        }
        plan.fertiliserNpk?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_npk, it))
        }
        // A short, concrete example of what to actually apply — the NPK ratio
        // alone doesn't tell a beginner what to buy or how strong to mix it.
        if (plan.fertiliseWeeks > 0) {
            add(
                SetupRow(
                    R.string.care_plan_detail_fertilise_howto,
                    context.getString(R.string.care_plan_fertilise_howto_value),
                ),
            )
        }
        plan.wateringMethod?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_watering_method, humanizeWateringMethod(it)))
        }
        plan.acclimation?.takeIf { it.isNotBlank() }?.let {
            add(SetupRow(R.string.care_plan_detail_acclimation, it))
        }
    }

    /** Placement / soil / NPK / acclimation / watering method roll into one
     *  labelled "Setup" section: infrequent reference data, not daily actions. */
    private fun bindSetup(rows: List<SetupRow>): Boolean {
        details.carePlanSetup.isVisible = rows.isNotEmpty()
        if (rows.isEmpty()) return false
        bindHeader(details.carePlanSetupHeader, R.string.care_plan_detail_setup, R.drawable.ic_garden)
        val container = details.carePlanSetupRows
        container.removeAllViews()
        rows.forEach { row ->
            val item = CarePlanSetupRowBinding.inflate(inflater, container, false)
            item.carePlanSetupLabel.setText(row.labelRes)
            item.carePlanSetupValue.text = row.value
            container.addView(item.root)
        }
        return true
    }

    /** Vertical timeline with a green "D + N" day badge per checkpoint. */
    private fun bindCheckpoints(items: List<CarePlanCheckpoint>): Boolean {
        details.carePlanCheckpoints.isVisible = items.isNotEmpty()
        if (items.isEmpty()) return false
        bindHeader(
            details.carePlanCheckpointsHeader,
            R.string.care_plan_detail_checkpoints,
            R.drawable.ic_clock,
        )
        val container = details.carePlanCheckpointRows
        container.removeAllViews()
        items.forEach { cp ->
            val row = CarePlanCheckpointRowBinding.inflate(inflater, container, false)
            row.carePlanCheckpointDay.text =
                context.resources.getString(R.string.care_plan_day_badge, cp.day)
            row.carePlanCheckpointText.text = cp.check
            container.addView(row.root)
        }
        return true
    }

    /** Reference context rather than an action, so it closes the panel. */
    private fun bindSeasonNote(note: String?): Boolean {
        val view = details.carePlanSeasonNote
        if (note.isNullOrBlank()) {
            view.isVisible = false
            return false
        }
        view.text = note
        view.isVisible = true
        return true
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

    private companion object {
        const val BULLET_PREFIX = "•  "
        const val ISOLATION_PREFIX = "⚠  "
    }
}
