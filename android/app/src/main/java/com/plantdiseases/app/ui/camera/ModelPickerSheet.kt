package com.plantdiseases.app.ui.camera

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.FragmentManager
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.plantdiseases.app.R
import com.plantdiseases.app.util.ModelFamily
import com.plantdiseases.app.util.ModelFamilyPrefs

/**
 * Bottom-sheet model-family picker shown from the scan screen.
 *
 * Lists the three tiers (Aqua / Gamma / Frontier) with an icon, name and a
 * one-line description. Aqua carries an "Offline" badge. A family the server
 * doesn't serve (frontier when disabled — see [ModelFamilyPrefs.frontierAvailable])
 * is shown disabled with an "Unavailable on this server" badge and can't be
 * picked. The chosen family is persisted; [onPicked] lets the host refresh
 * its chip.
 */
class ModelPickerSheet : BottomSheetDialogFragment() {

    private var onPicked: ((ModelFamily) -> Unit)? = null

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.sheet_model_picker, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val ctx = requireContext()
        val container = view.findViewById<LinearLayout>(R.id.model_family_container)
        val selected = ModelFamilyPrefs.selected(ctx)
        val frontierAvailable = ModelFamilyPrefs.frontierAvailable(ctx)

        for (family in ModelFamily.entries) {
            val available = family != ModelFamily.FRONTIER || frontierAvailable
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

            row.findViewById<ImageView>(R.id.family_check).visibility =
                if (family == selected && available) View.VISIBLE else View.INVISIBLE

            row.isEnabled = available
            row.alpha = if (available) 1f else 0.45f
            if (available) {
                row.setOnClickListener {
                    ModelFamilyPrefs.setSelected(ctx, family)
                    onPicked?.invoke(family)
                    dismiss()
                }
            }
            container.addView(row)
        }
    }

    companion object {
        private const val TAG = "ModelPickerSheet"

        fun show(fm: FragmentManager, onPicked: (ModelFamily) -> Unit) {
            ModelPickerSheet().apply { this.onPicked = onPicked }.show(fm, TAG)
        }
    }
}
