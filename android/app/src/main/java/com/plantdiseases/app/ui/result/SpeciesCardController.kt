package com.plantdiseases.app.ui.result

import android.content.Intent
import android.net.Uri
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.bumptech.glide.Glide
import com.bumptech.glide.load.engine.DiskCacheStrategy
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityResultBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Owns the detected-species card: the stored PlantNet best match, a localized
 * Wikipedia blurb + lead photo, and a strip of other community scans of the
 * same species. Everything past the stored names is best-effort and stays
 * hidden on failure.
 */
class SpeciesCardController(
    private val activity: AppCompatActivity,
    private val binding: ActivityResultBinding,
) : KoinComponent {
    private val scanRepository: ScanRepository by inject()

    fun bind(scan: ScanEntity) {
        binding.cardResultFlowering.visibility = View.GONE
        binding.resultFloweringDetails.visibility = View.GONE
        binding.resultFloweringHeader.setOnClickListener(null)

        // The card stays hidden when we have neither a scientific nor a common
        // name (PlantNet wasn't configured, or every candidate scored zero).
        val sci = scan.speciesScientific?.takeIf { it.isNotBlank() }
        val common = scan.speciesCommon?.takeIf { it.isNotBlank() }
        if (sci == null && common == null) {
            binding.cardSpecies.visibility = View.GONE
            return
        }
        binding.cardSpecies.visibility = View.VISIBLE
        binding.tvSpeciesBest.text = common ?: sci.orEmpty()
        binding.tvSpeciesScientific.text = sci.orEmpty()
        binding.tvSpeciesScientific.visibility =
            if (sci.isNullOrBlank() || sci == common) View.GONE else View.VISIBLE
        val score = scan.speciesScore
        if (score != null && score > 0f) {
            binding.tvSpeciesScore.visibility = View.VISIBLE
            binding.tvSpeciesScore.text = activity.getString(
                R.string.species_card_score,
                (score * 100).toInt().coerceIn(0, 100),
            )
        } else {
            binding.tvSpeciesScore.visibility = View.GONE
        }
        // Alternates intentionally left empty — we only persist the best match
        // on the ScanEntity.
        binding.speciesAltsLayout.removeAllViews()

        // Enrich with a localized Wikipedia blurb + lead photo, and a strip of
        // other community scans of this species — both need the scientific name.
        binding.ivSpeciesPhoto.visibility = View.GONE
        binding.tvSpeciesBlurb.visibility = View.GONE
        binding.tvSpeciesSource.visibility = View.GONE
        binding.tvSpeciesCommunityLabel.visibility = View.GONE
        binding.rvSpeciesCommunity.visibility = View.GONE
        if (sci != null) loadEnrichment(sci, common)
    }

    private fun loadEnrichment(scientific: String, common: String?) {
        val isRu = LocaleHelper.isRussian(activity)
        val pseudoId = TelemetryIds.userPseudoId(activity)

        activity.lifecycleScope.launch {
            scanRepository.getSpeciesInfo(scientific, if (isRu) "ru" else "en").onSuccess { info ->
                if (!info.available) return@onSuccess
                // Localized display name: a Russian user gets the Russian
                // Wikipedia title when PlantNet only returned an English/blank
                // label (its RU common-name coverage is thin). PlantNet's name
                // is kept when it's already localized (Cyrillic), and the
                // English experience is unchanged.
                val localized = info.title?.takeIf { it.isNotBlank() }
                val displayName = when {
                    isRu && (common == null || !hasCyrillic(common)) ->
                        localized ?: common ?: scientific
                    else -> common ?: localized ?: scientific
                }
                binding.tvSpeciesBest.text = displayName
                binding.tvSpeciesScientific.visibility =
                    if (scientific.equals(displayName, ignoreCase = true)) {
                        View.GONE
                    } else {
                        View.VISIBLE
                    }
                info.extract?.takeIf { it.isNotBlank() }?.let {
                    binding.tvSpeciesBlurb.text = it
                    binding.tvSpeciesBlurb.visibility = View.VISIBLE
                }
                info.imageUrl?.takeIf { it.isNotBlank() }?.let { url ->
                    Glide.with(activity)
                        .load(com.plantdiseases.app.util.ServerConfig.photoUrl(url))
                        .diskCacheStrategy(DiskCacheStrategy.ALL)
                        .transform(CenterCrop(), RoundedCorners(24))
                        .into(binding.ivSpeciesPhoto)
                    binding.ivSpeciesPhoto.visibility = View.VISIBLE
                }
                val page = info.pageUrl
                if (!page.isNullOrBlank()) {
                    binding.tvSpeciesSource.visibility = View.VISIBLE
                    binding.tvSpeciesSource.text = activity.getString(
                        if (info.translated) {
                            R.string.species_source_wikipedia_translated
                        } else {
                            R.string.species_source_wikipedia
                        },
                    )
                    binding.tvSpeciesSource.setOnClickListener {
                        runCatching { activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(page))) }
                    }
                }
            }
        }

        loadFloweringProfile(scientific, common, if (isRu) "ru" else "en")

        val adapter = SimilarScansAdapter(
            displayName = { key -> DiseaseLabels.displayName(key, isRu) ?: key },
            // Compact preview cells (diagnosis + detected species), and tapping
            // opens a READ-ONLY view — no leaving second opinions from here.
            preview = true,
            onClick = { item ->
                if (item.scanId.isNotBlank() && pseudoId.isNotBlank()) {
                    activity.lifecycleScope.launch {
                        scanRepository.recordCommunityView(item.scanId, pseudoId)
                    }
                }
                CommunityOpinionSheet.showReadOnly(
                    activity.supportFragmentManager,
                    item,
                    DiseaseLabels.displayName(item.disease, isRu) ?: item.disease,
                )
            },
        )
        binding.rvSpeciesCommunity.layoutManager =
            LinearLayoutManager(activity, LinearLayoutManager.HORIZONTAL, false)
        binding.rvSpeciesCommunity.adapter = adapter
        activity.lifecycleScope.launch {
            scanRepository.communityScansBySpecies(scientific, pseudoId).onSuccess { resp ->
                if (resp.items.isNotEmpty()) {
                    adapter.submit(resp.items)
                    binding.tvSpeciesCommunityLabel.text =
                        activity.getString(R.string.species_community_others)
                    binding.tvSpeciesCommunityLabel.visibility = View.VISIBLE
                    binding.rvSpeciesCommunity.visibility = View.VISIBLE
                }
            }
        }
    }

    /**
     * The result card reuses the same cached species-profile repository path
     * as SpeciesDetailActivity. It starts collapsed so diagnosis content keeps
     * priority, while the header still exposes today's derived bloom state.
     */
    private fun loadFloweringProfile(scientific: String, common: String?, locale: String) {
        activity.lifecycleScope.launch {
            val profile = scanRepository
                .fetchSpeciesProfile(scientific, common, locale)
                .getOrNull()
            val flowering = profile?.flowering
            if (
                profile?.available != true ||
                flowering == null ||
                !binding.resultFloweringChart.setFlowering(flowering.monthlyIntensity)
            ) {
                binding.cardResultFlowering.visibility = View.GONE
                return@launch
            }

            val status = binding.resultFloweringChart.statusText() ?: return@launch
            binding.tvResultFloweringStatus.text = status

            val note = flowering.indoorNote.trim()
            binding.tvResultFloweringIndoorNote.text = note
            binding.tvResultFloweringIndoorNote.visibility =
                if (note.isBlank()) View.GONE else View.VISIBLE

            val conditions = profile.floweringConditions?.trim().orEmpty()
            binding.tvResultFloweringConditions.text = conditions
            binding.resultFloweringConditions.visibility =
                if (conditions.isBlank()) View.GONE else View.VISIBLE

            binding.cardResultFlowering.visibility = View.VISIBLE
            setFloweringExpanded(expanded = false, status = status)
            binding.resultFloweringHeader.setOnClickListener {
                setFloweringExpanded(
                    expanded = binding.resultFloweringDetails.visibility != View.VISIBLE,
                    status = status,
                )
            }
        }
    }

    private fun setFloweringExpanded(expanded: Boolean, status: String) {
        binding.resultFloweringDetails.visibility = if (expanded) View.VISIBLE else View.GONE
        binding.ivResultFloweringChevron.setImageResource(
            if (expanded) R.drawable.ic_chevron_up else R.drawable.ic_chevron_down,
        )
        val action = activity.getString(
            if (expanded) R.string.flowering_collapse else R.string.flowering_expand,
        )
        binding.resultFloweringHeader.contentDescription = activity.getString(
            R.string.flowering_toggle_content_description,
            activity.getString(R.string.flowering_title),
            status,
            action,
        )
    }

    /** Cyrillic presence means PlantNet already returned a Russian common
     *  name, so we keep it instead of overriding with the Wikipedia title. */
    private fun hasCyrillic(s: String): Boolean = s.any { it in 'Ѐ'..'ӿ' }
}
