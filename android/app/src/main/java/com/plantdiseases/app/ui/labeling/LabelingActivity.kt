package com.plantdiseases.app.ui.labeling

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.bumptech.glide.load.model.GlideUrl
import com.bumptech.glide.load.model.LazyHeaders
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.chip.Chip
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.LabelCard
import com.plantdiseases.app.data.model.LabelClueResponse
import com.plantdiseases.app.data.model.LabelStatsResponse
import com.plantdiseases.app.data.repository.LabelingRepository
import com.plantdiseases.app.databinding.ActivityLabelingBinding
import com.plantdiseases.app.util.ServerConfig
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Tinder-style admin data-labeling screen. The session token (issued by the
 * Profile password gate) is passed in-memory via the launching intent and is
 * never persisted. Cards are prefetched in batches; verdicts are batch-submitted.
 */
class LabelingActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_TOKEN = "label_token"
        private const val PREFETCH_AT = 3
        private const val BATCH = 5
        private const val SWIPE_DP = 110
    }

    private lateinit var binding: ActivityLabelingBinding
    private lateinit var repo: LabelingRepository
    private lateinit var token: String

    private val buffer = ArrayDeque<LabelCard>()
    private val pending = ArrayList<Pair<Long, String>>()
    private var current: LabelCard? = null
    private var selectedClass: String? = null // null = Any/random
    private var selectedSource: String? = null // null = All (server priority order)
    private var loading = false
    private var stats: LabelStatsResponse? = null

    private val sourceKeys = listOf("feedback", "scans")

    private val lang: String get() = if (Locale.getDefault().language == "ru") "ru" else "en"
    private val base: String get() = ServerConfig.baseUrl.trimEnd('/')

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLabelingBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // targetSdk 35 draws edge-to-edge: pad the toolbar under the status bar
        // and the button row above the navigation bar so neither is covered.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.toolbar.updatePadding(top = bars.top)
            binding.buttonBar.updatePadding(bottom = bars.bottom)
            insets
        }

        token = intent.getStringExtra(EXTRA_TOKEN).orEmpty()
        repo = LabelingRepository((application as PlantDiseasesApp).apiClient)
        if (token.isBlank()) {
            finish()
            return
        }

        binding.toolbar.setNavigationOnClickListener { exit() }
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_stats -> {
                    showStats()
                    true
                }
                R.id.action_export -> {
                    doExport()
                    true
                }
                else -> false
            }
        }
        binding.btnYes.setOnClickListener { verdict("yes") }
        binding.btnNo.setOnClickListener { verdict("no") }
        binding.btnNotSure.setOnClickListener { verdict("not_sure") }
        binding.clueCard.setOnClickListener { showClue() }
        setupSwipe()

        refreshStats(thenLoad = true)
    }

    // ── Source switcher (All ▸ Feedback ▸ Scans) ──────────
    private fun sourceLabel(key: String): String = when (key) {
        "feedback" -> getString(R.string.label_source_feedback)
        else -> getString(R.string.label_source_scans)
    }

    private fun buildSourceChips() {
        val s = stats ?: return
        binding.chipSources.removeAllViews()
        val all = Chip(this).apply {
            text = getString(R.string.label_source_all)
            isCheckable = true
            isChecked = selectedSource == null
            setOnClickListener { selectSource(null) }
        }
        binding.chipSources.addView(all)
        // Hide the row entirely against an older server (no sources block).
        if (s.sources.isEmpty()) return
        sourceKeys.forEach { key ->
            val pendingN = s.sources[key]?.pending ?: 0
            val chip = Chip(this).apply {
                text = "${sourceLabel(key)} ($pendingN)"
                isCheckable = true
                isChecked = selectedSource == key
                setOnClickListener { selectSource(key) }
            }
            binding.chipSources.addView(chip)
        }
    }

    private fun selectSource(src: String?) {
        selectedSource = src
        buffer.clear()
        current = null
        loadMore(reset = true)
    }

    // ── Class switcher ──────────────────────────────────────────────
    private fun buildChips() {
        val s = stats ?: return
        binding.chipClasses.removeAllViews()
        val any = Chip(this).apply {
            text = getString(R.string.label_any_class)
            isCheckable = true
            isChecked = selectedClass == null
            setOnClickListener { selectClass(null) }
        }
        binding.chipClasses.addView(any)
        s.perClass.entries
            .filter { it.value.weight > 0 }
            .sortedByDescending { it.value.remaining }
            .forEach { (cls, st) ->
                val chip = Chip(this).apply {
                    text = if (st.satisfied) "✓ $cls" else "$cls (${st.validated}/${st.weight})"
                    isCheckable = true
                    isEnabled = !st.satisfied // can't collect a satisfied class
                    isChecked = selectedClass == cls
                    setOnClickListener { selectClass(cls) }
                }
                binding.chipClasses.addView(chip)
            }
    }

    private fun selectClass(cls: String?) {
        selectedClass = cls
        buffer.clear()
        current = null
        loadMore(reset = true)
    }

    // ── Card flow ───────────────────────────────────────────────────
    private fun loadMore(reset: Boolean = false) {
        if (loading) return
        loading = true
        if (reset) binding.progress.visibility = View.VISIBLE
        lifecycleScope.launch {
            val res = repo.next(token, selectedClass, source = selectedSource)
            loading = false
            binding.progress.visibility = View.GONE
            res.onSuccess { cards ->
                buffer.addAll(cards)
                if (current == null) showNext()
            }.onFailure { showError(it) }
        }
    }

    private fun showNext() {
        current = if (buffer.isEmpty()) null else buffer.removeFirst()
        val card = current
        if (card == null) {
            binding.ivCard.setImageDrawable(null)
            binding.tvEmpty.visibility = View.VISIBLE
            binding.tvSourceBadge.visibility = View.GONE
            binding.tvFeedback.visibility = View.GONE
            updateClue(null)
            return
        }
        binding.tvEmpty.visibility = View.GONE
        binding.cardHolder.translationX = 0f
        binding.cardHolder.alpha = 1f
        Glide.with(this)
            .load(GlideUrl(base + card.imageUrl, authHeaders()))
            .into(binding.ivCard)
        // Why the model flagged this as confusing: its guess + the calibrated
        // alternatives. The model already auto-labeled the easy ones.
        binding.tvModel.text = if (card.modelPred != null) {
            val conf = ((card.modelConf ?: 0.0) * 100).toInt()
            val alts = card.modelSet.joinToString(", ").ifBlank { card.modelPred }
            getString(R.string.label_model_hint, card.modelPred, conf, alts)
        } else {
            ""
        }
        renderSourceBadge(card)
        renderFeedbackContext(card)
        updateClue(card.suggestedClass ?: card.klass)
        if (buffer.size < PREFETCH_AT) loadMore()
    }

    /** Corner badge: where the card came from (+ audit marker). */
    private fun renderSourceBadge(card: LabelCard) {
        val label = when (card.source) {
            "user_feedback" -> getString(R.string.label_source_feedback)
            "user_scan" -> getString(R.string.label_source_scans)
            else -> null
        }
        val text = listOfNotNull(
            label,
            if (card.audit) getString(R.string.label_audit_badge) else null,
        ).joinToString("  ·  ")
        binding.tvSourceBadge.text = text
        binding.tvSourceBadge.visibility = if (text.isBlank()) View.GONE else View.VISIBLE
    }

    /** Feedback cards: "user proposed X — model said Y" + the user's note. */
    private fun renderFeedbackContext(card: LabelCard) {
        val parts = ArrayList<String>()
        if (card.scanFeedback == false &&
            card.modelPred != null &&
            card.modelPred != card.klass
        ) {
            parts.add(getString(R.string.label_feedback_ctx, card.klass, card.modelPred))
        }
        card.feedbackNote?.takeIf { it.isNotBlank() }?.let { parts.add("“$it”") }
        binding.tvFeedback.text = parts.joinToString("\n")
        binding.tvFeedback.visibility = if (parts.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun verdict(v: String) {
        val card = current ?: return
        pending.add(card.id to v)
        if (pending.size >= BATCH) flush()
        showNext()
    }

    private fun flush() {
        if (pending.isEmpty()) return
        val batch = ArrayList(pending)
        pending.clear()
        lifecycleScope.launch {
            repo.submit(token, batch).onSuccess { refreshStats() }
                .onFailure { pending.addAll(batch) } // retry later
        }
    }

    // ── Clue ────────────────────────────────────────────────────────
    private fun updateClue(cls: String?) {
        if (cls == null) {
            binding.clueName.text = ""
            return
        }
        binding.clueName.text = cls
        lifecycleScope.launch {
            repo.clue(token, cls, lang).onSuccess { c ->
                binding.clueName.text = c.suggested.name ?: cls
                c.suggested.photoUrl?.let {
                    Glide.with(this@LabelingActivity)
                        .load(GlideUrl(base + it, authHeaders()))
                        .into(binding.clueImage)
                }
            }
        }
    }

    private fun showClue() {
        val cls = current?.suggestedClass ?: current?.klass ?: return
        lifecycleScope.launch {
            repo.clue(token, cls, lang).onSuccess { renderClueSheet(it) }
                .onFailure { showError(it) }
        }
    }

    private fun renderClueSheet(c: LabelClueResponse) {
        val root = verticalSheet()
        root.addView(header(c.suggested.name ?: c.suggested.klass))
        root.addView(sectionTitle(getString(R.string.label_pass)))
        c.pass.forEach { root.addView(bullet("✓  $it")) }
        root.addView(sectionTitle(getString(R.string.label_drop)))
        c.drop.forEach { root.addView(bullet("✗  $it")) }
        if (c.confusables.isNotEmpty()) {
            root.addView(sectionTitle(getString(R.string.label_similar)))
            c.confusables.forEach { root.addView(bullet("•  ${it.name ?: it.klass}")) }
        }
        showSheet(root)
    }

    // ── Stats ───────────────────────────────────────────────────────
    private fun refreshStats(thenLoad: Boolean = false) {
        lifecycleScope.launch {
            repo.stats(token).onSuccess {
                stats = it
                buildSourceChips()
                buildChips()
                renderProgressStrip()
                if (thenLoad) loadMore(reset = true)
            }.onFailure { if (thenLoad) showError(it) }
        }
    }

    private fun renderProgressStrip() {
        val s = stats ?: return
        val active = selectedClass
            ?: s.perClass.entries.firstOrNull { it.value.weight > 0 && !it.value.satisfied }?.key
        val st = active?.let { s.perClass[it] }
        binding.tvProgress.text = if (st != null) {
            getString(R.string.label_progress_fmt, active, st.validated, st.weight)
        } else {
            ""
        }
    }

    private fun showStats() {
        val s = stats ?: return
        val root = verticalSheet()
        root.addView(header(getString(R.string.label_stats)))
        if (s.sources.isNotEmpty()) {
            root.addView(sectionTitle(getString(R.string.label_stats_sources)))
            sourceKeys.forEach { key ->
                val st = s.sources[key] ?: return@forEach
                root.addView(
                    bullet(
                        "${sourceLabel(key)} — pending ${st.pending}, " +
                            "claimed ${st.claimed}, resolved ${st.resolved}",
                    )
                )
            }
            root.addView(sectionTitle(getString(R.string.label_stats_classes)))
        }
        s.perClass.entries.filter { it.value.weight > 0 }.forEach { (cls, st) ->
            val mark = if (st.satisfied) "  ✓" else ""
            root.addView(bullet("$cls — ${st.validated}/${st.weight}  (pending ${st.pending})$mark"))
        }
        showSheet(root)
    }

    private fun doExport() {
        lifecycleScope.launch {
            flush()
            repo.export(token).onSuccess { r ->
                MaterialAlertDialogBuilder(this@LabelingActivity)
                    .setTitle(R.string.label_export)
                    .setMessage(r.kaggleCommand ?: r.bundle)
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
            }.onFailure { showError(it) }
        }
    }

    // ── Swipe gesture on the card ───────────────────────────────────
    @SuppressLint("ClickableViewAccessibility")
    private fun setupSwipe() {
        val threshold = SWIPE_DP * resources.displayMetrics.density
        var downX = 0f
        binding.cardHolder.setOnTouchListener { v, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX
                    v.translationX = dx
                    v.alpha = 1f - (kotlin.math.abs(dx) / (threshold * 3f)).coerceIn(0f, 0.6f)
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    val dx = e.rawX - downX
                    when {
                        dx > threshold -> verdict("yes")
                        dx < -threshold -> verdict("no")
                        else -> {
                            v.animate().translationX(0f).alpha(1f).setDuration(150).start()
                        }
                    }
                    true
                }
                else -> false
            }
        }
    }

    private fun authHeaders() =
        LazyHeaders.Builder().addHeader("X-Label-Token", token).build()

    // ── Bottom-sheet helpers (programmatic to keep layout count low) ─
    private fun verticalSheet(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        val p = (16 * resources.displayMetrics.density).toInt()
        setPadding(p, p, p, p)
    }

    private fun showSheet(content: View) {
        val sv = ScrollView(this).apply { addView(content) }
        BottomSheetDialog(this).apply {
            setContentView(sv)
            show()
        }
    }

    private fun header(text: String) = TextView(this).apply {
        this.text = text
        textSize = 18f
        setTypeface(typeface, android.graphics.Typeface.BOLD)
        setPadding(0, 0, 0, 12)
    }

    private fun sectionTitle(text: String) = TextView(this).apply {
        this.text = text
        textSize = 15f
        setTypeface(typeface, android.graphics.Typeface.BOLD)
        setPadding(0, 16, 0, 4)
    }

    private fun bullet(text: String) = TextView(this).apply {
        this.text = text
        textSize = 14f
        setPadding(0, 4, 0, 4)
    }

    private fun showError(t: Throwable) {
        val msg = if (t is com.plantdiseases.app.data.repository.LabelingUnavailableException) {
            getString(R.string.label_unavailable)
        } else {
            t.message ?: getString(R.string.label_error)
        }
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
    }

    private fun exit() {
        flush()
        token = ""
        finish()
    }

    override fun onPause() {
        flush()
        super.onPause()
    }

    override fun onSupportNavigateUp(): Boolean {
        exit()
        return true
    }
}
