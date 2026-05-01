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
 * Self-contained green-gradient Care Plan card.
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

    private val gradient: LinearLayout
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

    private var onRetryClick: (() -> Unit)? = null
    private var onApplyToReminders: ((CarePlanResponse) -> Unit)? = null
    private var lastPlan: CarePlanResponse? = null

    init {
        radius = 20.dp().toFloat()
        cardElevation = 0f
        useCompatPadding = false
        setContentPadding(0, 0, 0, 0)

        gradient = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.bg_care_plan_gradient)
            setPadding(20.dp(), 20.dp(), 20.dp(), 18.dp())
        }
        addView(gradient)

        // Header row — leaf icon + title + subtitle
        val header = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        header.addView(FrameLayout(context).apply {
            setBackgroundResource(R.drawable.bg_circle_semi)
            layoutParams = LinearLayout.LayoutParams(44.dp(), 44.dp())
            addView(ImageView(context).apply {
                setImageResource(R.drawable.ic_care)
                setColorFilter(context.getColor(R.color.on_primary))
                layoutParams = FrameLayout.LayoutParams(24.dp(), 24.dp(), Gravity.CENTER)
            })
        })
        val titles = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 12.dp() }
        }
        titleView = TextView(context).apply {
            text = context.getString(R.string.care_plan_title)
            textSize = 18f
            setTextColor(context.getColor(R.color.on_primary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        }
        subtitleView = TextView(context).apply {
            textSize = 12f
            setTextColor(context.getColor(R.color.on_primary_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 2.dp() }
        }
        titles.addView(titleView)
        titles.addView(subtitleView)
        header.addView(titles)
        gradient.addView(header)

        // Status container (loading / error / pills swap inside this)
        statusContainer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 16.dp() }
        }
        gradient.addView(statusContainer)

        // ── Loading state ──
        loadingView = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            visibility = View.GONE
            addView(ProgressBar(context).apply {
                isIndeterminate = true
                indeterminateTintList = android.content.res.ColorStateList.valueOf(
                    context.getColor(R.color.on_primary)
                )
                layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
            })
            addView(TextView(context).apply {
                text = context.getString(R.string.care_plan_loading)
                textSize = 14f
                setTextColor(context.getColor(R.color.on_primary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = 12.dp() }
            })
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
            setTextColor(context.getColor(R.color.on_primary))
            alpha = 0.92f
        }
        retryButton = MaterialButton(
            context, null, com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = context.getString(R.string.care_plan_retry)
            setTextColor(context.getColor(R.color.on_primary))
            strokeColor = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.on_primary)
            )
            iconTint = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.on_primary)
            )
            cornerRadius = 14.dp()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener { onRetryClick?.invoke() }
        }
        (errorView as LinearLayout).addView(errorBody)
        (errorView as LinearLayout).addView(retryButton)
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
        noteContainer.addView(TextView(context).apply {
            text = context.getString(R.string.care_plan_disease_note)
            textSize = 11f
            setTextColor(context.getColor(R.color.on_primary_secondary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            letterSpacing = 0.06f
        })
        noteBody = TextView(context).apply {
            textSize = 13f
            setTextColor(context.getColor(R.color.on_primary))
            setLineSpacing(2.dp().toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        }
        noteContainer.addView(noteBody)
        statusContainer.addView(noteContainer)

        followUpView = TextView(context).apply {
            textSize = 12f
            setTextColor(context.getColor(R.color.on_primary_secondary))
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
        }
        statusContainer.addView(followUpView)

        applyToRemindersBtn = MaterialButton(
            context, null, com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = context.getString(R.string.care_plan_use_for_reminders)
            setTextColor(context.getColor(R.color.on_primary))
            strokeColor = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.on_primary)
            )
            iconTint = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.on_primary)
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
    }

    fun showLoading(speciesHint: String?) {
        subtitleView.text = speciesSubtitle(speciesHint)
        loadingView.visibility = View.VISIBLE
        errorView.visibility = View.GONE
        pillsRow.visibility = View.GONE
        noteContainer.visibility = View.GONE
        followUpView.visibility = View.GONE
        applyToRemindersBtn.visibility = View.GONE
    }

    fun showError(onRetry: () -> Unit) {
        onRetryClick = onRetry
        loadingView.visibility = View.GONE
        pillsRow.visibility = View.GONE
        noteContainer.visibility = View.GONE
        followUpView.visibility = View.GONE
        applyToRemindersBtn.visibility = View.GONE
        errorView.visibility = View.VISIBLE
    }

    fun bind(
        plan: CarePlanResponse,
        speciesDisplay: String?,
        showApplyToReminders: Boolean,
        onApplyToReminders: ((CarePlanResponse) -> Unit)? = null,
    ) {
        lastPlan = plan
        this.onApplyToReminders = onApplyToReminders
        subtitleView.text = speciesSubtitle(speciesDisplay ?: plan.species.takeIf { plan.hasSpecies })

        loadingView.visibility = View.GONE
        errorView.visibility = View.GONE

        // Build pills
        pillsRow.removeAllViews()
        val pillsRowOne = horizontalRow()
        pillsRowOne.addView(pill(
            R.drawable.ic_watering,
            context.getString(R.string.care_plan_watering),
            wateringValueText(plan),
            isFirstInRow = true,
        ))
        pillsRowOne.addView(pill(
            R.drawable.ic_treatment,
            context.getString(R.string.care_plan_fertilising),
            context.resources.getString(R.string.care_plan_fertilising_weeks, plan.fertiliseWeeks),
            isFirstInRow = false,
        ))
        pillsRow.addView(pillsRowOne)

        val pillsRowTwo = horizontalRow().apply {
            (layoutParams as LinearLayout.LayoutParams).topMargin = 10.dp()
        }
        pillsRowTwo.addView(pill(
            R.drawable.ic_light,
            context.getString(R.string.care_plan_light),
            lightTierLabel(plan.lightTier),
            isFirstInRow = true,
        ))
        pillsRowTwo.addView(pill(
            R.drawable.ic_warning,
            context.getString(R.string.care_plan_humidity),
            plan.humidityTarget,
            isFirstInRow = false,
        ))
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
            R.string.care_plan_follow_up, plan.followUpDays
        )
        followUpView.visibility = View.VISIBLE

        applyToRemindersBtn.visibility =
            if (showApplyToReminders && onApplyToReminders != null) View.VISIBLE else View.GONE
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
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply {
                    if (isFirstInRow) marginEnd = 5.dp() else marginStart = 5.dp()
                }
        }
        val labelRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        labelRow.addView(ImageView(context).apply {
            setImageResource(iconRes)
            setColorFilter(context.getColor(R.color.on_primary))
            layoutParams = LinearLayout.LayoutParams(15.dp(), 15.dp())
        })
        labelRow.addView(TextView(context).apply {
            text = label.uppercase()
            textSize = 10f
            setTextColor(context.getColor(R.color.on_primary_secondary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            letterSpacing = 0.08f
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = 6.dp() }
        })
        box.addView(labelRow)
        box.addView(TextView(context).apply {
            text = value.ifBlank { "—" }
            textSize = 15f
            setTextColor(context.getColor(R.color.on_primary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            maxLines = 2
            ellipsize = android.text.TextUtils.TruncateAt.END
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 6.dp() }
        })
        return box
    }

    private fun wateringValueText(plan: CarePlanResponse): String {
        return when {
            plan.wateringDaysIsAsNeeded -> context.getString(R.string.care_plan_watering_as_needed)
            plan.wateringDaysInt != null -> context.resources.getString(
                R.string.care_plan_watering_days, plan.wateringDaysInt
            )
            else -> "—"
        }
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
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics,
    ).toInt()
}
