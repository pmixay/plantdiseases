package com.plantdiseases.app.util

/**
 * The on-device Aqua model's classifier-side out-of-distribution signal: a
 * MaxLogit floor fitted by notebook §5 and shipped in the sidecar as
 * `classes.json -> ood`.
 *
 * Aqua is single-stage (no detector, no embedding bank), so before this it
 * refused only blurry frames and a natural `not_a_plant` argmax. A frame whose
 * highest logit over the scoped columns falls below [maxLogitFloor] looks like
 * none of the conditions the model knows and is refused as out of
 * distribution. With the `plant_only` scope the `not_a_plant` column is left
 * out, so the score means "does this look like any plant condition?", the
 * same scope as the server's gate.
 */
class AquaOodGate(
    val maxLogitFloor: Float,
    /** Logit column left out of the max, or null to score every class. */
    private val excludedColumn: Int?,
) {
    init {
        require(maxLogitFloor.isFinite()) { "max_logit_floor must be finite" }
    }

    fun score(logits: FloatArray): Float {
        var best = Float.NEGATIVE_INFINITY
        for (i in logits.indices) {
            if (i != excludedColumn && logits[i] > best) best = logits[i]
        }
        require(best.isFinite()) { "No scoreable Aqua logit" }
        return best
    }

    /** True when the frame is out of distribution and must be refused. */
    fun rejects(logits: FloatArray): Boolean = score(logits) < maxLogitFloor

    companion object {
        const val SCOPE_PLANT_ONLY = "plant_only"
        const val SCOPE_ALL_CLASSES = "all_classes"
        private const val NOT_A_PLANT = "not_a_plant"

        /**
         * The gate a sidecar declares. Null when it declares none: no `ood`
         * object, or a null floor (the notebook ships null when the floor does
         * not separate plants from not_a_plant). A malformed block throws, so
         * the bundle is refused rather than served without the gate it asks for.
         */
        fun fromSidecar(floor: Double?, scope: String?, classNames: List<String>): AquaOodGate? {
            if (floor == null) return null
            require(floor.isFinite()) { "ood.max_logit_floor must be finite" }
            val excluded = when (scope) {
                SCOPE_PLANT_ONLY -> classNames.indexOf(NOT_A_PLANT).also {
                    require(it >= 0) { "plant_only scope needs a not_a_plant class" }
                }
                SCOPE_ALL_CLASSES -> null
                else -> throw IllegalArgumentException("Unknown ood.max_logit_scope: $scope")
            }
            return AquaOodGate(floor.toFloat(), excluded)
        }
    }
}
