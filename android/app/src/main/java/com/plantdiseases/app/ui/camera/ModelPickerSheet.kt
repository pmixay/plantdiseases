package com.plantdiseases.app.ui.camera

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.fragment.app.FragmentManager
import androidx.fragment.app.setFragmentResult
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.plantdiseases.app.R
import com.plantdiseases.app.util.AquaModelManager
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.ModelFamilyPrefs

/**
 * Bottom-sheet model-family picker shown from the scan screen.
 *
 * Lists the three tiers (Aqua / Gamma / Frontier) with an icon, name and a
 * one-line description. Aqua carries an "Offline" badge. A family the server
 * doesn't serve (frontier when disabled — see [ModelFamilyPrefs.frontierAvailable])
 * is shown disabled with an "Unavailable on this server" badge and can't be
 * picked. The chosen family is persisted; a Fragment Result on [REQUEST_KEY]
 * lets the host refresh its chip. (A lambda field set from the static show()
 * did not survive a configuration-change recreation of the open sheet, so a
 * pick made after rotating left the host chip stale until the next resume.)
 */
class ModelPickerSheet : BottomSheetDialogFragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.sheet_model_picker, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val ctx = requireContext()
        val container = view.findViewById<LinearLayout>(R.id.model_family_container)
        val selected = ModelFamilyPrefs.selected(ctx)
        val frontierAvailable = ModelFamilyPrefs.frontierAvailable(ctx)
        val aquaInstalled = AquaModelManager.isInstalled(ctx)

        for (family in ModelFamily.entries) {
            // Aqua is pickable only once its on-device model has downloaded.
            // Until then it's treated exactly like an unavailable frontier:
            // greyed out with an "Unavailable" badge. We deliberately do NOT
            // show a "Downloading…" badge — the fetch is best-effort and may
            // never complete (no server model, offline, etc.), so a perpetual
            // "Downloading…" would just confuse the user into waiting forever.
            val available = when (family) {
                ModelFamily.FRONTIER -> frontierAvailable
                ModelFamily.AQUA -> aquaInstalled
                else -> true
            }
            val row = layoutInflater.inflate(R.layout.item_model_family, container, false)

            row.findViewById<ImageView>(R.id.family_icon).setImageResource(family.iconRes)
            row.findViewById<TextView>(R.id.family_title).setText(family.labelRes)
            row.findViewById<TextView>(R.id.family_desc).setText(family.descriptionRes)

            val badge = row.findViewById<TextView>(R.id.family_badge)
            when {
                !available -> {
                    badge.setText(R.string.model_family_unavailable)
                    badge.visibility = View.VISIBLE
                }
                family.onDevice -> {
                    badge.setText(R.string.model_family_offline_badge)
                    badge.visibility = View.VISIBLE
                }
                else -> badge.visibility = View.GONE
            }

            val isSelected = family == selected && available
            row.findViewById<ImageView>(R.id.family_check).visibility =
                if (isSelected) View.VISIBLE else View.INVISIBLE

            // Announce selection to TalkBack as a state on the whole row
            // (the check icon itself is decorative) so screen-reader users
            // know which family is active without seeing the checkmark.
            row.isSelected = isSelected
            ViewCompat.setStateDescription(
                row,
                if (isSelected) getString(R.string.cd_selected) else null,
            )

            row.isEnabled = available
            row.alpha = if (available) 1f else 0.45f
            if (available) {
                row.setOnClickListener {
                    ModelFamilyPrefs.setSelected(ctx, family)
                    setFragmentResult(REQUEST_KEY, Bundle.EMPTY)
                    dismiss()
                }
            }
            container.addView(row)
        }
    }

    companion object {
        private const val TAG = "ModelPickerSheet"

        /** Fragment Result key fired after a family is picked + persisted.
         *  The payload is empty — hosts re-read [ModelFamilyPrefs]. */
        const val REQUEST_KEY = "model_family_picked"

        fun show(fm: FragmentManager) {
            ModelPickerSheet().show(fm, TAG)
        }
    }
}
