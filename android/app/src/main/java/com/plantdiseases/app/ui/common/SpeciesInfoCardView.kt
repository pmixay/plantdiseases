package com.plantdiseases.app.ui.common

import android.content.Context
import android.util.AttributeSet
import android.view.LayoutInflater
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.view.isVisible
import androidx.lifecycle.findViewTreeLifecycleOwner
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.color.MaterialColors
import com.plantdiseases.app.R
import com.plantdiseases.app.di.appEntryPoint
import com.plantdiseases.app.ui.species.SpeciesDetailActivity
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.hasCyrillic
import com.plantdiseases.app.util.localizedSpeciesName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import com.google.android.material.R as MaterialR

/**
 * The one species card used everywhere a specific species is referenced —
 * scan result, species identify, community opinion sheet.
 *
 * It shows only what identifies the plant: the Wikipedia lead photo, the
 * species name, the Wikipedia description, and a "more about this species"
 * action opening [SpeciesDetailActivity]. Once Wikipedia content is shown, an
 * "i" beside the name opens its attribution ([WikipediaAttribution]). Care,
 * toxicity, flowering, native range and common problems deliberately live on
 * that screen alone, so this card is the same height on every surface it is
 * embedded in.
 *
 * Bind is safe to call repeatedly: every call resets the card to its empty
 * state first, so a recycled host never shows the previous species' photo.
 *
 * Not `@AndroidEntryPoint`: [SpeciesInfoCardViewTest] constructs this view
 * directly against a plain (non-Hilt) `AppCompatActivity` host, which a
 * Hilt-injected View would crash against at construction time. Resolving the
 * repository lazily via [appEntryPoint] — forced only inside [loadEnrichment],
 * never on the null-`scientific` paths that test exercises — keeps the field
 * untouched on every path that test covers.
 */
class SpeciesInfoCardView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : MaterialCardView(context, attrs, defStyle) {

    private val scanRepository by lazy(LazyThreadSafetyMode.NONE) {
        appEntryPoint(context).scanRepository()
    }

    private val photo: ImageView
    private val nameView: TextView
    private val scientificView: TextView
    private val descriptionView: TextView
    private val expandRow: LinearLayout
    private val expandLabel: TextView
    private val expandChevron: ImageView
    private val moreButton: MaterialButton
    private val attributionButton: ImageButton

    /** Guards against a stale response from a previous [bind] landing last. */
    private var bindToken = 0

    private val collapsedLines = resources.getInteger(R.integer.species_info_collapsed_lines)

    init {
        // Styled here rather than via a host-applied style so the card is
        // pixel-identical on every surface — that uniformity is the point of
        // having one species card. Mirrors CarePlanCardView's approach.
        radius = resources.getDimension(R.dimen.radius_lg)
        cardElevation = 0f
        useCompatPadding = false
        strokeWidth = resources.getDimensionPixelSize(R.dimen.glass_stroke_width)
        setStrokeColor(context.getColorStateList(R.color.card_stroke))
        setCardBackgroundColor(
            MaterialColors.getColor(this, MaterialR.attr.colorSurfaceContainerLow, 0),
        )
        val pad = resources.getDimensionPixelSize(R.dimen.space_lg)
        setContentPadding(pad, pad, pad, pad)

        LayoutInflater.from(context).inflate(R.layout.view_species_info_card, this, true)
        photo = findViewById(R.id.species_info_photo)
        nameView = findViewById(R.id.species_info_name)
        scientificView = findViewById(R.id.species_info_scientific)
        descriptionView = findViewById(R.id.species_info_description)
        expandRow = findViewById(R.id.species_info_expand_row)
        expandLabel = findViewById(R.id.species_info_expand_label)
        expandChevron = findViewById(R.id.species_info_expand_chevron)
        moreButton = findViewById(R.id.species_info_more)
        attributionButton = findViewById(R.id.species_info_attribution)

        expandRow.setOnClickListener { setExpanded(descriptionView.maxLines == collapsedLines) }
    }

    /**
     * Convenience overload for hosts inside a lifecycle-owning view tree —
     * every current caller. Enrichment runs on the owner's scope so it is
     * cancelled with the host; without an owner the card still renders the
     * names it was given and simply skips the Wikipedia fetch.
     */
    fun bind(scientific: String?, common: String?): Boolean =
        bind(findViewTreeLifecycleOwner()?.lifecycleScope, scientific, common)

    /**
     * Render [scientific]/[common] immediately, then enrich with the Wikipedia
     * photo and description on [scope]. A null [scope] renders the names and
     * skips the fetch.
     *
     * Returns false — and hides itself — when neither name is usable, so hosts
     * can simply call `bind(...)` without pre-checking. The "more about this
     * species" action is shown only when [scientific] is present, because
     * [SpeciesDetailActivity] finishes immediately without a binomial.
     */
    fun bind(scope: CoroutineScope?, scientific: String?, common: String?): Boolean {
        val sci = scientific?.trim()?.takeIf { it.isNotEmpty() }
        val commonTrimmed = common?.trim()?.takeIf { it.isNotEmpty() }
        val token = ++bindToken

        reset()
        if (sci == null && commonTrimmed == null) {
            isVisible = false
            return false
        }
        isVisible = true

        val isRu = LocaleHelper.isRussian(context)
        val initialName = localizedSpeciesName(sci, commonTrimmed, isRu).orEmpty()
        nameView.text = initialName
        showScientific(sci, initialName)

        if (sci != null) {
            moreButton.isVisible = true
            moreButton.setOnClickListener {
                val display = nameView.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }
                SpeciesDetailActivity.start(context, sci, display ?: commonTrimmed)
            }
            scope?.let { loadEnrichment(it, sci, commonTrimmed, isRu, token) }
        }
        return true
    }

    /** Clears every enriched field so a rebound card never leaks the previous species. */
    private fun reset() {
        clearPhotoRequest()
        photo.setImageResource(R.drawable.ic_plant_placeholder)
        photo.isVisible = false
        scientificView.isVisible = false
        descriptionView.isVisible = false
        descriptionView.text = null
        expandRow.isVisible = false
        moreButton.isVisible = false
        moreButton.setOnClickListener(null)
        attributionButton.isVisible = false
        attributionButton.setOnClickListener(null)
        setExpanded(expanded = false)
    }

    private fun showScientific(scientific: String?, displayName: String) {
        scientificView.text = scientific.orEmpty()
        scientificView.isVisible =
            scientific != null &&
            !scientific.equals(displayName, ignoreCase = true)
    }

    private fun loadEnrichment(
        scope: CoroutineScope,
        scientific: String,
        common: String?,
        isRu: Boolean,
        token: Int,
    ) {
        scope.launch {
            val info = scanRepository
                .getSpeciesInfo(scientific, if (isRu) "ru" else "en")
                .getOrNull()
                ?.takeIf { it.available }
            // A newer bind started while this request was in flight.
            if (token != bindToken || info == null) return@launch

            // A Russian user gets the Russian Wikipedia title when the caller's
            // common name is English-only — PlantNet's RU coverage is thin.
            // Matches the rule the result-screen card used before this card
            // replaced it.
            val localized = info.title?.trim()?.takeIf { it.isNotEmpty() }
            val displayName = when {
                isRu && (common == null || !hasCyrillic(common)) ->
                    localized ?: common ?: scientific
                else -> common ?: localized ?: scientific
            }
            nameView.text = displayName
            showScientific(scientific, displayName)

            info.imageUrl?.trim()?.takeIf { it.isNotEmpty() }?.let { url ->
                Glide.with(context)
                    .load(ServerConfig.photoUrl(url))
                    .diskCacheStrategy(DiskCacheStrategy.ALL)
                    .transform(CenterCrop(), RoundedCorners(PHOTO_CORNER_RADIUS_PX))
                    .into(photo)
                photo.isVisible = true
            }

            info.extract?.trim()?.takeIf { it.isNotEmpty() }?.let { extract ->
                descriptionView.text = extract
                descriptionView.isVisible = true
                revealToggleIfClipped()
            }

            // CC BY-SA: whatever Wikipedia content is on the card carries
            // its source and licence one tap away.
            if (photo.isVisible || descriptionView.isVisible) {
                attributionButton.isVisible = true
                attributionButton.setOnClickListener {
                    WikipediaAttribution.show(context, info.title, info.pageUrl, info.translated)
                }
            }
        }
    }

    /**
     * Show the expand row only when the description is genuinely cut off.
     * `lineCount` is only meaningful after layout, hence the post.
     */
    private fun revealToggleIfClipped() {
        descriptionView.post {
            if (!descriptionView.isVisible) return@post
            val clipped = descriptionView.lineCount > collapsedLines
            expandRow.isVisible = clipped
            if (!clipped) setExpanded(expanded = false)
        }
    }

    private fun setExpanded(expanded: Boolean) {
        descriptionView.maxLines = if (expanded) Int.MAX_VALUE else collapsedLines
        expandLabel.setText(
            if (expanded) R.string.species_info_collapse else R.string.species_info_expand,
        )
        expandChevron.setImageResource(
            if (expanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
        )
        expandRow.contentDescription = context.getString(
            R.string.species_info_toggle_content_description,
            nameView.text?.toString().orEmpty(),
            context.getString(
                if (expanded) R.string.species_info_collapse else R.string.species_info_expand,
            ),
        )
    }

    override fun onDetachedFromWindow() {
        // Detachment happens after Activity.onDestroy() when a result screen
        // finishes. Asking Glide for that destroyed Activity's RequestManager
        // crashes, so use the process-scoped manager only to clear the request.
        // Invalidating the token also prevents an enrichment response that was
        // already queued on the main thread from rebinding this detached view.
        bindToken++
        clearPhotoRequest()
        super.onDetachedFromWindow()
    }

    private fun clearPhotoRequest() {
        Glide.with(context.applicationContext).clear(photo)
    }

    private companion object {
        /** Matches the rounding the result-screen species photo used. */
        const val PHOTO_CORNER_RADIUS_PX = 24
    }
}
