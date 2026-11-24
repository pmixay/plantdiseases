package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertThrows
import org.junit.Test

/** The sidecar's MaxLogit floor ([AquaOodGate]): parsing and scoring. Pure JVM. */
class AquaOodGateTest {

    private val classes = listOf("healthy", "leaf_spot", "not_a_plant")

    @Test
    fun no_floor_means_no_gate() {
        assertThat(AquaOodGate.fromSidecar(null, AquaOodGate.SCOPE_PLANT_ONLY, classes)).isNull()
        assertThat(AquaOodGate.fromSidecar(null, null, classes)).isNull()
    }

    @Test
    fun plant_only_scope_ignores_the_not_a_plant_logit() {
        val gate = AquaOodGate.fromSidecar(3.0, AquaOodGate.SCOPE_PLANT_ONLY, classes)!!
        // A confident not_a_plant logit must not lift the plant score.
        val logits = floatArrayOf(1.0f, 2.5f, 9.0f)
        assertThat(gate.score(logits)).isEqualTo(2.5f)
        assertThat(gate.rejects(logits)).isTrue()
        assertThat(gate.rejects(floatArrayOf(1.0f, 3.5f, 9.0f))).isFalse()
    }

    @Test
    fun all_classes_scope_scores_every_column() {
        val gate = AquaOodGate.fromSidecar(3.0, AquaOodGate.SCOPE_ALL_CLASSES, classes)!!
        assertThat(gate.rejects(floatArrayOf(1.0f, 2.5f, 9.0f))).isFalse()
    }

    @Test
    fun a_floor_the_app_cannot_apply_is_refused() {
        assertThrows(IllegalArgumentException::class.java) {
            AquaOodGate.fromSidecar(3.0, "mystery", classes)
        }
        assertThrows(IllegalArgumentException::class.java) {
            AquaOodGate.fromSidecar(3.0, null, classes)
        }
        assertThrows(IllegalArgumentException::class.java) {
            AquaOodGate.fromSidecar(Double.NaN, AquaOodGate.SCOPE_ALL_CLASSES, classes)
        }
        assertThrows(IllegalArgumentException::class.java) {
            AquaOodGate.fromSidecar(3.0, AquaOodGate.SCOPE_PLANT_ONLY, listOf("healthy", "leaf_spot"))
        }
    }
}
