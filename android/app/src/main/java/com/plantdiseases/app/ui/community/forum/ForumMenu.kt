package com.plantdiseases.app.ui.community.forum

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.PopupWindow
import androidx.annotation.ColorRes
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.core.content.ContextCompat
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.ItemForumMenuRowBinding
import com.plantdiseases.app.databinding.PopupForumMenuBinding

/**
 * The Community overflow menu: a rounded card of rows, each with a tinted
 * icon disc, that grows out of the top-end corner of [anchor]. Tapping a row
 * closes the menu, then runs that row's action.
 */
class ForumMenu(private val anchor: View) {

    /** One row; [tone] picks the icon colour and its disc behind it. */
    data class Item(
        @StringRes val label: Int,
        @DrawableRes val icon: Int,
        val tone: Tone,
        val onClick: () -> Unit,
    )

    enum class Tone(@ColorRes val foreground: Int, @ColorRes val background: Int) {
        GREEN(R.color.primary, R.color.primary_light),
        AMBER(R.color.warning_amber, R.color.warning_amber_bg),
        RED(R.color.disease_red, R.color.disease_red_bg),
        BLUE(R.color.neutral_blue, R.color.neutral_blue_bg),
    }

    fun show(items: List<Item>) {
        val context = anchor.context
        val inflater = LayoutInflater.from(context)
        val binding = PopupForumMenuBinding.inflate(inflater)
        val popup = PopupWindow(
            binding.root,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            true,
        )
        items.forEach { item ->
            val row = ItemForumMenuRowBinding.inflate(inflater, binding.menuRows, false)
            row.tvLabel.setText(item.label)
            row.ivIcon.setImageResource(item.icon)
            row.ivIcon.imageTintList = ColorStateList.valueOf(ContextCompat.getColor(context, item.tone.foreground))
            row.ivIcon.backgroundTintList =
                ColorStateList.valueOf(ContextCompat.getColor(context, item.tone.background))
            row.root.setOnClickListener {
                popup.dismiss()
                item.onClick()
            }
            binding.menuRows.addView(row.root)
        }
        popup.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        // The window's own shadow follows its rectangular bounds and shows as
        // square corners; the rounded card casts the only shadow.
        popup.elevation = 0f
        popup.isOutsideTouchable = true
        popup.animationStyle = R.style.Forum_MenuAnimation
        // The card's outer padding is shadow room; pull it back so the card's
        // edge lines up with the anchor's.
        val shadow = context.resources.getDimensionPixelSize(R.dimen.forum_menu_shadow)
        popup.showAsDropDown(anchor, shadow, -shadow, Gravity.END)
    }
}
