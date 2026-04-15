package com.plantdiseases.app.ui.guide

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.databinding.SheetGuideDetailBinding
import com.plantdiseases.app.util.LocaleHelper

class GuideDetailSheet : BottomSheetDialogFragment() {

    private var _binding: SheetGuideDetailBinding? = null
    private val binding get() = _binding!!

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = SheetGuideDetailBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val itemId = arguments?.getString(ARG_ITEM_ID) ?: return dismiss()
        val item = GuideDataProvider.getGuideItems().find { it.id == itemId } ?: return dismiss()
        val isRu = LocaleHelper.isRussian(requireContext())

        binding.apply {
            tvTitle.text = if (isRu) item.titleRu else item.titleEn
            tvContent.text = if (isRu) item.contentRu else item.contentEn
            ivIcon.setImageResource(item.iconRes)
            btnClose.setOnClickListener { dismiss() }
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    companion object {
        private const val ARG_ITEM_ID = "item_id"

        fun newInstance(itemId: String): GuideDetailSheet {
            return GuideDetailSheet().apply {
                arguments = Bundle().apply {
                    putString(ARG_ITEM_ID, itemId)
                }
            }
        }
    }
}
