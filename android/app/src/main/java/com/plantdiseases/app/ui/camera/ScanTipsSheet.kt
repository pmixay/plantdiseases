package com.plantdiseases.app.ui.camera

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.FragmentManager
import androidx.fragment.app.setFragmentResult
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.plantdiseases.app.R

/**
 * Pre-scan tips bottom sheet. Replaces the v1 auto-capture flow with
 * an explicit "read these three rules, then tap to scan" gate.
 *
 * Three tips:
 *   1. Frame a single leaf at arm's length — not too close, not the
 *      whole plant + the room behind it.
 *   2. Hold steady, wait for autofocus, avoid back-lit shots.
 *   3. One species per frame — multiple plants confuse the
 *      classifier and the species fan-out.
 *
 * The sheet is shown the first time the user taps the shutter on a
 * fresh install; the camera fragment records the
 * ``scan_tips_seen`` pref so subsequent scans go straight to capture.
 */
class ScanTipsSheet : BottomSheetDialogFragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.sheet_scan_tips, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        view.findViewById<MaterialButton>(R.id.btn_tips_continue).setOnClickListener {
            // Emit a Fragment Result instead of invoking a transient lambda, so
            // the "Continue" signal survives a config-change recreation while
            // the sheet is open. The host (CameraFragment) listens for it.
            setFragmentResult(REQUEST_KEY, Bundle.EMPTY)
            dismiss()
        }
    }

    companion object {
        private const val TAG = "scan-tips-sheet"
        const val REQUEST_KEY = "scan_tips_continue"

        fun show(fm: FragmentManager) {
            ScanTipsSheet().show(fm, TAG)
        }
    }
}
