package com.plantdiseases.app.ui.result

import android.app.Dialog
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import androidx.fragment.app.DialogFragment
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.databinding.DialogDiseaseAppearanceBinding
import com.plantdiseases.app.ui.guide.GuideDetailSheet

/** A focused, dismissible diagnosis reference: image, description and guide. */
class DiseaseAppearanceDialog : DialogFragment() {

    private var _binding: DialogDiseaseAppearanceBinding? = null
    private val binding get() = _binding!!

    override fun onCreateDialog(savedInstanceState: Bundle?): Dialog =
        Dialog(requireContext()).apply {
            requestWindowFeature(Window.FEATURE_NO_TITLE)
            setCanceledOnTouchOutside(true)
        }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = DialogDiseaseAppearanceBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val imageUrl = arguments?.getString(ARG_IMAGE_URL)
        val diseaseName = arguments?.getString(ARG_DISEASE_NAME).orEmpty()
        val description = arguments?.getString(ARG_DESCRIPTION) ?: return dismiss()
        val guideId = arguments?.getString(ARG_GUIDE_ID)

        binding.appearanceOverlay.setOnClickListener { dismiss() }
        binding.cardDiseaseAppearance.setOnClickListener { /* Consume outside-dismiss taps. */ }
        binding.btnCloseAppearance.setOnClickListener { dismiss() }
        binding.ivDiseaseAppearance.contentDescription = diseaseName
        binding.tvAppearanceDescription.text = description

        if (imageUrl == null) {
            binding.ivDiseaseAppearance.visibility = View.GONE
        } else {
            Glide.with(this)
                .load(imageUrl)
                .diskCacheStrategy(DiskCacheStrategy.ALL)
                .placeholder(R.drawable.ic_plant_placeholder)
                .error(R.drawable.ic_plant_placeholder)
                .transform(CenterCrop(), RoundedCorners(20))
                .into(binding.ivDiseaseAppearance)
        }

        if (guideId != null) {
            binding.btnOpenAppearanceGuide.visibility = View.VISIBLE
            binding.btnOpenAppearanceGuide.setOnClickListener {
                GuideDetailSheet.newInstance(guideId).show(parentFragmentManager, GUIDE_DETAIL_TAG)
                dismiss()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        dialog?.window?.apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            setLayout(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            setGravity(Gravity.CENTER)
            setWindowAnimations(R.style.App_Animation_DiseaseAppearance)
            attributes = attributes.apply {
                dimAmount = 0.5f
            }
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        }
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }

    companion object {
        private const val ARG_IMAGE_URL = "image_url"
        private const val ARG_DISEASE_NAME = "disease_name"
        private const val ARG_DESCRIPTION = "description"
        private const val ARG_GUIDE_ID = "guide_id"
        private const val GUIDE_DETAIL_TAG = "guide_detail"

        fun newInstance(
            imageUrl: String?,
            diseaseName: String,
            description: String,
            guideId: String?,
        ): DiseaseAppearanceDialog =
            DiseaseAppearanceDialog().apply {
                arguments = Bundle().apply {
                    putString(ARG_IMAGE_URL, imageUrl)
                    putString(ARG_DISEASE_NAME, diseaseName)
                    putString(ARG_DESCRIPTION, description)
                    putString(ARG_GUIDE_ID, guideId)
                }
            }
    }
}
