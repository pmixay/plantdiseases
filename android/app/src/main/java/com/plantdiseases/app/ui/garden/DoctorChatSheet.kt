package com.plantdiseases.app.ui.garden

import android.os.Bundle
import android.text.InputFilter
import android.text.Spannable
import android.text.SpannableStringBuilder
import android.text.TextPaint
import android.text.method.LinkMovementMethod
import android.text.style.ClickableSpan
import android.text.style.StyleSpan
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.FragmentManager
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.widget.NestedScrollView
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.local.ChatMessageEntity
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.local.ScanChatMessageEntity
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.databinding.SheetDoctorChatBinding
import com.plantdiseases.app.ui.guide.GuideDetailSheet
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.localizedSpeciesName
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import javax.inject.Inject

@AndroidEntryPoint
class DoctorChatSheet : BottomSheetDialogFragment() {

    private var _binding: SheetDoctorChatBinding? = null
    private val binding get() = _binding!!

    @Inject
    lateinit var plantRepository: PlantRepository

    @Inject
    lateinit var scanRepository: ScanRepository

    // The chat runs in one of two modes:
    //   * plant mode — launched from the Garden plant detail; chat history
    //     is persisted in Room (chat_messages), keyed by the plant id.
    //   * scan mode — launched from the after-scan result screen before the
    //     plant is in the garden; history is persisted in Room
    //     (scan_chat_messages), keyed by the scan id, so reopening the chat
    //     for the same scan restores the transcript.
    private var plant: PlantEntity? = null
    private var scan: ScanEntity? = null

    /** Plant id to persist chat under, or null for a scan session. */
    private val persistPlantId: Long?
        get() = plant?.id

    /** Scan id to persist chat under, or null for a plant session. */
    private val persistScanId: Long?
        get() = if (plant == null) scan?.id else null

    /** Stable per-owner key — also the base of the server session id. */
    private val ownerKey: String
        get() = plant?.let { "plant_${it.id}" } ?: "scan_${scan?.id ?: 0L}"

    /**
     * Stable per-session id sent to the server for turn/token budgeting.
     * Reopening the same plant/scan chat reuses the same id so the server's
     * per-session budget carries over; clearing the chat bumps a persisted
     * epoch so a fresh session (and fresh budget) starts.
     */
    private val sessionId: String
        get() {
            val epoch = sessionEpoch(requireContext(), ownerKey)
            return if (epoch == 0) ownerKey else "${ownerKey}_r$epoch"
        }

    /** True while a reply is in flight — send is locked until it lands. */
    private var isSending = false
    private var sendJob: Job? = null
    private var activeSendToken: Any? = null

    /** Diagnosis-payload label (`plant_name`, sent to the chat endpoint for a
     *  scan session) — kept distinct from the header subtitle
     *  ([resolveSubtitle]) so localising the header never changes what the
     *  server sees. */
    private val displayName: String
        get() = plant?.name
            ?: scan?.let { s ->
                s.speciesCommon?.takeIf { it.isNotBlank() }
                    ?: s.speciesScientific?.takeIf { it.isNotBlank() }
                    ?: s.diseaseName
            }
            ?: ""

    private lateinit var messagesLayout: LinearLayout
    private lateinit var scrollView: NestedScrollView
    private lateinit var suggestionsContainer: LinearLayout
    private lateinit var inputField: TextInputEditText
    private var thinkingView: View? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = SheetDoctorChatBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        messagesLayout = binding.chatMessages
        scrollView = binding.chatScroll
        suggestionsContainer = binding.chatSuggestions
        inputField = binding.chatInput
        inputField.filters = arrayOf(InputFilter.LengthFilter(TEXT_FIELD_MAX_LENGTH))

        // Suggestion chips — label comes from the layout; tapping one
        // sends it verbatim.
        listOf(binding.chatChip1, binding.chatChip2, binding.chatChip3).forEach { chip ->
            chip.setOnClickListener {
                Haptics.tick(requireContext())
                sendMessage(chip.text.toString())
            }
        }

        binding.chatClearBtn.setOnClickListener { clearChat() }
        binding.chatSendBtn.setOnClickListener {
            val txt = inputField.text?.toString()?.trim().orEmpty()
            if (txt.isNotEmpty()) {
                Haptics.tick(requireContext())
                sendMessage(txt)
            }
        }
        inputField.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                // Enter-to-send is ignored while a reply is in flight.
                val txt = inputField.text?.toString()?.trim().orEmpty()
                if (txt.isNotEmpty() && !isSending) sendMessage(txt)
                true
            } else {
                false
            }
        }
        ViewCompat.setOnApplyWindowInsetsListener(binding.chatInputRow) { v, insets ->
            val bottomInset = insets.getInsets(WindowInsetsCompat.Type.systemBars()).bottom
            v.setPadding(20.dp(), 8.dp(), 20.dp(), 12.dp() + bottomInset)
            insets
        }

        // Open near-full height so the conversation area is large: pin the
        // sheet to ~90% of the screen, expanded, with no collapsed stop.
        val sheet = view.parent as View
        val behavior = BottomSheetBehavior.from(sheet)
        val maxHeight = (resources.displayMetrics.heightPixels * 0.9f).toInt()
        sheet.layoutParams = sheet.layoutParams.apply { height = maxHeight }
        behavior.maxHeight = maxHeight
        behavior.peekHeight = maxHeight
        behavior.skipCollapsed = true
        behavior.state = BottomSheetBehavior.STATE_EXPANDED

        // Resolve the session. Normal path: show() set plant/scan directly.
        // After process death the system recreates the fragment WITHOUT
        // calling show(), so fall back to the ids stashed in arguments and
        // reload from Room (both plant and scan chat history are persisted,
        // so either restores).
        if (plant != null || scan != null) {
            bindSession()
        } else {
            val savedPlantId = arguments?.getLong(ARG_PLANT_ID, -1L) ?: -1L
            val savedScanId = arguments?.getLong(ARG_SCAN_ID, -1L) ?: -1L
            if (savedPlantId <= 0L && savedScanId <= 0L) {
                dismissAllowingStateLoss()
                return
            }
            lifecycleScope.launch {
                when {
                    savedPlantId > 0L -> plant = plantRepository.getPlant(savedPlantId)
                    savedScanId > 0L -> scan = scanRepository.getScanById(savedScanId)
                }
                if ((plant == null && scan == null) || _binding == null) {
                    dismissAllowingStateLoss()
                    return@launch
                }
                bindSession()
            }
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    /** Bind session-dependent views. Safe once [plant] or [scan] is set. */
    private fun bindSession() {
        binding.chatPlantName.text = resolveSubtitle(plant, scan, requireContext())
        loadMessages()
    }

    private fun loadMessages() {
        val pid = persistPlantId
        val sid = persistScanId
        lifecycleScope.launch {
            val msgs: List<Triple<String, String, Long>> = when {
                pid != null -> plantRepository.getChatMessages(pid)
                    .map { Triple(it.role, it.content, it.timestamp) }
                sid != null -> scanRepository.getChatMessages(sid)
                    .map { Triple(it.role, it.content, it.timestamp) }
                else -> emptyList()
            }
            if (msgs.isNotEmpty() && _binding != null) {
                suggestionsContainer.visibility = View.GONE
                msgs.forEach { (role, content, ts) -> addMessageBubble(role, content, ts) }
                scrollToBottom()
            }
        }
    }

    /** Lock / unlock the send controls while a reply is in flight. */
    private fun setSending(sending: Boolean) {
        isSending = sending
        _binding?.chatSendBtn?.isEnabled = !sending
        _binding?.let { b ->
            listOf(b.chatChip1, b.chatChip2, b.chatChip3).forEach { it.isEnabled = !sending }
        }
    }

    private fun sendMessage(text: String) {
        // Guard the brief window during a process-death restore where the
        // view exists but the session hasn't finished reloading yet — and
        // never allow a second send while a reply is still in flight.
        if (plant == null && scan == null) return
        if (isSending) return
        setSending(true)
        inputField.setText("")
        suggestionsContainer.visibility = View.GONE

        val plantRepo = plantRepository
        val scanRepo = scanRepository
        val isRu = LocaleHelper.isRussian(requireContext())
        val now = System.currentTimeMillis()

        addMessageBubble("user", text, now)
        scrollToBottom()

        // Thinking placeholder — removed once the server replies (or
        // the request fails).
        val thinkingBubble = buildBubble(
            getString(R.string.doctor_thinking),
            isUser = false,
            timestamp = now,
        )
        thinkingView = thinkingBubble
        messagesLayout.addView(thinkingBubble)
        scrollToBottom()

        val pid = persistPlantId
        val sid = persistScanId
        // Freeze the server session for this turn. Clear-chat may bump the
        // persisted epoch while this job is suspended, but an older request
        // must never migrate into (and consume budget from) the new session.
        val requestSessionId = sessionId

        val sendToken = Any()
        activeSendToken = sendToken
        sendJob = lifecycleScope.launch {
            try {
                // Persist the user message before the network round-trip so it
                // survives a server timeout / process death.
                when {
                    pid != null -> plantRepo.saveChatMessage(
                        ChatMessageEntity(plantId = pid, role = "user", content = text, timestamp = now),
                    )
                    sid != null -> scanRepo.saveChatMessage(
                        ScanChatMessageEntity(scanId = sid, role = "user", content = text, timestamp = now),
                    )
                }

                // Build a compact diagnosis context. The server caps this dict at
                // 8 KB on the wire — we keep it tiny on purpose. Plant mode pulls
                // the latest scan for the plant; scan mode uses the scan directly.
                val diagnosis: Map<String, Any?>? = plant?.let { p ->
                    val latestScan = plantRepo.getLatestScanForPlant(p.id)
                    latestScan?.let {
                        val classKey = scanRepo.topClassKey(it)
                            ?: it.diseaseName.lowercase().replace(' ', '_')
                        mapOf(
                            "class_name" to classKey,
                            "disease_name" to it.diseaseName,
                            "confidence" to it.confidence.toDouble(),
                            "severity" to it.severity,
                            "is_healthy" to it.isHealthy,
                            "warnings" to scanRepo.parseWarnings(it.warnings),
                            "plant_name" to p.name,
                            "species" to (p.scientificName ?: p.species),
                        )
                    } ?: mapOf(
                        "plant_name" to p.name,
                        "species" to (p.scientificName ?: p.species),
                    )
                } ?: scan?.let { s ->
                    val classKey = scanRepo.topClassKey(s)
                        ?: s.diseaseName.lowercase().replace(' ', '_')
                    mapOf(
                        "class_name" to classKey,
                        "disease_name" to s.diseaseName,
                        "confidence" to s.confidence.toDouble(),
                        "severity" to s.severity,
                        "is_healthy" to s.isHealthy,
                        "warnings" to scanRepo.parseWarnings(s.warnings),
                        "plant_name" to displayName,
                        "species" to (s.speciesScientific ?: s.speciesCommon),
                    )
                }

                val request = ChatRequest(
                    sessionId = requestSessionId,
                    message = text,
                    locale = if (isRu) "ru" else "en",
                    diagnosis = diagnosis,
                )
                val result = scanRepo.sendChat(request)

                removeThinkingBubble()

                val replyTime = System.currentTimeMillis()
                val replyText = result.fold(
                    onSuccess = { resp ->
                        when {
                            resp.available && resp.reply.isNotBlank() -> resp.reply
                            // Server declined the turn (no API key, budget,
                            // empty reply, etc.). Surface its `fallback`
                            // verbatim if present, else a translated reason.
                            !resp.fallback.isNullOrBlank() -> resp.fallback
                            else -> formatChatUnavailable(resp.reason)
                        }
                    },
                    onFailure = { err -> formatChatNetworkError(err) },
                )

                if (_binding != null) {
                    addMessageBubble("bot", replyText, replyTime)
                    scrollToBottom()
                }
                when {
                    pid != null -> plantRepo.saveChatMessage(
                        ChatMessageEntity(
                            plantId = pid,
                            role = "bot",
                            content = replyText,
                            timestamp = replyTime,
                        ),
                    )
                    sid != null -> scanRepo.saveChatMessage(
                        ScanChatMessageEntity(
                            scanId = sid,
                            role = "bot",
                            content = replyText,
                            timestamp = replyTime,
                        ),
                    )
                }
            } finally {
                // Clear may have replaced the active token before cancelling
                // this job. In that case the clear coroutine owns the controls
                // and unlocks them only after the transcript is durably gone.
                if (activeSendToken === sendToken) {
                    removeThinkingBubble()
                    activeSendToken = null
                    sendJob = null
                    setSending(false)
                }
            }
        }
    }

    private fun removeThinkingBubble() {
        thinkingView?.let { view ->
            (view.parent as? ViewGroup)?.removeView(view)
        }
        thinkingView = null
    }

    /** "Server replied 200 OK with available=false" — translate the reason.
     *  Localised via string resources (RU copy lives in values-ru). */
    private fun formatChatUnavailable(reason: String?): String = getString(
        when (reason) {
            "session_turn_cap" -> R.string.chat_err_turn_cap
            "session_token_cap" -> R.string.chat_err_token_cap
            "no_api_key" -> R.string.chat_err_no_api_key
            "message_too_long", "empty_message" -> R.string.chat_err_message_rejected
            "empty_reply", "api_error" -> R.string.chat_err_no_reply
            else -> R.string.chat_err_unavailable
        },
    )

    /** Network-level failure — the server never gave us an HTTP response. */
    private fun formatChatNetworkError(err: Throwable): String {
        val name = err::class.simpleName ?: ""
        return getString(
            when {
                "NoNetwork" in name -> R.string.chat_err_no_network
                "Timeout" in name -> R.string.chat_err_timeout
                "RateLimit" in name -> R.string.chat_err_rate_limit
                else -> R.string.chat_err_generic
            },
        )
    }

    private fun addMessageBubble(role: String, content: String, timestamp: Long) {
        val isUser = role == "user"
        messagesLayout.addView(buildBubble(content, isUser, timestamp))
    }

    private fun buildBubble(content: String, isUser: Boolean, timestamp: Long): View {
        val outer = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = if (isUser) Gravity.END else Gravity.START
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = 10.dp() }
        }

        val bubbleMaxWidth = (resources.displayMetrics.widthPixels * 0.78f).toInt()
        val bubble = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(
                if (isUser) R.drawable.bg_chat_bubble_user else R.drawable.bg_chat_bubble_bot
            )
            setPadding(14.dp(), 10.dp(), 14.dp(), 10.dp())
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }

        bubble.addView(
            TextView(requireContext()).apply {
                text = applyMarkdown(content)
                textSize = 15f
                maxWidth = bubbleMaxWidth
                setTextColor(requireContext().getColor(R.color.chat_bubble_text))
                setLineSpacing(3.dp().toFloat(), 1f)
                // Make [[doc:…]] citation links tappable.
                movementMethod = LinkMovementMethod.getInstance()
            }
        )
        bubble.addView(
            TextView(requireContext()).apply {
                text = formatTime(timestamp)
                textSize = 10f
                setTextColor(requireContext().getColor(R.color.chat_bubble_text_secondary))
                alpha = 0.85f
                gravity = Gravity.END
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = 4.dp() }
            }
        )

        outer.addView(bubble)
        return outer
    }

    private fun applyMarkdown(text: String): CharSequence {
        // One pass over **bold** and the AI doctor's doc-citation tokens
        // [[doc:<class_key>]]. A token renders as the matching guide article's
        // title, tappable to open the offline guide; unknown keys are dropped.
        // The model is asked for double brackets but a reasoning model often
        // drifts to the single-bracket [doc:key] form, so accept either — an
        // unmatched token would otherwise leak into the bubble as literal text.
        val pattern = Regex("""\*\*(.+?)\*\*|\[\[?doc:([a-z0-9_]+)]]?""")
        if (!pattern.containsMatchIn(text)) return text

        val isRu = LocaleHelper.isRussian(requireContext())
        val linkColor = requireContext().getColor(R.color.primary)
        val sb = SpannableStringBuilder()
        var last = 0
        for (m in pattern.findAll(text)) {
            sb.append(text.substring(last, m.range.first))
            val bold = m.groupValues[1]
            val docKey = m.groupValues[2]
            when {
                bold.isNotEmpty() -> {
                    val start = sb.length
                    sb.append(bold)
                    sb.setSpan(
                        StyleSpan(android.graphics.Typeface.BOLD),
                        start,
                        sb.length,
                        Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
                    )
                }
                docKey.isNotEmpty() -> {
                    val guideId = GuideDataProvider.guideIdForDisease(docKey)
                    val title = guideId?.let { id ->
                        GuideDataProvider.getGuideItems().firstOrNull { it.id == id }
                            ?.let { if (isRu) it.titleRu else it.titleEn }
                    }
                    if (guideId != null && title != null) {
                        val start = sb.length
                        sb.append(title)
                        sb.setSpan(
                            object : ClickableSpan() {
                                override fun onClick(widget: View) {
                                    GuideDetailSheet.newInstance(guideId)
                                        .show(parentFragmentManager, "guide_detail")
                                }
                                override fun updateDrawState(ds: TextPaint) {
                                    ds.color = linkColor
                                    ds.isUnderlineText = true
                                }
                            },
                            start,
                            sb.length,
                            Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
                        )
                    }
                }
            }
            last = m.range.last + 1
        }
        sb.append(text.substring(last))
        return sb
    }

    private fun clearChat() {
        if (plant == null && scan == null) return
        val pid = persistPlantId
        val sid = persistScanId
        val inFlight = sendJob
        activeSendToken = null
        sendJob = null
        setSending(true)
        _binding?.chatClearBtn?.isEnabled = false
        removeThinkingBubble()
        // Start a fresh server session so the per-session budget resets
        // alongside the wiped transcript.
        bumpSessionEpoch(requireContext(), ownerKey)
        lifecycleScope.launch {
            try {
                withContext(NonCancellable) {
                    // Wait for the cancelled request (including its local user
                    // message write) to stop before clearing Room. Otherwise that
                    // write or the old bot reply can land after DELETE and make a
                    // supposedly cleared transcript reappear. Finish the Room
                    // clear even if the sheet is dismissed while this is running.
                    inFlight?.cancelAndJoin()
                    when {
                        pid != null -> plantRepository.clearChat(pid)
                        sid != null -> scanRepository.clearChat(sid)
                    }
                }
                if (_binding != null) {
                    messagesLayout.removeAllViews()
                    suggestionsContainer.visibility = View.VISIBLE
                }
            } finally {
                _binding?.chatClearBtn?.isEnabled = true
                setSending(false)
            }
        }
    }

    private fun scrollToBottom() {
        scrollView.post { scrollView.fullScroll(View.FOCUS_DOWN) }
    }

    private fun formatTime(timestamp: Long): String =
        SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(timestamp))

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP,
        this.toFloat(),
        resources.displayMetrics
    ).toInt()

    companion object {
        private const val TEXT_FIELD_MAX_LENGTH = 1024
        private const val TAG = "DoctorChatSheet"
        private const val ARG_PLANT_ID = "arg_plant_id"
        private const val ARG_SCAN_ID = "arg_scan_id"
        private const val PREFS_NAME = "plantdiseases_prefs"
        private const val KEY_SESSION_EPOCH_PREFIX = "chat_session_epoch_"

        /** Per-owner "clear count". 0 (the default) keeps the legacy
         *  un-suffixed session id so pre-existing sessions keep their
         *  server-side budget; each clear bumps it to mint a new id. */
        private fun sessionEpoch(context: android.content.Context, ownerKey: String): Int =
            context.getSharedPreferences(PREFS_NAME, android.content.Context.MODE_PRIVATE)
                .getInt(KEY_SESSION_EPOCH_PREFIX + ownerKey, 0)

        private fun bumpSessionEpoch(context: android.content.Context, ownerKey: String) {
            val prefs = context.getSharedPreferences(
                PREFS_NAME,
                android.content.Context.MODE_PRIVATE,
            )
            prefs.edit()
                .putInt(
                    KEY_SESSION_EPOCH_PREFIX + ownerKey,
                    prefs.getInt(KEY_SESSION_EPOCH_PREFIX + ownerKey, 0) + 1,
                )
                .apply()
        }

        /** Header subtitle — locale-aware plant/species identity, distinct
         *  from [displayName]: the scan branch prefers a common name that is
         *  actually written in the UI language, falling back to the
         *  locale-neutral scientific name and then the raw disease name, so a
         *  Russian user is never shown an English-only common name (mirrors
         *  [localizedSpeciesName], used the same way for the plant-detail
         *  hero and species card). */
        internal fun resolveSubtitle(
            plant: PlantEntity?,
            scan: ScanEntity?,
            context: android.content.Context,
        ): String = plant?.name
            ?: scan?.let { s ->
                localizedSpeciesName(
                    s.speciesScientific,
                    s.speciesCommon,
                    LocaleHelper.isRussian(context),
                ) ?: s.diseaseName
            }
            ?: ""

        /** Plant session — persistent chat keyed by the garden plant. */
        fun show(fm: FragmentManager, plant: PlantEntity) {
            val sheet = DoctorChatSheet().apply {
                this.plant = plant
                // Stash the id so the sheet can self-heal after process death.
                arguments = Bundle().apply { putLong(ARG_PLANT_ID, plant.id) }
            }
            sheet.show(fm, TAG)
        }

        /** Scan session — chat from the after-scan result screen, carrying
         *  the scan's diagnosis context; history persists per scan. */
        fun show(fm: FragmentManager, scan: ScanEntity) {
            val sheet = DoctorChatSheet().apply {
                this.scan = scan
                arguments = Bundle().apply { putLong(ARG_SCAN_ID, scan.id) }
            }
            sheet.show(fm, TAG)
        }
    }
}
