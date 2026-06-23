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
        inflater: LayoutInflater,
        container: ViewGroup?,
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

        binding.tvTitle.text = if (isRu) item.titleRu else item.titleEn
        binding.ivIcon.setImageResource(item.iconRes)
        binding.btnClose.setOnClickListener { dismiss() }

        GuideContentRenderer.render(binding.llSections, if (isRu) item.contentRu else item.contentEn)
        // Pin a single prominent example photo above the article cards so the
        // topic opens with "here's what it looks like" (omitted where none).
        GuideDataProvider.exampleImageUrl(item.id)?.let { url ->
            binding.llSections.addView(
                GuideContentRenderer.heroPhoto(requireContext(), url, binding.tvTitle.text),
                0,
            )
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    companion object {
        private const val ARG_ITEM_ID = "item_id"

        fun newInstance(itemId: String): GuideDetailSheet = GuideDetailSheet().apply {
            arguments = Bundle().apply {
                putString(ARG_ITEM_ID, itemId)
            }
        }
    }
}
