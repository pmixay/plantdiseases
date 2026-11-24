package com.plantdiseases.app.ui.garden.detail

import android.content.Intent
import android.view.View
import android.widget.LinearLayout
import android.widget.Toast
import androidx.annotation.DrawableRes
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.local.TreatmentStepEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.repository.AssistantRepository
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.DetailAdaptiveWateringCardBinding
import com.plantdiseases.app.databinding.DetailEntryCardBinding
import com.plantdiseases.app.databinding.DetailHealthCardBinding
import com.plantdiseases.app.databinding.DetailLastWateredStripBinding
import com.plantdiseases.app.databinding.DetailProgressionCardBinding
import com.plantdiseases.app.databinding.DetailTreatmentPlanCardBinding
import com.plantdiseases.app.databinding.DetailTreatmentStepRowBinding
import com.plantdiseases.app.ui.common.CarePlanCardView
import com.plantdiseases.app.ui.common.MeteredAccessUi
import com.plantdiseases.app.ui.garden.DoctorChatSheet
import com.plantdiseases.app.ui.garden.PlantDetailActivity
import com.plantdiseases.app.ui.garden.PlantHealthUi
import com.plantdiseases.app.ui.garden.ReminderScheduleSheet
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.ImageUtils.formatRelativeDate
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.PlantHealth
import com.plantdiseases.app.util.localizedSpeciesName
import java.util.Locale
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

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
    private val assistant: AssistantRepository,
    private val careRepository: CareRepository,
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
            ).apply { topMargin = activity.resources.getDimensionPixelSize(R.dimen.detail_card_gap_lg) }
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

        val acceptedScans = scans.filterNot(scanRepository::isAbstainedScan)
        val latestScan = acceptedScans.maxByOrNull { it.timestamp }
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
            val result = assistant.fetchCarePlan(request)
            result.onSuccess { plan ->
                val materializablePlan = plan.withStoredTreatmentFallback(latestScan, isRu)
                card.bind(
                    plan = materializablePlan,
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
                maybeStartTreatmentPlan(plant, latestScan, classKey, materializablePlan)
            }.onFailure { error ->
                MeteredAccessUi.showDialog(activity, error)
                val fallback = storedTreatmentPlan(latestScan, classKey, isRu)
                if (fallback != null) {
                    card.bind(
                        plan = fallback,
                        speciesDisplay = speciesDisplay,
                        showApplyToReminders = true,
                        onApplyToReminders = { applied ->
                            ReminderScheduleSheet.showWithCarePlanDefaults(
                                activity.supportFragmentManager,
                                plant,
                                applied,
                            )
                        },
                        offlineBasic = true,
                    )
                    maybeStartTreatmentPlan(plant, latestScan, classKey, fallback)
                } else {
                    val limit = if (MeteredAccessUi.handles(error)) {
                        activity.getString(MeteredAccessUi.title(error))
                    } else {
                        null
                    }
                    card.showError(
                        onRetry = {
                            val replacement = buildCarePlanSlot(plant, acceptedScans)
                            val idx = content.indexOfChild(card)
                            if (idx >= 0) {
                                content.removeViewAt(idx)
                                content.addView(replacement, idx)
                            }
                        },
                        message = limit,
                    )
                }
            }
        }
        return card
    }

    /** Prefer the persisted probability-backed class key; only legacy scans
     * need the human-name fallback. */
    private fun resolveClassKey(scan: ScanEntity): String =
        scanRepository.verdictClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')

    private fun CarePlanResponse.withStoredTreatmentFallback(
        scan: ScanEntity,
        isRu: Boolean,
    ): CarePlanResponse {
        if (!treatment.isNullOrEmpty()) return this
        val stored = storedTreatment(scan, isRu)
        return if (stored.isEmpty()) this else copy(treatment = stored)
    }

    private fun storedTreatmentPlan(
        scan: ScanEntity,
        classKey: String,
        isRu: Boolean,
    ): CarePlanResponse? {
        val stored = storedTreatment(scan, isRu)
        if (stored.isEmpty()) return null
        return CarePlanResponse(
            disease = classKey,
            keyAction = stored.first(),
            followUpDays = 14,
            treatment = stored,
        )
    }

    private fun storedTreatment(scan: ScanEntity, isRu: Boolean): List<String> {
        val preferred = scanRepository
            .parseStringList(if (isRu) scan.treatmentRu else scan.treatment)
            .filter { it.isNotBlank() }
        if (preferred.isNotEmpty()) return preferred
        return scanRepository
            .parseStringList(if (isRu) scan.treatment else scan.treatmentRu)
            .filter { it.isNotBlank() }
    }

    /** Materialises a treatment plan from a disease-diagnosing scan, once
     *  the care plan it's built from has actually loaded (Garden refactor
     *  §Scan-driven care checklists). This fires on every render — including
     *  onRestart, off a cached care plan — so it needs three guards: [scan]
     *  must be the plant's newest, the active plan's disease key must differ
     *  (protects an in-progress plan against a newer same-disease scan), and
     *  no plan of ANY status may already exist for this scan. The last one
     *  matters because the active-plan lookup filters `status = 'active'` and
     *  therefore goes blind once the user ticks the final step; without it,
     *  the next visit re-creates the finished plan with progress reset. */
    private fun maybeStartTreatmentPlan(
        plant: PlantEntity,
        scan: ScanEntity,
        classKey: String,
        carePlan: CarePlanResponse,
    ) {
        if (scanRepository.isHealthyVerdict(scan) || scanRepository.isAbstainedScan(scan)) return

        activity.lifecycleScope.launch {
            val newestScanId = plantRepository.getScansForPlant(plant.id).maxByOrNull { it.timestamp }?.id
            if (newestScanId != scan.id) return@launch // a newer scan landed while this card was loading

            val createdPlanId = careRepository.ensureTreatmentPlanForScan(
                plantId = plant.id,
                scanId = scan.id,
                carePlan = carePlan,
                diseaseKey = classKey,
                diseaseLabelEn = scan.diseaseName,
                diseaseLabelRu = scan.diseaseNameRu,
            )
            if (createdPlanId != null) onReload()
        }
    }

    /** One-line "Last watered N days ago" strip directly under the
     *  action row. Reads from the plant_reminder_events log filled
     *  by the Done notification action; null when the user has
     *  never tapped Done on a watering reminder for this plant. */
    fun buildLastWateredStrip(lastWateredAt: Long?): View? {
        val ts = lastWateredAt ?: return null
        val strip = DetailLastWateredStripBinding.inflate(activity.layoutInflater, content, false).root
        strip.text = activity.getString(
            R.string.reminder_streak_last_watered,
            ImageUtils.formatRelativeDate(activity, ts),
        )
        return strip
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
        val binding = DetailAdaptiveWateringCardBinding.inflate(activity.layoutInflater, content, false)
        binding.detailAdaptiveTitle.text = activity.getString(
            R.string.adaptive_watering_title,
            s.currentIntervalDays,
            s.proposedIntervalDays,
        )
        binding.detailAdaptiveSubtitle.text = activity.getString(
            R.string.adaptive_watering_subtitle,
            s.samples,
            String.format(Locale.getDefault(), "%.1f", s.meanGapDays),
        )
        binding.detailAdaptiveApply.text = activity.getString(R.string.adaptive_watering_apply, s.proposedIntervalDays)
        binding.detailAdaptiveApply.setOnClickListener {
            Haptics.tick(activity)
            applyWateringSuggestion(plant, s)
        }
        binding.detailAdaptiveDismiss.setOnClickListener {
            onClearWateringSuggestion()
            binding.root.visibility = View.GONE
        }
        return binding.root
    }

    private fun applyWateringSuggestion(plant: PlantEntity, s: PlantRepository.WateringSuggestion) {
        val appContext = activity.applicationContext
        activity.lifecycleScope.launch(start = CoroutineStart.UNDISPATCHED) {
            withContext(NonCancellable) {
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
                    createdAt = System.currentTimeMillis(),
                )
                val rid = if (existing == null) {
                    plantRepository.saveReminder(updated)
                } else {
                    plantRepository.saveReminderRow(updated)
                }
                com.plantdiseases.app.util.ReminderManager.schedule(
                    context = appContext,
                    plantId = plant.id,
                    plantName = plant.name,
                    type = updated.type,
                    intervalDays = updated.intervalDays,
                    hour = updated.hour,
                    minute = updated.minute,
                    reminderId = rid,
                )
            }
            if (!activity.isFinishing && !activity.isDestroyed) {
                Toast.makeText(
                    activity,
                    activity.getString(R.string.adaptive_watering_applied, s.proposedIntervalDays),
                    Toast.LENGTH_SHORT,
                ).show()
                onClearWateringSuggestion()
                onReload()
            }
        }
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
        val latest = scans
            .filterNot(scanRepository::isAbstainedScan)
            .maxByOrNull { it.timestamp }
            ?: return null
        val classKey = resolveClassKey(latest)
        val guideId = guideIdForClass(classKey) ?: return null
        // RU UI shows the localised verdict: the EN diseaseName leaked through
        // here once (same bug as the garden card badge).
        val verdictLabel = if (LocaleHelper.isRussian(activity)) latest.diseaseNameRu else latest.diseaseName
        return inflateEntry(
            iconRes = R.drawable.ic_care,
            title = activity.getString(R.string.guide_link_title),
            subtitle = activity.getString(R.string.guide_link_subtitle, verdictLabel),
        ) {
            com.plantdiseases.app.ui.guide.GuideDetailSheet
                .newInstance(guideId)
                .show(activity.supportFragmentManager, "guide_detail")
        }
    }

    /** Light-meter shortcut. Launches the standalone
     *  [com.plantdiseases.app.ui.camera.LightMeterActivity] (which
     *  already ships under Profile) so the user can verify the actual
     *  lux at the plant's location. */
    fun buildLightMeterEntry(plant: PlantEntity): View {
        // Surface the previously-recorded lux + when it was measured if the
        // user has run the Light Meter on this plant before; otherwise fall
        // back to the generic invite copy.
        val measured = plant.measuredLux
        val measuredAt = plant.measuredLuxAt
        val subtitle = if (measured != null && measured > 0 && measuredAt != null) {
            activity.getString(
                R.string.light_meter_entry_subtitle_recorded,
                measured,
                ImageUtils.formatRelativeDate(activity, measuredAt),
            )
        } else {
            activity.getString(R.string.light_meter_entry_subtitle)
        }
        return inflateEntry(
            iconRes = R.drawable.ic_light,
            title = activity.getString(R.string.light_meter_entry_title),
            subtitle = subtitle,
        ) {
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
    }

    /** Water-calculator shortcut. Launches the standalone
     *  [com.plantdiseases.app.ui.camera.WaterCalculatorActivity], forwarding
     *  the plant's species so the tool pre-selects the right plant-type bucket
     *  (succulent / tropical / fern / vegetable / general). Advisory only —
     *  nothing is persisted back onto the plant. */
    fun buildWaterCalculatorEntry(plant: PlantEntity): View =
        inflateEntry(
            iconRes = R.drawable.ic_watering,
            title = activity.getString(R.string.water_calc_entry_title),
            subtitle = activity.getString(R.string.water_calc_entry_subtitle),
        ) {
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

    fun buildDoctorEntry(plant: PlantEntity): View =
        inflateEntry(
            iconRes = R.drawable.ic_care,
            title = activity.getString(R.string.doctor_title),
            subtitle = activity.getString(R.string.doctor_stub_subtitle),
            singleLineSubtitle = true,
        ) {
            DoctorChatSheet.show(activity.supportFragmentManager, plant)
        }

    /** Inflates the shared [DetailEntryCardBinding] shortcut card: icon,
     *  title, subtitle and chevron, with a haptic tick before [onClick]. */
    private fun inflateEntry(
        @DrawableRes iconRes: Int,
        title: String,
        subtitle: String,
        singleLineSubtitle: Boolean = false,
        onClick: () -> Unit,
    ): View {
        val binding = DetailEntryCardBinding.inflate(activity.layoutInflater, content, false)
        binding.detailEntryIcon.setImageResource(iconRes)
        binding.detailEntryTitle.text = title
        binding.detailEntrySubtitle.text = subtitle
        if (singleLineSubtitle) binding.detailEntrySubtitle.maxLines = 1
        binding.root.setOnClickListener {
            Haptics.tick(activity)
            onClick()
        }
        return binding.root
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
        val acceptedScans = scans.filterNot(scanRepository::isAbstainedScan)
        if (acceptedScans.size < 2) return null
        // Loading state is the layout default (loading chip text, empty meta line).
        val binding = DetailProgressionCardBinding.inflate(activity.layoutInflater, content, false)
        val card = binding.root
        val trendChip = binding.detailProgressionTrend
        val sparkline = binding.detailProgressionSparkline
        val meta = binding.detailProgressionMeta

        // Server expects oldest-first; cap to the 12 newest to keep
        // payloads bounded (Pydantic on the server doesn't, but the
        // device's network stack still has to upload them).
        val ordered = acceptedScans.sortedBy { it.timestamp }.takeLast(12)
        val historyPayload = ordered.map { scanRepository.scanToProgressionPayload(it) }
        val newest = ordered.last()
        val plantName = currentPlant?.name
        activity.lifecycleScope.launch {
            val res = assistant.fetchProgression(
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
                    else -> activity.getColor(R.color.on_surface)
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
                if (resp.alert && plantName != null) {
                    fireProgressionAlert(plantName, newest.id)
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
     *  notification channel + intent into the plant detail.
     *
     *  The POST_NOTIFICATIONS check gates only `notify()`, never
     *  [NotificationCenter.record][com.plantdiseases.app.util.NotificationCenter.record]:
     *  a user who denied the permission is exactly the user the in-app
     *  notification centre exists for. */
    private suspend fun fireProgressionAlert(plantName: String, latestScanId: Long) {
        // A previous render's network request may finish after a newer scan
        // has already rebuilt this screen. Never notify from that stale
        // history window.
        if (plantRepository.getLatestScanForPlant(plantId)?.id != latestScanId) return

        val notificationId = (plantId * 10 + 9).toInt()
        val title = activity.getString(R.string.progression_alert_title, plantName)
        val body = activity.getString(R.string.progression_alert_body)
        val recorded = ProgressionAlertGate.recordOnce(
            context = activity,
            plantId = plantId,
            latestScanId = latestScanId,
        ) {
            com.plantdiseases.app.util.NotificationCenter.record(
                activity,
                type = "garden",
                title = title,
                body = body,
                deeplink = "plant:$plantId",
            )
        }
        if (!recorded) return

        val contentIntent = android.app.PendingIntent.getActivity(
            activity,
            notificationId,
            Intent(activity, PlantDetailActivity::class.java).apply {
                putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plantId)
            },
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE,
        )
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
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU &&
            androidx.core.content.ContextCompat.checkSelfPermission(
                activity,
                android.Manifest.permission.POST_NOTIFICATIONS,
            ) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        try {
            (activity.getSystemService(android.app.NotificationManager::class.java))
                ?.notify(notificationId, builder.build())
        } catch (_: SecurityException) {
            // permission revoked between check and notify — ignore.
        }
    }

    // ─── Health card (Garden refactor Overview tab) ─────────────────────

    /** Score + delta chip + dated sparkline for [verdict] — the same
     *  verdict the header pill shows, computed once by the caller from
     *  [PlantRepository.getPlantHealth], so the two can't disagree. This is
     *  never the network-driven progression card's comparison, a separate
     *  feature this doesn't touch. Without a score (too few recent scans)
     *  it renders the honest "scan again" empty state instead of a fake
     *  flat line. The status itself is carried by colour here, so the score
     *  also gets a spoken label. */
    fun buildHealthCard(verdict: PlantHealth.Verdict): View {
        val statusColor = activity.getColor(PlantHealthUi.colorRes(verdict.status))
        val binding = DetailHealthCardBinding.inflate(activity.layoutInflater, content, false)
        // Heart in a tinted circle, coloured by status once there is one (no
        // side stripe: the status colour lives on the icon and score).
        val iconTint = if (verdict.status == PlantHealth.Status.UNKNOWN) {
            activity.getColor(R.color.primary)
        } else {
            statusColor
        }
        binding.detailHealthIcon.imageTintList = android.content.res.ColorStateList.valueOf(iconTint)

        val score = verdict.score
        if (score == null) {
            binding.detailHealthEmpty.text = activity.resources.getQuantityString(
                R.plurals.plant_health_need_recent_scans,
                PlantHealth.MIN_SCANS_FOR_SCORE,
                PlantHealth.MIN_SCANS_FOR_SCORE,
            )
            binding.detailHealthEmpty.visibility = View.VISIBLE
            return binding.root
        }

        val statusLabel = activity.getString(PlantHealthUi.labelRes(verdict))
        binding.detailHealthScore.apply {
            text = "$score%"
            contentDescription = "$statusLabel, $score%"
            setTextColor(statusColor)
            visibility = View.VISIBLE
        }

        val weekDelta = verdict.weekDelta
        val weekDeltaDays = verdict.weekDeltaDays
        val trend = verdict.trend
        if (weekDelta != null && weekDeltaDays != null && trend != null) {
            val sign = if (weekDelta >= 0) "+" else ""
            binding.detailHealthDelta.apply {
                text = activity.resources.getQuantityString(
                    R.plurals.plant_health_delta_days,
                    weekDeltaDays,
                    "$sign$weekDelta%",
                    weekDeltaDays,
                )
                setTextColor(activity.getColor(PlantHealthUi.trendColorRes(trend)))
                visibility = View.VISIBLE
            }
        }

        binding.detailHealthSparkline.apply {
            setData(
                verdict.series.map { (it.score / 100f).coerceIn(0f, 1f) },
                PlantHealthUi.sparklineTrend(trend),
            )
            visibility = View.VISIBLE
        }
        return binding.root
    }

    // ─── Treatment plan card (Garden refactor Overview tab) ─────────────

    /** Null (renders nothing) when the plant has no treatment plan worth
     *  showing — never an empty card. Steps render done / current / later /
     *  overdue; the last step completing flips this to the "finished"
     *  single-line state, not a silent disappearance — which is why the
     *  lookup is [CareRepository.activeOrRecentlyCompletedPlan] and not
     *  `activePlan`, whose `status = 'active'` filter made the finished
     *  state unreachable.
     *
     *  Suspend, unlike this controller's other `build*` methods: the
     *  card's very existence depends on a DB read (is there a plan at
     *  all), so the caller awaits this before deciding whether
     *  to add anything to the tab, rather than adding a shell and
     *  filling it in asynchronously. */
    suspend fun buildTreatmentPlanCard(plant: PlantEntity): View? {
        val planWithSteps = careRepository.activeOrRecentlyCompletedPlan(plant.id) ?: return null
        val plan = planWithSteps.plan
        val steps = planWithSteps.steps.sortedBy { it.stepIndex }
        val binding = DetailTreatmentPlanCardBinding.inflate(activity.layoutInflater, content, false)

        if (plan.status != "active") {
            // Completed recently (see activeOrRecentlyCompletedPlan's window)
            // — the finished state, not silence.
            binding.detailTreatmentActive.visibility = View.GONE
            binding.detailTreatmentFinished.apply {
                text = activity.getString(
                    R.string.treatment_finished,
                    steps.count { it.status == "done" },
                    steps.size,
                )
                visibility = View.VISIBLE
            }
            return binding.root
        }

        binding.detailTreatmentDisease.text =
            if (LocaleHelper.isRussian(activity)) plan.diseaseLabelRu else plan.diseaseLabelEn
        val firstPendingIndex = steps.indexOfFirst { it.status == "pending" }
        val currentStepNumber = (if (firstPendingIndex >= 0) firstPendingIndex else steps.lastIndex) + 1
        binding.detailTreatmentStepOf.text =
            activity.getString(R.string.treatment_step_of, currentStepNumber, steps.size)
        val now = System.currentTimeMillis()
        steps.forEachIndexed { index, step ->
            binding.detailTreatmentSteps.addView(
                buildTreatmentStepRow(
                    binding.detailTreatmentSteps,
                    step,
                    isCurrent = index == firstPendingIndex,
                    isOverdue = step.status == "pending" && step.dueAt <= now,
                ),
            )
        }
        return binding.root
    }

    private fun buildTreatmentStepRow(
        parent: LinearLayout,
        step: TreatmentStepEntity,
        isCurrent: Boolean,
        isOverdue: Boolean,
    ): View {
        val row = DetailTreatmentStepRowBinding.inflate(activity.layoutInflater, parent, false)
        val isDone = step.status == "done"
        row.detailStepGlyph.setImageResource(if (isDone) R.drawable.ic_check else R.drawable.ic_disease)
        row.detailStepGlyph.setColorFilter(
            activity.getColor(
                when {
                    isDone -> R.color.healthy_green
                    isOverdue || isCurrent -> R.color.disease_red
                    else -> R.color.on_surface_secondary
                },
            ),
        )
        row.detailStepTitle.text = step.title
        row.detailStepState.text = when {
            isDone -> activity.getString(
                R.string.treatment_step_done,
                formatRelativeDate(activity, step.doneAt ?: step.dueAt),
            )
            isOverdue -> activity.getString(R.string.treatment_step_overdue, formatRelativeDate(activity, step.dueAt))
            else -> activity.getString(R.string.treatment_step_due, formatRelativeDate(activity, step.dueAt))
        }
        if (isOverdue) row.detailStepState.setTextColor(activity.getColor(R.color.disease_red))
        row.root.setOnClickListener {
            Haptics.tick(activity)
            activity.startActivity(
                Intent(activity, com.plantdiseases.app.ui.garden.care.CareTaskActivity::class.java).apply {
                    putExtra(com.plantdiseases.app.ui.garden.care.CareTaskActivity.EXTRA_TASK_KIND, "step")
                    putExtra(com.plantdiseases.app.ui.garden.care.CareTaskActivity.EXTRA_STEP_ID, step.id)
                },
            )
        }
        return row.root
    }
}
