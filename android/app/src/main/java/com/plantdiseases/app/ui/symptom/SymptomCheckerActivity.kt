package com.plantdiseases.app.ui.symptom

import android.content.Context
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.SymptomDiagnoseRequest
import com.plantdiseases.app.data.model.SymptomResult
import com.plantdiseases.app.databinding.ActivitySymptomCheckerBinding
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch

/**
 * No-photo symptom checker — the fallback shown when the camera/ML pipeline
 * can't get a confident read (blurry shot, ``not_a_plant``, ``unknown``).
 *
 * Presents a short guided questionnaire; the answers (stable question/option
 * IDs that match ``server/symptoms.py``) are scored server-side into a ranked
 * shortlist of likely conditions, each rendered as a care card. Honest about
 * its limits: a sparse/weak result shows a "take a photo for a reliable
 * diagnosis" nudge.
 */
class SymptomCheckerActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySymptomCheckerBinding

    // Selected option per question id. The submit button enables at >= 2.
    private val answers = linkedMapOf<String, String>()

    private data class Option(val id: String, val labelRes: Int)
    private data class Question(val id: String, val titleRes: Int, val options: List<Option>)

    companion object {
        private const val MIN_ANSWERS = 2

        // Question/option IDs are a STABLE CONTRACT with server/symptoms.py —
        // do not rename. Labels are localised string resources.
        private val QUESTIONS = listOf(
            Question(
                "part", R.string.symptom_q_part,
                listOf(
                    Option("leaves", R.string.symptom_opt_part_leaves),
                    Option("stems", R.string.symptom_opt_part_stems),
                    Option("whole", R.string.symptom_opt_part_whole),
                    Option("flowers", R.string.symptom_opt_part_flowers),
                    Option("soil", R.string.symptom_opt_part_soil),
                ),
            ),
            Question(
                "symptom", R.string.symptom_q_symptom,
                listOf(
                    Option("spots", R.string.symptom_opt_symptom_spots),
                    Option("yellowing", R.string.symptom_opt_symptom_yellowing),
                    Option("wilting", R.string.symptom_opt_symptom_wilting),
                    Option("powder", R.string.symptom_opt_symptom_powder),
                    Option("webbing", R.string.symptom_opt_symptom_webbing),
                    Option("sticky", R.string.symptom_opt_symptom_sticky),
                    Option("dry_edges", R.string.symptom_opt_symptom_dry_edges),
                    Option("cottony", R.string.symptom_opt_symptom_cottony),
                    Option("deformed", R.string.symptom_opt_symptom_deformed),
                    Option("holes", R.string.symptom_opt_symptom_holes),
                ),
            ),
            Question(
                "pattern", R.string.symptom_q_pattern,
                listOf(
                    Option("tips_edges", R.string.symptom_opt_pattern_tips_edges),
                    Option("whole_leaf", R.string.symptom_opt_pattern_whole_leaf),
                    Option("underside", R.string.symptom_opt_pattern_underside),
                    Option("new_growth", R.string.symptom_opt_pattern_new_growth),
                    Option("lower_leaves", R.string.symptom_opt_pattern_lower_leaves),
                    Option("window_side", R.string.symptom_opt_pattern_window_side),
                ),
            ),
            Question(
                "moisture", R.string.symptom_q_moisture,
                listOf(
                    Option("overwatered", R.string.symptom_opt_moisture_overwatered),
                    Option("underwatered", R.string.symptom_opt_moisture_underwatered),
                    Option("normal", R.string.symptom_opt_moisture_normal),
                ),
            ),
            Question(
                "pests", R.string.symptom_q_pests,
                listOf(
                    Option("moving_dots", R.string.symptom_opt_pests_moving_dots),
                    Option("white_cotton", R.string.symptom_opt_pests_white_cotton),
                    Option("brown_domes", R.string.symptom_opt_pests_brown_domes),
                    Option("flying", R.string.symptom_opt_pests_flying),
                    Option("none", R.string.symptom_opt_pests_none),
                ),
            ),
        )
    }

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivitySymptomCheckerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.symptomScroll.clipToPadding = false
        ViewCompat.setOnApplyWindowInsetsListener(binding.symptomAppBar) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, bars.top, 0, 0)
            binding.symptomScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.symptomToolbar.apply {
            setContentInsetsAbsolute(0, 0)
            setPadding(16.dp(), 0, 16.dp(), 0)
            setNavigationIconTint(getColor(R.color.toolbar_title))
            setNavigationOnClickListener { finish() }
        }

        buildQuestions()
        binding.symptomSubmit.setOnClickListener { submit() }
    }

    private fun buildQuestions() {
        QUESTIONS.forEach { question ->
            binding.symptomQuestions.addView(buildQuestionCard(question))
        }
    }

    private fun buildQuestionCard(question: Question): View = MaterialCardView(this).apply {
        radius = 18.dp().toFloat()
        cardElevation = 0f
        strokeWidth = 1.dp()
        strokeColor = getColor(R.color.card_stroke)
        setCardBackgroundColor(getColor(R.color.surface))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 10.dp() }
        addView(LinearLayout(this@SymptomCheckerActivity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
            addView(TextView(this@SymptomCheckerActivity).apply {
                text = getString(question.titleRes)
                textSize = 15f
                setTextColor(getColor(R.color.on_surface))
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium", android.graphics.Typeface.NORMAL,
                )
                ViewCompat.setAccessibilityHeading(this, true)
            })
            addView(ChipGroup(this@SymptomCheckerActivity).apply {
                isSingleSelection = true
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 8.dp() }
                question.options.forEach { option ->
                    addView(Chip(this@SymptomCheckerActivity).apply {
                        text = getString(option.labelRes)
                        isCheckable = true
                        isClickable = true
                        setChipBackgroundColorResource(R.color.chip_bg_color)
                        setEnsureMinTouchTargetSize(false)
                        setOnClickListener {
                            // Re-tapping the selected chip clears it (ChipGroup
                            // single-selection leaves it checked otherwise).
                            if (answers[question.id] == option.id && !isChecked) {
                                answers.remove(question.id)
                            } else {
                                answers[question.id] = option.id
                            }
                            updateSubmitState()
                        }
                    })
                }
            })
        })
    }

    private fun updateSubmitState() {
        val enough = answers.size >= MIN_ANSWERS
        binding.symptomSubmit.isEnabled = enough
        binding.symptomHint.visibility = if (enough) View.GONE else View.VISIBLE
    }

    private fun submit() {
        Haptics.tick(this)
        binding.symptomSubmit.isEnabled = false
        binding.symptomProgress.visibility = View.VISIBLE
        val app = application as PlantDiseasesApp
        val locale = LocaleHelper.getSavedLanguage(this)
        lifecycleScope.launch {
            val result = app.scanRepository.diagnoseSymptoms(
                SymptomDiagnoseRequest(answers = LinkedHashMap(answers), locale = locale),
            )
            if (isFinishing || isDestroyed) return@launch
            binding.symptomProgress.visibility = View.GONE
            result.onSuccess { renderResults(it.results, it.lowConfidence) }
                .onFailure {
                    binding.symptomSubmit.isEnabled = true
                    Toast.makeText(
                        this@SymptomCheckerActivity, R.string.symptom_error, Toast.LENGTH_LONG,
                    ).show()
                }
        }
    }

    private fun renderResults(results: List<SymptomResult>, lowConfidence: Boolean) {
        val isRu = LocaleHelper.isRussian(this)
        // Swap the questionnaire out for the results.
        binding.symptomQuestions.visibility = View.GONE
        binding.symptomSubmit.visibility = View.GONE
        binding.symptomHint.visibility = View.GONE
        binding.symptomIntro.visibility = View.GONE

        val container = binding.symptomResults
        container.removeAllViews()
        container.visibility = View.VISIBLE

        container.addView(TextView(this).apply {
            text = getString(R.string.symptom_results_title)
            textSize = 20f
            setTextColor(getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create(
                "sans-serif-medium", android.graphics.Typeface.NORMAL,
            )
            ViewCompat.setAccessibilityHeading(this, true)
        })

        if (lowConfidence) {
            container.addView(buildNoticeCard(getString(R.string.symptom_low_confidence)))
        }

        results.forEach { container.addView(buildResultCard(it, isRu)) }

        container.addView(MaterialButton(this).apply {
            text = getString(R.string.symptom_take_photo)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 16.dp() }
            setOnClickListener {
                // Return to the camera/scan screen. finish() pops back to
                // whatever launched the checker (result screen or camera).
                finish()
            }
        })
        container.addView(MaterialButton(
            this, null,
            com.google.android.material.R.attr.materialButtonOutlinedStyle,
        ).apply {
            text = getString(R.string.symptom_start_over)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
            setOnClickListener { resetToQuestionnaire() }
        })
    }

    private fun resetToQuestionnaire() {
        answers.clear()
        binding.symptomResults.removeAllViews()
        binding.symptomResults.visibility = View.GONE
        binding.symptomQuestions.removeAllViews()
        buildQuestions()
        binding.symptomQuestions.visibility = View.VISIBLE
        binding.symptomIntro.visibility = View.VISIBLE
        binding.symptomSubmit.visibility = View.VISIBLE
        binding.symptomSubmit.isEnabled = false
        binding.symptomHint.visibility = View.VISIBLE
    }

    private fun buildResultCard(result: SymptomResult, isRu: Boolean): View =
        MaterialCardView(this).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = getColor(R.color.card_stroke)
            setCardBackgroundColor(getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
            addView(LinearLayout(this@SymptomCheckerActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(16.dp(), 14.dp(), 16.dp(), 16.dp())

                // Rank caption: "Most likely" for the top hit, else "Also possible".
                addView(TextView(this@SymptomCheckerActivity).apply {
                    text = getString(
                        if (result.rank <= 1) R.string.symptom_match_most_likely
                        else R.string.symptom_match_possible,
                    )
                    textSize = 12f
                    setTextColor(getColor(if (result.rank <= 1) R.color.primary else R.color.on_surface_secondary))
                    typeface = android.graphics.Typeface.create(
                        "sans-serif-medium", android.graphics.Typeface.NORMAL,
                    )
                    isAllCaps = true
                    letterSpacing = 0.05f
                })

                addView(TextView(this@SymptomCheckerActivity).apply {
                    text = if (isRu) result.nameRu else result.nameEn
                    textSize = 18f
                    setTextColor(getColor(R.color.on_surface))
                    typeface = android.graphics.Typeface.create(
                        "sans-serif-medium", android.graphics.Typeface.NORMAL,
                    )
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT,
                    ).apply { topMargin = 2.dp() }
                })

                val desc = if (isRu) result.descriptionRu else result.description
                if (desc.isNotBlank()) {
                    addView(TextView(this@SymptomCheckerActivity).apply {
                        text = desc
                        textSize = 14f
                        setTextColor(getColor(R.color.on_surface_secondary))
                        lineSpacingExtra = 2f
                        layoutParams = LinearLayout.LayoutParams(
                            LinearLayout.LayoutParams.MATCH_PARENT,
                            LinearLayout.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = 6.dp() }
                    })
                }

                val treatment = if (isRu) result.treatmentRu else result.treatment
                if (treatment.isNotEmpty()) {
                    addView(sectionLabel(getString(R.string.symptom_treatment_label)))
                    treatment.forEach { addView(bullet(it)) }
                }
                val prevention = if (isRu) result.preventionRu else result.prevention
                if (prevention.isNotEmpty()) {
                    addView(sectionLabel(getString(R.string.symptom_prevention_label)))
                    prevention.forEach { addView(bullet(it)) }
                }
            })
        }

    private fun buildNoticeCard(message: String): View = MaterialCardView(this).apply {
        radius = 16.dp().toFloat()
        cardElevation = 0f
        strokeWidth = 1.dp()
        strokeColor = getColor(R.color.warning_amber)
        setCardBackgroundColor(getColor(R.color.surface))
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 12.dp() }
        addView(TextView(this@SymptomCheckerActivity).apply {
            text = message
            textSize = 13f
            setTextColor(getColor(R.color.on_surface))
            lineSpacingExtra = 2f
            setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
        })
    }

    private fun sectionLabel(label: String): TextView = TextView(this).apply {
        text = label
        textSize = 13f
        setTextColor(getColor(R.color.primary))
        typeface = android.graphics.Typeface.create(
            "sans-serif-medium", android.graphics.Typeface.NORMAL,
        )
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 10.dp() }
    }

    private fun bullet(text: String): View = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = 4.dp() }
        addView(TextView(this@SymptomCheckerActivity).apply {
            this.text = "•"
            setTextColor(getColor(R.color.on_surface_secondary))
            textSize = 14f
        })
        addView(TextView(this@SymptomCheckerActivity).apply {
            this.text = text
            textSize = 14f
            setTextColor(getColor(R.color.on_surface))
            lineSpacingExtra = 1f
            layoutParams = LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f,
            ).apply { marginStart = 8.dp() }
        })
    }

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics,
    ).toInt()
}
