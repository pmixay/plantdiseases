package com.plantdiseases.app.ui.garden.detail

import android.content.Intent
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.ui.garden.DoctorChatSheet
import com.plantdiseases.app.ui.garden.PlantDetailActivity
import com.plantdiseases.app.ui.garden.ReminderScheduleSheet
import com.plantdiseases.app.ui.garden.SparklineView
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.ImageUtils.formatRelativeDate
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.localizedSpeciesName
import kotlinx.coroutines.launch

/**
 * The "extra cards" stacked between the header and the tabs (care plan,
 * progression sparkline, adaptive-watering nudge, last-watered strip, guide
 * link) and the secondary surfaces below the tabs (light meter, water
 * calculator, doctor). Each `build*` returns the card (or null to skip it),
 * mirroring how the Activity composed them inline.
 */
class DetailExtraCardsController(
    private val activity: AppCompatActivity,
    private val plantRepository: PlantRepository,
    private val scanRepository: ScanRepository,
    private val content: LinearLayout,
    private val onReload: () -> Unit,
    private val onClearWateringSuggestion: () -> Unit,
) {

    private var plantId: Long = -1L

    fun bindPlantId(plantId: Long) {
        this.plantId = plantId
    }

    /**
     * Care Plan slot. The card itself takes care of loading / error / filled
     * states; we just construct it, place it under the action row, and kick
     * off the network call.
     *
     * When the plant has no scans yet we deliberately skip the network call
     * and surface an "awaiting scan" placeholder. The server would happily
     * return a generic ``disease=unknown`` plan, but a "Monitor weekly"
     * card without any diagnosis context is misleading — it implies the
     * advice is tailored when it isn't. The CTA bounces the user to the
     * Scan tab so they can produce a real verdict before we recommend
     * anything specific.
     */
    fun buildCarePlanSlot(plant: PlantEntity, scans: List<ScanEntity>): View {
        val card = CarePlanCardView(activity).apply {
            // Keyed to the plant's current condition, not the species baseline.
            isCurePlan = true
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }

        val isRu = LocaleHelper.isRussian(activity)
        // Server lookup wants the most precise species (scientific first), raw.
        val speciesHint = plant.scientificName?.takeIf { it.isNotBlank() }
            ?: plant.commonName?.takeIf { it.isNotBlank() }
            ?: plant.species?.takeIf { it.isNotBlank() }
        // Card display: localized — never an English common name for a RU user.
        val speciesDisplay = localizedSpeciesName(
            plant.scientificName,
            plant.commonName ?: plant.species,
            isRu,
        ) ?: speciesHint

        val latestScan = scans.maxByOrNull { it.timestamp }
        if (latestScan == null) {
            card.showAwaitingScan(speciesDisplay) {
                Toast.makeText(
                    activity,
                    R.string.care_plan_awaiting_scan_toast,
                    Toast.LENGTH_SHORT,
                ).show()
                activity.finish()
            }
            return card
        }

        card.showLoading(speciesDisplay)
        val classKey = resolveClassKey(latestScan)

        activity.lifecycleScope.launch {
            val request = CarePlanRequest(
                disease = classKey,
                species = speciesHint,
                light = "bright_indirect",
                locale = if (isRu) "ru" else "en",
            )
            val result = scanRepository.fetchCarePlan(request)
            result.onSuccess { plan ->
                card.bind(
                    plan = plan,
                    speciesDisplay = speciesDisplay,
                    showApplyToReminders = true,
                    onApplyToReminders = { applied ->
                        ReminderScheduleSheet.showWithCarePlanDefaults(
                            activity.supportFragmentManager,
                            plant,
                            applied,
                        )
                    },
                )
            }.onFailure {
                card.showError(onRetry = {
                    val replacement = buildCarePlanSlot(plant, scans)
                    val idx = content.indexOfChild(card)
                    if (idx >= 0) {
                        content.removeViewAt(idx)
                        content.addView(replacement, idx)
                    }
                })
            }
        }
        return card
    }

    /**
     * Best-effort: read the disease class key from the scan's stored top
     * probability (canonical model class name like "powdery_mildew"); fall
     * back to a normalised lowercase of the human-readable name.
     */
    private fun resolveClassKey(scan: ScanEntity): String =
        scanRepository.parseAllProbs(scan.allProbs)
            .entries.maxByOrNull { it.value }?.key
            ?: scan.diseaseName.lowercase().replace(' ', '_')

    /** One-line "Last watered N days ago" strip directly under the
     *  action row. Reads from the plant_reminder_events log filled
     *  by the Done notification action; null when the user has
     *  never tapped Done on a watering reminder for this plant. */
    fun buildLastWateredStrip(lastWateredAt: Long?): View? {
        val ts = lastWateredAt ?: return null
        return TextView(activity).apply {
            text = activity.getString(
                R.string.reminder_streak_last_watered,
                ImageUtils.formatRelativeDate(activity, ts),
            )
            textSize = 12f
            setTextColor(activity.getColor(R.color.on_surface_secondary))
            setPadding(4.dp(), 0, 4.dp(), 0)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
    }

    /** Surfaces a "💡 Tighten the watering schedule from N to M days?"
     *  pill when the user has ≥5 done watering events that show
     *  they're consistently early or late by >1 day. Tap → updates
     *  the reminder + reschedules WorkManager. */
    fun buildAdaptiveWateringChip(
        wateringSuggestion: PlantRepository.WateringSuggestion?,
        currentPlant: PlantEntity?,
    ): View? {
        val s = wateringSuggestion ?: return null
        val plant = currentPlant ?: return null
        val card = MaterialCardView(activity).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = activity.getColor(R.color.primary)
            setCardBackgroundColor(activity.getColor(R.color.surface))
            isClickable = true
            foreground = activity.selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
        }
        val inner = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
        }
        inner.addView(
            TextView(activity).apply {
                text = activity.getString(
                    R.string.adaptive_watering_title,
                    s.currentIntervalDays,
                    s.proposedIntervalDays,
                )
                textSize = 14f
                typeface = android.graphics.Typeface.create(
                    "sans-serif-medium",
                    android.graphics.Typeface.NORMAL,
                )
                setTextColor(activity.getColor(R.color.on_surface))
            },
        )
        inner.addView(
            TextView(activity).apply {
                text = activity.getString(
                    R.string.adaptive_watering_subtitle,
                    s.samples,
                    String.format("%.1f", s.meanGapDays),
                )
                textSize = 12f
                setTextColor(activity.getColor(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 2.dp() }
            },
        )
        val actions = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
        actions.addView(
            com.google.android.material.button.MaterialButton(
                activity,
                null,
                com.google.android.material.R.attr.materialButtonOutlinedStyle,
            ).apply {
                text = activity.getString(R.string.adaptive_watering_apply, s.proposedIntervalDays)
                cornerRadius = 12.dp()
                setOnClickListener {
                    Haptics.tick(activity)
                    activity.lifecycleScope.launch {
                        val existing = plantRepository.getReminders(plant.id)
                            .firstOrNull { it.type == s.type }
                        val updated = (
                            existing ?: com.plantdiseases.app.data.local.PlantReminderEntity(
                                plantId = plant.id,
                                type = s.type,
                            )
                            ).copy(
                            intervalDays = s.proposedIntervalDays,
                            enabled = true,
                        )
                        val rid = plantRepository.saveReminder(updated)
                        com.plantdiseases.app.util.ReminderManager.schedule(
                            context = activity,
                            plantId = plant.id,
                            plantName = plant.name,
                            type = updated.type,
                            intervalDays = updated.intervalDays,
                            hour = updated.hour,
                            minute = updated.minute,
                            reminderId = rid,
                        )
                        Toast.makeText(
                            activity,
                            activity.getString(R.string.adaptive_watering_applied, s.proposedIntervalDays),
                            Toast.LENGTH_SHORT,
                        ).show()
                        onClearWateringSuggestion()
                        onReload()
                    }
                }
            },
        )
        actions.addView(
            com.google.android.material.button.MaterialButton(
                activity,
                null,
                androidx.appcompat.R.attr.borderlessButtonStyle,
            ).apply {
                text = activity.getString(R.string.adaptive_watering_dismiss)
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = 4.dp() }
                setOnClickListener {
                    onClearWateringSuggestion()
                    card.visibility = View.GONE
                }
            },
        )
        inner.addView(actions)
        card.addView(inner)
        return card
    }

    /** Static map from a disease class key (as it appears on
     *  ScanEntity.diseaseName or the resolved classKey) to the
     *  matching id in [com.plantdiseases.app.data.GuideDataProvider].
     *  Returns null when there's no helpful guide article — healthy /
     *  not_a_plant fall here. */
    private fun guideIdForClass(classKey: String): String? = when (classKey) {
        "powdery_mildew" -> "powdery_mildew"
        "gray_mold_botrytis" -> "botrytis"
        "viral_disease" -> "viral_disease"
        // "leaf_spot" is the v2.2 merged class; the other two are legacy
        // keys from pre-merge models / historical scans.
        "leaf_spot", "leaf_spot_fungal", "bacterial_leaf_spot" -> "leaf_spot"
        "nutrient_deficiency" -> "fertilizing"
        "leaf_scorch_underwatering" -> "watering_basics"
        else -> null
    }

    /** "Learn more in the Guide →" link card. Returns null when the
     *  latest scan's class doesn't map to a guide article (healthy,
     *  not_a_plant, or no scan yet). */
    fun buildGuideLink(scans: List<ScanEntity>): View? {
        val latest = scans.maxByOrNull { it.timestamp } ?: return null
        val classKey = resolveClassKey(latest)
        val guideId = guideIdForClass(classKey) ?: return null
        return MaterialCardView(activity).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = activity.getColor(R.color.card_stroke)
            setCardBackgroundColor(activity.getColor(R.color.surface))
            isClickable = true
            foreground = activity.selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener {
                Haptics.tick(activity)
                com.plantdiseases.app.ui.guide.GuideDetailSheet
                    .newInstance(guideId)
                    .show(activity.supportFragmentManager, "guide_detail")
            }
            addView(
                LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
                    addView(
                        FrameLayout(activity).apply {
                            setBackgroundResource(R.drawable.bg_icon_circle)
                            layoutParams = LinearLayout.LayoutParams(42.dp(), 42.dp())
                            addView(
                                ImageView(activity).apply {
                                    setImageResource(R.drawable.ic_care)
                                    setColorFilter(activity.getColor(R.color.primary))
                                    layoutParams = FrameLayout.LayoutParams(23.dp(), 23.dp(), Gravity.CENTER)
                                },
                            )
                        },
                    )
                    addView(
                        LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                                .apply { marginStart = 12.dp() }
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(R.string.guide_link_title)
                                    textSize = 15f
                                    setTextColor(activity.getColor(R.color.on_surface))
                                    typeface = android.graphics.Typeface.create(
                                        "sans-serif-medium",
                                        android.graphics.Typeface.NORMAL,
                                    )
                                },
                            )
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(
                                        R.string.guide_link_subtitle,
                                        // RU UI shows the localised verdict — the EN
                                        // diseaseName leaked through here (same bug as
                                        // the garden card badge).
                                        if (LocaleHelper.isRussian(activity)) {
                                            latest.diseaseNameRu
                                        } else {
                                            latest.diseaseName
                                        },
                                    )
                                    textSize = 12f
                                    setTextColor(activity.getColor(R.color.on_surface_secondary))
                                    maxLines = 2
                                    ellipsize = android.text.TextUtils.TruncateAt.END
                                    layoutParams = LinearLayout.LayoutParams(
                                        LinearLayout.LayoutParams.MATCH_PARENT,
                                        LinearLayout.LayoutParams.WRAP_CONTENT,
                                    ).apply { topMargin = 2.dp() }
                                },
                            )
                        },
                    )
                    addView(
                        ImageView(activity).apply {
                            setImageResource(R.drawable.ic_chevron_right)
                            setColorFilter(activity.getColor(R.color.on_surface_secondary))
                            layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                        },
                    )
                },
            )
        }
    }

    /** Light-meter shortcut. Launches the standalone
     *  [com.plantdiseases.app.ui.camera.LightMeterActivity] (which
     *  already ships under Profile) so the user can verify the actual
     *  lux at the plant's location. */
    fun buildLightMeterEntry(plant: PlantEntity): View =
        MaterialCardView(activity).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = activity.getColor(R.color.card_stroke)
            setCardBackgroundColor(activity.getColor(R.color.surface))
            isClickable = true
            foreground = activity.selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener {
                Haptics.tick(activity)
                // Forward the plant's scientific name so the gauge
                // pre-selects the right species bucket (succulent /
                // tropical / fern / vegetable / general), and the
                // plant id so the session peak gets persisted back
                // onto this plant on close.
                val intent = Intent(
                    activity,
                    com.plantdiseases.app.ui.camera.LightMeterActivity::class.java,
                )
                plant.scientificName?.takeIf { it.isNotBlank() }?.let {
                    intent.putExtra(
                        com.plantdiseases.app.ui.camera.LightMeterActivity.EXTRA_SCIENTIFIC_NAME,
                        it,
                    )
                }
                // Common / free-text name as an auto-detection fallback when
                // the scientific name is absent or its genus isn't mapped.
                (plant.commonName ?: plant.species)?.takeIf { it.isNotBlank() }?.let {
                    intent.putExtra(
                        com.plantdiseases.app.ui.camera.LightMeterActivity.EXTRA_COMMON_NAME,
                        it,
                    )
                }
                intent.putExtra(
                    com.plantdiseases.app.ui.camera.LightMeterActivity.EXTRA_PLANT_ID,
                    plant.id,
                )
                activity.startActivity(intent)
            }
            addView(
                LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
                    addView(
                        FrameLayout(activity).apply {
                            setBackgroundResource(R.drawable.bg_icon_circle)
                            layoutParams = LinearLayout.LayoutParams(42.dp(), 42.dp())
                            addView(
                                ImageView(activity).apply {
                                    setImageResource(R.drawable.ic_light)
                                    setColorFilter(activity.getColor(R.color.primary))
                                    layoutParams = FrameLayout.LayoutParams(23.dp(), 23.dp(), Gravity.CENTER)
                                },
                            )
                        },
                    )
                    addView(
                        LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                                .apply { marginStart = 12.dp() }
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(R.string.light_meter_entry_title)
                                    textSize = 15f
                                    setTextColor(activity.getColor(R.color.on_surface))
                                    typeface = android.graphics.Typeface.create(
                                        "sans-serif-medium",
                                        android.graphics.Typeface.NORMAL,
                                    )
                                },
                            )
                            addView(
                                TextView(activity).apply {
                                    // Surface the previously-recorded lux + when
                                    // it was measured if the user has run the
                                    // Light Meter on this plant before; otherwise
                                    // fall back to the generic invite copy.
                                    val measured = plant.measuredLux
                                    val measuredAt = plant.measuredLuxAt
                                    text = if (measured != null && measured > 0 && measuredAt != null) {
                                        activity.getString(
                                            R.string.light_meter_entry_subtitle_recorded,
                                            measured,
                                            ImageUtils.formatRelativeDate(
                                                activity,
                                                measuredAt,
                                            ),
                                        )
                                    } else {
                                        activity.getString(R.string.light_meter_entry_subtitle)
                                    }
                                    textSize = 12f
                                    setTextColor(activity.getColor(R.color.on_surface_secondary))
                                    maxLines = 2
                                    ellipsize = android.text.TextUtils.TruncateAt.END
                                    layoutParams = LinearLayout.LayoutParams(
                                        LinearLayout.LayoutParams.MATCH_PARENT,
                                        LinearLayout.LayoutParams.WRAP_CONTENT,
                                    ).apply { topMargin = 2.dp() }
                                },
                            )
                        },
                    )
                    addView(
                        ImageView(activity).apply {
                            setImageResource(R.drawable.ic_chevron_right)
                            setColorFilter(activity.getColor(R.color.on_surface_secondary))
                            layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                        },
                    )
                },
            )
        }

    /** Water-calculator shortcut. Launches the standalone
     *  [com.plantdiseases.app.ui.camera.WaterCalculatorActivity], forwarding
     *  the plant's species so the tool pre-selects the right plant-type bucket
     *  (succulent / tropical / fern / vegetable / general). Advisory only —
     *  nothing is persisted back onto the plant. */
    fun buildWaterCalculatorEntry(plant: PlantEntity): View =
        MaterialCardView(activity).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = activity.getColor(R.color.card_stroke)
            setCardBackgroundColor(activity.getColor(R.color.surface))
            isClickable = true
            foreground = activity.selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener {
                Haptics.tick(activity)
                val intent = Intent(
                    activity,
                    com.plantdiseases.app.ui.camera.WaterCalculatorActivity::class.java,
                )
                plant.scientificName?.takeIf { it.isNotBlank() }?.let {
                    intent.putExtra(
                        com.plantdiseases.app.ui.camera.WaterCalculatorActivity.EXTRA_SCIENTIFIC_NAME,
                        it,
                    )
                }
                (plant.commonName ?: plant.species)?.takeIf { it.isNotBlank() }?.let {
                    intent.putExtra(
                        com.plantdiseases.app.ui.camera.WaterCalculatorActivity.EXTRA_COMMON_NAME,
                        it,
                    )
                }
                activity.startActivity(intent)
            }
            addView(
                LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
                    addView(
                        FrameLayout(activity).apply {
                            setBackgroundResource(R.drawable.bg_icon_circle)
                            layoutParams = LinearLayout.LayoutParams(42.dp(), 42.dp())
                            addView(
                                ImageView(activity).apply {
                                    setImageResource(R.drawable.ic_watering)
                                    setColorFilter(activity.getColor(R.color.primary))
                                    layoutParams = FrameLayout.LayoutParams(23.dp(), 23.dp(), Gravity.CENTER)
                                },
                            )
                        },
                    )
                    addView(
                        LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                                .apply { marginStart = 12.dp() }
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(R.string.water_calc_entry_title)
                                    textSize = 15f
                                    setTextColor(activity.getColor(R.color.on_surface))
                                    typeface = android.graphics.Typeface.create(
                                        "sans-serif-medium",
                                        android.graphics.Typeface.NORMAL,
                                    )
                                },
                            )
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(R.string.water_calc_entry_subtitle)
                                    textSize = 12f
                                    setTextColor(activity.getColor(R.color.on_surface_secondary))
                                    maxLines = 2
                                    ellipsize = android.text.TextUtils.TruncateAt.END
                                    layoutParams = LinearLayout.LayoutParams(
                                        LinearLayout.LayoutParams.MATCH_PARENT,
                                        LinearLayout.LayoutParams.WRAP_CONTENT,
                                    ).apply { topMargin = 2.dp() }
                                },
                            )
                        },
                    )
                    addView(
                        ImageView(activity).apply {
                            setImageResource(R.drawable.ic_chevron_right)
                            setColorFilter(activity.getColor(R.color.on_surface_secondary))
                            layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                        },
                    )
                },
            )
        }

    fun buildDoctorEntry(plant: PlantEntity, scans: List<ScanEntity>): View =
        MaterialCardView(activity).apply {
            radius = 18.dp().toFloat()
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = activity.getColor(R.color.card_stroke)
            setCardBackgroundColor(activity.getColor(R.color.surface))
            isClickable = true
            foreground = activity.selectable()
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 10.dp() }
            setOnClickListener {
                Haptics.tick(activity)
                DoctorChatSheet.show(activity.supportFragmentManager, plant)
            }
            addView(
                LinearLayout(activity).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                    setPadding(14.dp(), 12.dp(), 14.dp(), 12.dp())
                    addView(
                        FrameLayout(activity).apply {
                            setBackgroundResource(R.drawable.bg_icon_circle)
                            layoutParams = LinearLayout.LayoutParams(42.dp(), 42.dp())
                            addView(
                                ImageView(activity).apply {
                                    setImageResource(R.drawable.ic_care)
                                    setColorFilter(activity.getColor(R.color.primary))
                                    layoutParams = FrameLayout.LayoutParams(23.dp(), 23.dp(), Gravity.CENTER)
                                },
                            )
                        },
                    )
                    addView(
                        LinearLayout(activity).apply {
                            orientation = LinearLayout.VERTICAL
                            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                                .apply { marginStart = 12.dp() }
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(R.string.doctor_title)
                                    textSize = 15f
                                    setTextColor(activity.getColor(R.color.on_surface))
                                    typeface = android.graphics.Typeface.create(
                                        "sans-serif-medium",
                                        android.graphics.Typeface.NORMAL,
                                    )
                                },
                            )
                            addView(
                                TextView(activity).apply {
                                    text = activity.getString(R.string.doctor_stub_subtitle)
                                    textSize = 12f
                                    setTextColor(activity.getColor(R.color.on_surface_secondary))
                                    maxLines = 1
                                    ellipsize = android.text.TextUtils.TruncateAt.END
                                    layoutParams = LinearLayout.LayoutParams(
                                        LinearLayout.LayoutParams.MATCH_PARENT,
                                        LinearLayout.LayoutParams.WRAP_CONTENT,
                                    ).apply { topMargin = 2.dp() }
                                },
                            )
                        },
                    )
                    addView(
                        ImageView(activity).apply {
                            setImageResource(R.drawable.ic_chevron_right)
                            setColorFilter(activity.getColor(R.color.on_surface_secondary))
                            layoutParams = LinearLayout.LayoutParams(22.dp(), 22.dp())
                        },
                    )
                },
            )
        }

    /**
     * Disease-progression card. Builds a history list from the plant's
     * scan entities (oldest first, capped to the most recent 12 to keep
     * the request body bounded) and asks `/api/progression` for the
     * 5-point severity sparkline.
     *
     * Returns null with fewer than two scans — there is nothing to draw
     * a trend between, and the old "baseline" placeholder rendered as an
     * empty box. The card also hides itself if the fetch fails or the
     * server returns no usable sparkline.
     */
    fun buildProgressionCard(scans: List<ScanEntity>, currentPlant: PlantEntity?): View? {
        if (scans.size < 2) return null
        val card = MaterialCardView(activity).apply {
            radius = activity.resources.getDimension(R.dimen.radius_lg)
            cardElevation = 0f
            strokeWidth = 1.dp()
            strokeColor = activity.getColor(R.color.card_stroke)
            setCardBackgroundColor(activity.getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 12.dp() }
        }
        val body = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(16.dp(), 14.dp(), 16.dp(), 14.dp())
        }
        val title = TextView(activity).apply {
            text = activity.getString(R.string.progression_title)
            textSize = 15f
            setTextColor(activity.getColor(R.color.on_surface))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        }
        val trendChip = TextView(activity).apply {
            textSize = 12f
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            setPadding(10.dp(), 4.dp(), 10.dp(), 4.dp())
            background = activity.getDrawable(R.drawable.bg_plant_stat_pill)
        }
        val titleRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(trendChip)
        }
        body.addView(titleRow)

        val sparkline = SparklineView(activity).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                64.dp(),
            ).apply { topMargin = 10.dp() }
        }
        body.addView(sparkline)

        val meta = TextView(activity).apply {
            textSize = 12f
            setTextColor(activity.getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 8.dp() }
        }
        body.addView(meta)
        card.addView(body)

        // Loading state.
        trendChip.text = activity.getString(R.string.progression_loading)
        trendChip.setTextColor(activity.getColor(R.color.on_surface_secondary))
        meta.text = ""

        // Server expects oldest-first; cap to the 12 newest to keep
        // payloads bounded (Pydantic on the server doesn't, but the
        // device's network stack still has to upload them).
        val ordered = scans.sortedBy { it.timestamp }.takeLast(12)
        val historyPayload = ordered.map { scanRepository.scanToProgressionPayload(it) }
        val newest = ordered.last()
        activity.lifecycleScope.launch {
            val res = scanRepository.fetchProgression(
                ProgressionRequest(history = historyPayload),
            )
            res.onSuccess { resp ->
                // "Usable data" gate: a response with no sparkline points
                // would draw the same empty box the <2-scans path used
                // to — collapse the whole section instead.
                if (resp.sparkline.isNullOrEmpty()) {
                    card.visibility = View.GONE
                    return@onSuccess
                }
                val trendLabel = when (resp.trend) {
                    "improving" -> activity.getString(R.string.progression_trend_improving)
                    "worsening" -> activity.getString(R.string.progression_trend_worsening)
                    "stable" -> activity.getString(R.string.progression_trend_stable)
                    else -> activity.getString(R.string.progression_trend_baseline)
                }
                val trendColor = when (resp.trend) {
                    "improving" -> activity.getColor(R.color.healthy_green)
                    "worsening" -> activity.getColor(R.color.disease_red)
                    "stable" -> activity.getColor(R.color.neutral_blue)
                    else -> activity.getColor(R.color.on_surface_secondary)
                }
                trendChip.text = trendLabel
                trendChip.setTextColor(trendColor)
                sparkline.setData(resp.sparkline, resp.trend)
                meta.text = activity.getString(
                    R.string.progression_history_meta,
                    resp.historyLength ?: ordered.size,
                    formatRelativeDate(activity, newest.timestamp),
                )
                // Per-plant progression alert (C3) — when the server
                // flags two-in-a-row worsening points, push a
                // high-priority notification with a "Take a scan"
                // CTA so the user sees it even if the app isn't open.
                if (resp.alert) {
                    fireProgressionAlert(currentPlant?.name ?: "")
                }
            }.onFailure {
                // No data to show — hide the whole section rather than
                // leaving a header over an empty sparkline box.
                card.visibility = View.GONE
            }
        }
        return card
    }

    /** Notify the user that the plant's severity series is worsening
     *  twice in a row. Reuses the existing plant-reminders
     *  notification channel + intent into the plant detail. */
    private fun fireProgressionAlert(plantName: String) {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU &&
            androidx.core.content.ContextCompat.checkSelfPermission(
                activity,
                android.Manifest.permission.POST_NOTIFICATIONS,
            ) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val notificationId = (plantId * 10 + 9).toInt()
        val contentIntent = android.app.PendingIntent.getActivity(
            activity,
            notificationId,
            Intent(activity, PlantDetailActivity::class.java).apply {
                putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plantId)
            },
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE,
        )
        val title = activity.getString(R.string.progression_alert_title, plantName)
        val body = activity.getString(R.string.progression_alert_body)
        val builder = androidx.core.app.NotificationCompat.Builder(
            activity,
            com.plantdiseases.app.PlantDiseasesApp.CHANNEL_PLANT_REMINDERS,
        )
            .setSmallIcon(R.drawable.ic_warning)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(contentIntent)
            .setPriority(androidx.core.app.NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
        try {
            (activity.getSystemService(android.app.NotificationManager::class.java))
                ?.notify(notificationId, builder.build())
        } catch (_: SecurityException) {
            // permission revoked between check and notify — ignore.
        }
    }

    private fun Int.dp(): Int = dp(activity)
}
