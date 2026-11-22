package com.plantdiseases.app.ui.common

import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.CarePlanResponse

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

    private val summarySection: LinearLayout
    private val verticalStack: LinearLayout
    private val accordionHeader: LinearLayout
    private val accordionChevron: ImageView
    private val titleView: TextView
    private val subtitleView: TextView
    private val statusContainer: LinearLayout
    private val loadingView: View
    private val errorView: View
    private val errorBody: TextView
    private val retryButton: MaterialButton
    private val pillsRow: LinearLayout
    private val noteContainer: LinearLayout
    private val noteBody: TextView
    private val followUpView: TextView
    private val applyToRemindersBtn: MaterialButton
    private val detailsToggle: MaterialButton
    private val detailsContainer: LinearLayout

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
            titleView.setText(if (value) R.string.cure_plan_title else R.string.care_plan_title)
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

        // The card stacks two sections vertically on one surface: the summary
        // (pills, key action, follow-up) and the verbose details the user
        // expands. Both use on_surface text on the card background.
        verticalStack = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LayoutParams(
                LayoutParams.MATCH_PARENT,
                LayoutParams.WRAP_CONTENT,
            )
        }
        addView(verticalStack)

        summarySection = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp(), 20.dp(), 20.dp(), 18.dp())
        }
        verticalStack.addView(summarySection)

        // Header row — leaf icon + title + subtitle
        accordionHeader = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = 48.dp()
            isClickable = true
            isFocusable = true
        }
        accordionHeader.addView(
            FrameLayout(context).apply {
                layoutParams = LinearLayout.LayoutParams(44.dp(), 44.dp())
                addView(
                    ImageView(context).apply {
                        setImageResource(R.drawable.ic_care)
                        setColorFilter(context.getColor(R.color.primary))
                        layoutParams = FrameLayout.LayoutParams(24.dp(), 24.dp(), Gravity.CENTER)
                    }
                )
            }
        )
        val titles = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                0,
                LinearLayout.LayoutParams.WRAP_CONTENT,
                1f,
            ).apply { marginStart = 12.dp() }
        }
        titleView = TextView(context).apply {
            text = context.getString(R.string.care_plan_title)
            textSize = 18f
            setTextColor(context.getColor(R.color.on_surface))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        }
        subtitleView = TextView(context).apply {
            textSize = 12f
            setTextColor(context.getColor(R.color.on_surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 2.dp() }
        }
        titles.addView(titleView)
        titles.addView(subtitleView)
        accordionHeader.addView(titles)
        accordionChevron = ImageView(context).apply {
            setImageResource(R.drawable.ic_chevron_down)
            setColorFilter(context.getColor(R.color.primary))
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            layoutParams = LinearLayout.LayoutParams(20.dp(), 20.dp()).apply {
                marginStart = 8.dp()
            }
        }
        accordionHeader.addView(accordionChevron)
        accordionHeader.setOnClickListener {
            setSectionExpanded(!sectionExpanded)
        }
        summarySection.addView(accordionHeader)

        // Status container (loading / error / pills swap inside this)
        statusContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 16.dp() }
        }
        summarySection.addView(statusContainer)

        // ── Loading state ──
        loadingView = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            visibility = View.GONE
            addView(
                ProgressBar(context).apply {
                    isIndeterminate = true
                    indeterminateTintList = android.content.res.ColorStateList.valueOf(
                        context.getColor(R.color.primary)
                    )
                    layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                }
            )
            addView(
                TextView(context).apply {
                    text = context.getString(R.string.care_plan_loading)
                    textSize = 14f
                    setTextColor(context.getColor(R.color.on_surface))
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { marginStart = 12.dp() }
                }
            )
        }
        statusContainer.addView(loadingView)

        // ── Error state ──
        errorView = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        errorBody = TextView(context).apply {
            text = context.getString(R.string.care_plan_unavailable)
            textSize = 13f
            setTextColor(context.getColor(R.color.on_surface))
        }
        retryButton = MaterialButton(
            context,
            null,
            com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = context.getString(R.string.care_plan_retry)
            setTextColor(context.getColor(R.color.on_surface))
            strokeColor = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.primary)
            )
            iconTint = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.primary)
            )
            cornerRadius = 14.dp()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener { onRetryClick?.invoke() }
        }
        errorView.addView(errorBody)
        errorView.addView(retryButton)
        statusContainer.addView(errorView)

        // ── Filled state: pills + note + follow-up ──
        pillsRow = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
        }
        statusContainer.addView(pillsRow)

        noteContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setBackgroundResource(R.drawable.bg_care_plan_pill)
            setPadding(14.dp(), 10.dp(), 14.dp(), 12.dp())
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }
        noteContainer.addView(
            TextView(context).apply {
                text = context.getString(R.string.care_plan_disease_note)
                textSize = 12f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.DEFAULT_BOLD
                letterSpacing = 0.06f
            }
        )
        noteBody = TextView(context).apply {
            textSize = 14f
            setTextColor(context.getColor(R.color.on_surface))
            setLineSpacing(3.dp().toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        }
        noteContainer.addView(noteBody)
        statusContainer.addView(noteContainer)

        followUpView = TextView(context).apply {
            textSize = 13f
            setTextColor(context.getColor(R.color.on_surface))
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
        }
        statusContainer.addView(followUpView)

        applyToRemindersBtn = MaterialButton(
            context,
            null,
            com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = context.getString(R.string.care_plan_use_for_reminders)
            setTextColor(context.getColor(R.color.on_surface))
            strokeColor = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.primary)
            )
            iconTint = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.primary)
            )
            cornerRadius = 14.dp()
            visibility = View.GONE
            setIconResource(R.drawable.ic_clock)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 14.dp() }
            setOnClickListener {
                lastPlan?.let { onApplyToReminders?.invoke(it) }
            }
        }
        statusContainer.addView(applyToRemindersBtn)

        // The "Show details" toggle stays inside the summary section (it
        // controls the section below). The details container itself
        // lives on the surface-colored section.
        detailsToggle = MaterialButton(
            context,
            null,
            androidx.appcompat.R.attr.borderlessButtonStyle,
        ).apply {
            text = context.getString(R.string.care_plan_show_details)
            setTextColor(context.getColor(R.color.on_surface))
            iconTint = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.primary)
            )
            setIconResource(R.drawable.ic_chevron_down)
            iconGravity = MaterialButton.ICON_GRAVITY_TEXT_END
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = 8.dp()
                marginStart = -12.dp() // pull flush with the card padding
            }
            setOnClickListener { toggleDetails() }
        }
        statusContainer.addView(detailsToggle)

        // Details panel — sits BELOW the summary section on the card's
        // base surface, same contrast as every other card in the app.
        // Each detail row null-guards independently so a sparse plan
        // collapses unused sections instead of rendering empty ones.
        detailsContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(20.dp(), 4.dp(), 20.dp(), 20.dp())
        }
        verticalStack.addView(detailsContainer)
        setSectionExpanded(expanded = false, animate = false)
    }

    private fun setSectionExpanded(expanded: Boolean, animate: Boolean = true) {
        sectionExpanded = expanded
        ExpandableSectionAnimator.update(
            container = verticalStack,
            expanded = expanded,
            chevron = accordionChevron,
            animate = animate,
        ) {
            statusContainer.visibility = if (expanded) View.VISIBLE else View.GONE
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
        detailsToggle.visibility = if (available) View.VISIBLE else View.GONE
        val show = available && sectionExpanded && detailsExpanded
        detailsContainer.visibility = if (show) View.VISIBLE else View.GONE
    }

    private fun updateAccordionAccessibility() {
        accordionHeader.contentDescription = context.getString(
            R.string.species_info_toggle_content_description,
            titleView.text?.toString().orEmpty(),
            context.getString(
                if (sectionExpanded) R.string.care_plan_collapse else R.string.care_plan_expand,
            ),
        )
    }

    private fun toggleDetails() {
        detailsExpanded = !detailsExpanded
        ExpandableSectionAnimator.update(
            container = verticalStack,
            expanded = detailsExpanded,
        ) {
            applyDetailsState()
        }
        detailsToggle.text = context.getString(
            if (detailsExpanded) {
                R.string.care_plan_hide_details
            } else {
                R.string.care_plan_show_details
            },
        )
        detailsToggle.setIconResource(
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
        detailsToggle.text = context.getString(R.string.care_plan_show_details)
        detailsToggle.setIconResource(R.drawable.ic_chevron_down)
        applyDetailsState()
    }

    fun showLoading(speciesHint: String?) {
        subtitleView.text = speciesSubtitle(speciesHint)
        resetDetailsExpansion()
        loadingView.visibility = View.VISIBLE
        errorView.visibility = View.GONE
        pillsRow.visibility = View.GONE
        noteContainer.visibility = View.GONE
        followUpView.visibility = View.GONE
        applyToRemindersBtn.visibility = View.GONE
        detailsToggle.visibility = View.GONE
        detailsContainer.visibility = View.GONE
    }

    fun showError(onRetry: () -> Unit, message: CharSequence? = null) {
        onRetryClick = onRetry
        resetDetailsExpansion()
        loadingView.visibility = View.GONE
        pillsRow.visibility = View.GONE
        noteContainer.visibility = View.GONE
        followUpView.visibility = View.GONE
        applyToRemindersBtn.visibility = View.GONE
        detailsToggle.visibility = View.GONE
        detailsContainer.visibility = View.GONE
        // Reset error-state text in case a previous showAwaitingScan() call
        // overrode it on this same instance.
        errorBody.text = message ?: context.getString(R.string.care_plan_unavailable)
        retryButton.text = context.getString(R.string.care_plan_retry)
        errorView.visibility = View.VISIBLE
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
        subtitleView.text = speciesSubtitle(speciesHint)
        resetDetailsExpansion()
        loadingView.visibility = View.GONE
        pillsRow.visibility = View.GONE
        noteContainer.visibility = View.GONE
        followUpView.visibility = View.GONE
        applyToRemindersBtn.visibility = View.GONE
        detailsToggle.visibility = View.GONE
        detailsContainer.visibility = View.GONE
        errorView.visibility = View.VISIBLE
        errorBody.text = context.getString(R.string.care_plan_awaiting_scan)
        retryButton.text = context.getString(R.string.care_plan_scan_now)
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
        subtitleView.text = if (offlineBasic) {
            context.getString(R.string.care_plan_subtitle_offline)
        } else {
            speciesSubtitle(speciesDisplay ?: plan.species.takeIf { plan.hasSpecies })
        }

        loadingView.visibility = View.GONE
        errorView.visibility = View.GONE

        // Build pills
        pillsRow.removeAllViews()
        val pillsRowOne = horizontalRow()
        pillsRowOne.addView(
            pill(
                R.drawable.ic_watering,
                context.getString(R.string.care_plan_watering),
                wateringValueText(plan),
                isFirstInRow = true,
            )
        )
        pillsRowOne.addView(
            pill(
                R.drawable.ic_treatment,
                context.getString(R.string.care_plan_fertilising),
                context.resources.getString(R.string.care_plan_fertilising_weeks, plan.fertiliseWeeks),
                isFirstInRow = false,
            )
        )
        pillsRow.addView(pillsRowOne)

        val pillsRowTwo = horizontalRow().apply {
            (layoutParams as LinearLayout.LayoutParams).topMargin = 10.dp()
        }
        pillsRowTwo.addView(
            pill(
                R.drawable.ic_light,
                context.getString(R.string.care_plan_light),
                lightTierLabel(plan.lightTier),
                isFirstInRow = true,
            )
        )
        pillsRowTwo.addView(
            pill(
                R.drawable.ic_warning,
                context.getString(R.string.care_plan_humidity),
                plan.humidityTarget,
                isFirstInRow = false,
            )
        )
        pillsRow.addView(pillsRowTwo)
        pillsRow.visibility = View.VISIBLE

        // Disease note (only if non-empty)
        val note = plan.keyAction.trim()
        if (note.isNotEmpty()) {
            noteBody.text = note
            noteContainer.visibility = View.VISIBLE
        } else {
            noteContainer.visibility = View.GONE
        }

        followUpView.text = context.resources.getString(
            R.string.care_plan_follow_up,
            plan.followUpDays
        )
        followUpView.visibility = View.VISIBLE

        applyToRemindersBtn.visibility =
            if (showApplyToReminders && onApplyToReminders != null) View.VISIBLE else View.GONE

        // Build v2-field details panel and toggle visibility based on
        // whether any v2 field is populated.
        // Not built at all when details are off — the panel is the expensive
        // half of a bind (a 14-row checklist and a timeline), and a host that
        // will never reveal it should not pay for it.
        val rendered = detailsEnabled && applyV2Details(plan)
        detailsExpanded = false
        hasDetails = rendered
        detailsToggle.text = context.getString(R.string.care_plan_show_details)
        detailsToggle.setIconResource(R.drawable.ic_chevron_down)
        applyDetailsState()
    }

    /** Render the rich v2 care-plan fields into [detailsContainer].
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
        detailsContainer.removeAllViews()
        var any = false

        // 1. RED FLAGS — surfaced first because the whole point of the
        // details panel is "tell me what to watch for". Buried at the
        // bottom (the old layout) made them the hardest-to-find
        // section despite being the most important.
        plan.redFlags?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            detailsContainer.addView(redFlagsCard(items))
            any = true
        }

        // 2. CLIMATE — lux, humidity, temperature compress into three
        // small pills on a single row. The user wants "what range?"
        // not a verbose label per field.
        val climatePills = climateChipRow(plan)
        if (climatePills != null) {
            detailsContainer.addView(climatePills)
            any = true
        }

        // 3. TREATMENT CHECKLIST — what to actually do, in order.
        plan.treatment?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            detailsContainer.addView(
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
            detailsContainer.addView(
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
            detailsContainer.addView(isolationChip())
            any = true
        }

        // 6. SETUP — placement / soil / NPK / acclimation / watering
        // method roll into one labelled "Setup" subsection because
        // they're all infrequent reference data, not daily actions.
        val setupRows = buildSetupRows(plan)
        if (setupRows.isNotEmpty()) {
            detailsContainer.addView(setupSection(setupRows))
            any = true
        }

        // 7. CHECKPOINTS — vertical timeline with day badge per item.
        plan.checkpoints?.filter { it.check.isNotBlank() }?.takeIf { it.isNotEmpty() }?.let { items ->
            detailsContainer.addView(checkpointsTimeline(items))
            any = true
        }

        // 8. SEASON NOTE — italic, dim; this is reference context not
        // an action.
        plan.seasonNote?.takeIf { it.isNotBlank() }?.let {
            detailsContainer.addView(seasonNote(it))
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

    private fun horizontalRow(): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        )
    }

    private fun pill(
        iconRes: Int,
        label: String,
        value: String,
        isFirstInRow: Boolean,
    ): View {
        val box = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.bg_care_plan_pill)
            setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
            // MATCH_PARENT height: the horizontal row measures to its tallest
            // child, then stretches the sibling to the same height — so a
            // two-line value (e.g. watering "only when soil dries out") no
            // longer leaves its row-mate pill visibly shorter.
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f)
                .apply {
                    if (isFirstInRow) marginEnd = 5.dp() else marginStart = 5.dp()
                }
        }
        val labelRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        labelRow.addView(
            ImageView(context).apply {
                setImageResource(iconRes)
                setColorFilter(context.getColor(R.color.primary))
                layoutParams = LinearLayout.LayoutParams(15.dp(), 15.dp())
            }
        )
        labelRow.addView(
            TextView(context).apply {
                text = label.uppercase()
                textSize = 12f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.DEFAULT_BOLD
                letterSpacing = 0.08f
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = 6.dp() }
            }
        )
        box.addView(labelRow)
        box.addView(
            TextView(context).apply {
                text = value.ifBlank { "-" }
                textSize = 16f
                setTextColor(context.getColor(R.color.on_surface))
                typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                maxLines = 2
                ellipsize = android.text.TextUtils.TruncateAt.END
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 6.dp() }
            }
        )
        return box
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
