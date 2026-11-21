package com.plantdiseases.app.ui.garden

import androidx.annotation.ColorRes
import androidx.annotation.StringRes
import com.plantdiseases.app.R
import com.plantdiseases.app.util.PlantHealth

/**
 * The single [PlantHealth] -> label / colour mapping. The plant-detail
 * header, the plant-detail health card and the room plant list all render a
 * verdict; mapping it in one place keeps a status from being wired into one
 * of them and forgotten in another.
 */
object PlantHealthUi {

    /** Status label. UNKNOWN splits on whether the plant was ever scanned:
     *  a plant whose scans are too few or too old is not "No scans yet". */
    @StringRes
    fun labelRes(verdict: PlantHealth.Verdict): Int = when (verdict.status) {
        PlantHealth.Status.HEALTHY -> R.string.plant_status_healthy
        PlantHealth.Status.WATCH -> R.string.plant_status_watch
        PlantHealth.Status.POOR -> R.string.plant_status_poor
        PlantHealth.Status.TREATMENT -> R.string.plant_status_treatment
        PlantHealth.Status.RECOVERING -> R.string.plant_status_recovering
        PlantHealth.Status.UNKNOWN -> if (verdict.scanCount == 0) R.string.plant_status_no_scans else R.string.plant_status_need_scans
    }

    /** Status accent: green doing well, amber unsure or one bad reading,
     *  red a confirmed problem (POOR) or one being treated. */
    @ColorRes
    fun colorRes(status: PlantHealth.Status): Int = when (status) {
        PlantHealth.Status.HEALTHY, PlantHealth.Status.RECOVERING -> R.color.healthy_green
        PlantHealth.Status.WATCH -> R.color.warning_amber
        PlantHealth.Status.POOR, PlantHealth.Status.TREATMENT -> R.color.disease_red
        PlantHealth.Status.UNKNOWN -> R.color.on_surface
    }

    /** Trend colour: a change inside the noise band stays neutral instead
     *  of flashing red or green. */
    @ColorRes
    fun trendColorRes(trend: PlantHealth.Trend): Int = when (trend) {
        PlantHealth.Trend.IMPROVING -> R.color.healthy_green
        PlantHealth.Trend.STABLE -> R.color.on_surface
        PlantHealth.Trend.DECLINING -> R.color.disease_red
    }

    /** The trend key [SparklineView.setData] colours the line by. */
    fun sparklineTrend(trend: PlantHealth.Trend?): String = when (trend) {
        null -> "baseline"
        PlantHealth.Trend.IMPROVING -> "improving"
        PlantHealth.Trend.STABLE -> "stable"
        PlantHealth.Trend.DECLINING -> "worsening"
    }
}
