package com.plantdiseases.app.ui.community

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.resource.bitmap.CenterCrop
import com.bumptech.glide.load.resource.bitmap.RoundedCorners
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.CommunityOpinion
import com.plantdiseases.app.data.model.CommunityTopDisease
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.ActivityMyScanRepliesBinding
import com.plantdiseases.app.util.DiseaseLabels
import com.plantdiseases.app.util.LocaleHelper
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import java.io.File
import javax.inject.Inject

/**
 * Read-only owner view of the second opinions on one of the caller's own
 * shared scans. A dedicated screen rather than a bottom sheet: your own
 * scan photo (the local file, not the community thumbnail), the model's
 * verdict, the live request status with Stop, the consensus graph, then
 * the thread. No input controls — you can't comment on your own scan.
 */
@AndroidEntryPoint
class MyScanRepliesActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMyScanRepliesBinding

    @Inject
    lateinit var scanRepository: ScanRepository
    private val isRu by lazy { LocaleHelper.isRussian(this) }
    private var scanId: String = ""

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleHelper.applyLocale(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        binding = ActivityMyScanRepliesBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.appBar.updatePadding(top = bars.top)
            binding.repliesScroll.updatePadding(bottom = bars.bottom)
            insets
        }
        binding.toolbar.setNavigationOnClickListener { finish() }

        scanId = intent.getStringExtra(EXTRA_SCAN_ID).orEmpty()
        if (scanId.isBlank()) {
            finish()
            return
        }
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            val scan = scanRepository.getScanByClientScanId(scanId)
            if (scan == null) {
                // Reachable from a persisted notification-centre row up to 30
                // days old, whose scan the user has since deleted (deleting a
                // scan doesn't cascade to the notification table). Say so
                // instead of flashing an empty toolbar and finishing silently.
                Toast.makeText(
                    this@MyScanRepliesActivity,
                    R.string.scan_no_longer_available,
                    Toast.LENGTH_SHORT,
                ).show()
                finish()
                return@launch
            }
            renderScan(scan)
            if (scanRepository.isAbstainedScan(scan)) {
                // A request persisted before abstention-aware uploads may still
                // point at a rejected raw argmax. Keep the photo/correction
                // context, but do not query or render disease-scoped opinions.
                binding.llRepliesStatus.visibility = View.GONE
                binding.tvRepliesVotesHeader.visibility = View.GONE
                binding.llRepliesGraph.visibility = View.GONE
                binding.tvRepliesListHeader.visibility = View.GONE
                binding.llRepliesList.removeAllViews()
                binding.tvRepliesEmpty.apply {
                    visibility = View.VISIBLE
                    text = getString(R.string.inconclusive_result_body)
                    isClickable = false
                    setOnClickListener(null)
                }
                return@launch
            }
            scanRepository.getCommunityOpinions(scanId)
                .onSuccess {
                    renderStatus(it.wantsOpinion, it.opinionRequestedAt)
                    renderThread(it.consensus, it.opinions)
                }
                .onFailure { renderThread(emptyList(), emptyList()) }
        }
    }

    private fun renderScan(scan: ScanEntity) {
        Glide.with(this)
            .load(File(scan.imagePath))
            .transform(CenterCrop(), RoundedCorners(24))
            .into(binding.ivRepliesPhoto)

        if (scanRepository.isAbstainedScan(scan)) {
            binding.tvRepliesDiagnosis.text = getString(R.string.inconclusive_diagnosis_name)
            binding.tvRepliesConfidence.visibility = View.GONE
            return
        }

        val diseaseKey = scanRepository.verdictClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')
        binding.tvRepliesDiagnosis.text = DiseaseLabels.displayName(diseaseKey, isRu) ?: scan.diseaseName
        if (scan.confidence in 0f..1f) {
            binding.tvRepliesConfidence.visibility = View.VISIBLE
            binding.tvRepliesConfidence.text = getString(
                R.string.community_opinion_confidence,
                Math.round(scan.confidence * 100),
            )
        }
    }

    /** Hours until the 72 h expiry, floored at 1 so it never reads "0 h
     *  left" while still live; null when the timestamp is unknown. */
    private fun hoursLeft(requestedAtSec: Long?): Int? {
        if (requestedAtSec == null || requestedAtSec <= 0L) return null
        val left = (requestedAtSec + 72L * 3600L) * 1000L - System.currentTimeMillis()
        if (left <= 0L) return null
        return ((left + 3_599_999L) / 3_600_000L).toInt().coerceIn(1, 72)
    }

    private fun renderStatus(wantsOpinion: Boolean, requestedAtSec: Long?) {
        if (!wantsOpinion) {
            binding.llRepliesStatus.visibility = View.GONE
            return
        }
        binding.llRepliesStatus.visibility = View.VISIBLE
        val hours = hoursLeft(requestedAtSec)
        binding.tvRepliesStatus.text = if (hours != null) {
            getString(R.string.second_opinion_sheet_status, hours)
        } else {
            getString(R.string.result_second_opinion_waiting)
        }
        binding.btnRepliesStopRequest.setOnClickListener {
            binding.btnRepliesStopRequest.isEnabled = false
            lifecycleScope.launch {
                scanRepository.cancelSecondOpinion(scanId)
                    .onSuccess {
                        Toast.makeText(this@MyScanRepliesActivity, R.string.second_opinion_stopped, Toast.LENGTH_SHORT).show()
                        binding.llRepliesStatus.visibility = View.GONE
                    }
                    .onFailure {
                        binding.btnRepliesStopRequest.isEnabled = true
                        Toast.makeText(this@MyScanRepliesActivity, R.string.community_opinion_error, Toast.LENGTH_SHORT).show()
                    }
            }
        }
    }

    private fun renderThread(consensus: List<CommunityTopDisease>, opinions: List<CommunityOpinion>) {
        val total = consensus.sumOf { it.count }
        if (consensus.isEmpty() || total <= 0) {
            binding.tvRepliesVotesHeader.visibility = View.GONE
            binding.llRepliesGraph.visibility = View.GONE
        } else {
            binding.tvRepliesVotesHeader.visibility = View.VISIBLE
            binding.llRepliesGraph.visibility = View.VISIBLE
            binding.llRepliesGraph.removeAllViews()
            consensus.sortedByDescending { it.count }.forEachIndexed { index, entry ->
                binding.llRepliesGraph.addView(
                    OpinionViews.buildVoteBar(this, entry, total, leading = index == 0),
                )
            }
        }

        binding.llRepliesList.removeAllViews()
        if (opinions.isEmpty()) {
            binding.tvRepliesListHeader.visibility = View.GONE
            binding.tvRepliesEmpty.visibility = View.VISIBLE
        } else {
            binding.tvRepliesEmpty.visibility = View.GONE
            binding.tvRepliesListHeader.visibility = View.VISIBLE
            binding.tvRepliesListHeader.text = getString(R.string.community_opinion_list_header, opinions.size)
            opinions.forEach { opinion ->
                binding.llRepliesList.addView(
                    OpinionViews.buildOpinionRow(this, opinion) {
                        it.authorToken?.let { token ->
                            startActivity(CommunityProfileActivity.intent(this, token))
                        }
                    },
                )
            }
        }
    }

    companion object {
        const val EXTRA_SCAN_ID = "extra_scan_id"

        fun intentFor(context: Context, scanId: String): Intent =
            Intent(context, MyScanRepliesActivity::class.java).putExtra(EXTRA_SCAN_ID, scanId)
    }
}
