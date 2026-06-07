package com.plantdiseases.app.ui.guide

import android.graphics.Typeface
import android.os.Bundle
import android.util.TypedValue
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R
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

        binding.tvTitle.text = if (isRu) item.titleRu else item.titleEn
        binding.ivIcon.setImageResource(item.iconRes)
        binding.btnClose.setOnClickListener { dismiss() }

        renderSections(if (isRu) item.contentRu else item.contentEn)
        loadExamplePhotos(item.id)
    }

    /** Pin a single prominent example photo above the article cards so the
     *  topic opens with "here's what it looks like". The photo is a bundled
     *  server-hosted photo (disk-cached for offline), omitted where none. */
    private fun loadExamplePhotos(guideId: String) {
        val imageUrl = GuideDataProvider.exampleImageUrl(guideId) ?: return
        binding.llSections.addView(buildHeroPhoto(imageUrl), 0)
    }

    private fun buildHeroPhoto(imageUrl: String): View {
        val ctx = requireContext()
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(16) }
        }
        column.addView(TextView(ctx).apply {
            text = getString(R.string.example_photos_label)
            textSize = 12f
            setTextColor(ContextCompat.getColor(ctx, R.color.primary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            letterSpacing = 0.06f
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(8) }
        })
        val image = ImageView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(190),
            )
            scaleType = ImageView.ScaleType.CENTER_CROP
            contentDescription = binding.tvTitle.text
        }
        Glide.with(this@GuideDetailSheet)
            .load(imageUrl)
            .diskCacheStrategy(DiskCacheStrategy.ALL)
            .placeholder(R.drawable.ic_plant_placeholder)
            .error(R.drawable.ic_plant_placeholder)
            .transform(CenterCrop(), RoundedCorners(dp(16)))
            .into(image)
        column.addView(image)
        return column
    }

    /** Split the article into its hand-typed sections (intro + SYMPTOMS: /
     *  TREATMENT: / … blocks) and render each as an accent-headed card so the
     *  body reads as a designed document, not one flat grey block. */
    private fun renderSections(content: String) {
        val container = binding.llSections
        container.removeAllViews()
        parseSections(content).forEach { section ->
            container.addView(
                if (section.header == null) introView(section.body)
                else sectionCard(section.header, section.body)
            )
        }
    }

    private fun introView(body: String): View = TextView(requireContext()).apply {
        text = body
        textSize = 15f
        setTextColor(ContextCompat.getColor(context, R.color.on_surface))
        setLineSpacing(dp(3).toFloat(), 1f)
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { bottomMargin = dp(14) }
    }

    private fun sectionCard(header: String, body: String): View {
        val ctx = requireContext()
        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        col.addView(TextView(ctx).apply {
            text = header.uppercase()
            textSize = 12f
            setTextColor(ContextCompat.getColor(ctx, R.color.primary))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            letterSpacing = 0.06f
        })
        col.addView(TextView(ctx).apply {
            text = body
            textSize = 15f
            setTextColor(ContextCompat.getColor(ctx, R.color.on_surface))
            setLineSpacing(dp(3).toFloat(), 1f)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = dp(6) }
        })
        return MaterialCardView(ctx).apply {
            radius = dp(14).toFloat()
            cardElevation = 0f
            setCardBackgroundColor(ContextCompat.getColor(ctx, R.color.surface_variant))
            strokeWidth = dp(1)
            strokeColor = ContextCompat.getColor(ctx, R.color.card_stroke)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dp(12) }
            addView(col)
        }
    }

    private data class Section(val header: String?, val body: String)

    private fun parseSections(content: String): List<Section> {
        val sections = mutableListOf<Section>()
        var header: String? = null
        val buf = StringBuilder()
        fun flush() {
            val text = buf.toString().trim()
            if (text.isNotEmpty() || header != null) sections.add(Section(header, text))
            buf.setLength(0)
        }
        content.lines().forEach { line ->
            val t = line.trim()
            // A header is a short, all-caps line ending in ':' (EN or RU),
            // e.g. "SYMPTOMS:" / "ЛЕЧЕНИЕ:" / "WINTER (Dec-Feb):". Bullets and
            // numbered steps start with a non-letter, so they never match.
            val lead = t.takeWhile { it.isLetter() }
            val isHeader = t.endsWith(":") && t.length <= 48 &&
                lead.length >= 3 && lead == lead.uppercase()
            if (isHeader) {
                flush()
                header = t.removeSuffix(":").trim()
            } else {
                buf.appendLine(line)
            }
        }
        flush()
        return sections
    }

    private fun dp(v: Int): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics,
    ).toInt()

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
