package com.plantdiseases.app.ui.garden

import android.os.Bundle
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
import android.widget.ScrollView
import android.widget.TextView
import androidx.fragment.app.FragmentManager
import androidx.core.os.bundleOf
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.GuideDataProvider
import com.plantdiseases.app.data.local.ChatMessageEntity
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.databinding.SheetDoctorChatBinding
import com.plantdiseases.app.ui.guide.GuideDetailSheet
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class DoctorChatSheet : BottomSheetDialogFragment() {

    private var _binding: SheetDoctorChatBinding? = null
    private val binding get() = _binding!!

    private lateinit var plant: PlantEntity

    private lateinit var messagesLayout: LinearLayout
    private lateinit var scrollView: ScrollView
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
                val txt = inputField.text?.toString()?.trim().orEmpty()
                if (txt.isNotEmpty()) sendMessage(txt)
                true
            } else false
        }
        ViewCompat.setOnApplyWindowInsetsListener(binding.chatInputRow) { v, insets ->
            val bottomInset = insets.getInsets(WindowInsetsCompat.Type.systemBars()).bottom
            v.setPadding(20.dp(), 8.dp(), 20.dp(), 12.dp() + bottomInset)
            insets
        }

        val behavior = BottomSheetBehavior.from(view.parent as View)
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
        behavior.peekHeight = 600.dp()

        // Resolve the plant. Normal path: show() set it directly. After
        // process death the system recreates the fragment WITHOUT calling
        // show(), leaving `plant` uninitialised — fall back to the id
        // stashed in arguments and reload from Room (chat history is
        // persisted, so the session restores rather than crashing).
        if (::plant.isInitialized) {
            bindPlant()
        } else {
            val savedId = arguments?.getLong(ARG_PLANT_ID, -1L) ?: -1L
            if (savedId <= 0L) {
                dismissAllowingStateLoss()
                return
            }
            lifecycleScope.launch {
                val restored = (requireActivity().application as PlantDiseasesApp)
                    .plantRepository.getPlant(savedId)
                if (restored == null || _binding == null) {
                    dismissAllowingStateLoss()
                    return@launch
                }
                plant = restored
                bindPlant()
            }
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    /** Bind plant-dependent views. Safe to call only once [plant] is set. */
    private fun bindPlant() {
        binding.chatPlantName.text = plant.name
        loadMessages()
    }

    private fun loadMessages() {
        val repo = (requireActivity().application as PlantDiseasesApp).plantRepository
        lifecycleScope.launch {
            val msgs = repo.getChatMessages(plant.id)
            if (msgs.isNotEmpty()) {
                suggestionsContainer.visibility = View.GONE
                msgs.forEach { addMessageBubble(it.role, it.content, it.timestamp) }
                scrollToBottom()
            }
        }
    }

    private fun sendMessage(text: String) {
        // Guard the brief window during a process-death restore where the
        // view exists but the plant hasn't finished reloading yet.
        if (!::plant.isInitialized) return
        inputField.setText("")
        suggestionsContainer.visibility = View.GONE

        val app = requireActivity().application as PlantDiseasesApp
        val plantRepo = app.plantRepository
        val scanRepo = app.scanRepository
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

        lifecycleScope.launch {
            // Persist the user message before the network round-trip
            // so it survives a server timeout / process death.
            plantRepo.saveChatMessage(
                ChatMessageEntity(plantId = plant.id, role = "user", content = text, timestamp = now),
            )

            // Pull the latest scan for this plant (if any) and ship a
            // compact diagnosis context. The server caps this dict at
            // 8 KB on the wire — we keep it tiny on purpose.
            val latestScan = plantRepo.getLatestScanForPlant(plant.id)
            val diagnosis: Map<String, Any?>? = latestScan?.let {
                val classKey = scanRepo.topClassKey(it)
                    ?: it.diseaseName.lowercase().replace(' ', '_')
                mapOf(
                    "class_name" to classKey,
                    "disease_name" to it.diseaseName,
                    "confidence" to it.confidence.toDouble(),
                    "severity" to it.severity,
                    "is_healthy" to it.isHealthy,
                    "warnings" to scanRepo.parseWarnings(it.warnings),
                    "plant_name" to plant.name,
                    "species" to (plant.scientificName ?: plant.species),
                )
            } ?: mapOf(
                "plant_name" to plant.name,
                "species" to (plant.scientificName ?: plant.species),
            )

            val request = ChatRequest(
                sessionId = "plant_${plant.id}",
                message = text,
                locale = if (isRu) "ru" else "en",
                diagnosis = diagnosis,
            )
            val result = scanRepo.sendChat(request)

            // Drop the thinking placeholder regardless of outcome.
            thinkingView?.let { messagesLayout.removeView(it) }
            thinkingView = null

            val replyTime = System.currentTimeMillis()
            val replyText = result.fold(
                onSuccess = { resp ->
                    when {
                        resp.available && resp.reply.isNotBlank() -> resp.reply
                        // Server declined the turn (no API key, budget,
                        // empty reply, etc.). Surface its `fallback`
                        // verbatim if present, else a translated reason.
                        !resp.fallback.isNullOrBlank() -> resp.fallback
                        else -> formatChatUnavailable(resp.reason, isRu)
                    }
                },
                onFailure = { err -> formatChatNetworkError(err, isRu) },
            )

            addMessageBubble("bot", replyText, replyTime)
            scrollToBottom()
            plantRepo.saveChatMessage(
                ChatMessageEntity(
                    plantId = plant.id,
                    role = "bot",
                    content = replyText,
                    timestamp = replyTime,
                ),
            )
        }
    }

    /** "Server replied 200 OK with available=false" — translate the reason. */
    private fun formatChatUnavailable(reason: String?, isRu: Boolean): String {
        if (isRu) {
            return when (reason) {
                "session_turn_cap" ->
                    "Лимит сообщений в этой сессии исчерпан. Очистите чат, чтобы начать заново."
                "session_token_cap" ->
                    "Бюджет токенов сессии исчерпан. Очистите чат, чтобы начать заново."
                "no_api_key" ->
                    "AI Plant Doctor временно недоступен (сервер не настроен). Используйте раздел «Лечение» в карточке диагноза."
                "message_too_long", "empty_message" ->
                    "Сообщение не отправлено. Сократите запрос и попробуйте снова."
                "empty_reply", "api_error" ->
                    "Не удалось получить ответ от ассистента. Попробуйте отправить сообщение ещё раз."
                else -> "AI Plant Doctor пока недоступен. Попробуйте позже."
            }
        }
        return when (reason) {
            "session_turn_cap" ->
                "You hit the per-session message cap. Clear the chat to start over."
            "session_token_cap" ->
                "You hit the per-session token budget. Clear the chat to start over."
            "no_api_key" ->
                "AI Plant Doctor is offline (server not configured). See the diagnosis Treatment list for now."
            "message_too_long", "empty_message" ->
                "The message wasn't sent. Shorten it and try again."
            "empty_reply", "api_error" ->
                "The assistant couldn't reply. Try sending the message again."
            else -> "AI Plant Doctor is unavailable right now. Try again later."
        }
    }

    /** Network-level failure — the server never gave us an HTTP response. */
    private fun formatChatNetworkError(err: Throwable, isRu: Boolean): String {
        val name = err::class.simpleName ?: ""
        return if (isRu) {
            when {
                "NoNetwork" in name -> "Нет интернет-соединения. Подключитесь к сети и попробуйте снова."
                "Timeout" in name -> "Сервер не отвечает. Попробуйте отправить сообщение ещё раз."
                "RateLimit" in name -> "Слишком частые запросы. Подождите несколько секунд и повторите."
                else -> "Не удалось связаться с сервером. Попробуйте позже."
            }
        } else {
            when {
                "NoNetwork" in name -> "No internet connection. Reconnect and try again."
                "Timeout" in name -> "The server didn't respond. Try sending the message again."
                "RateLimit" in name -> "Too many requests. Wait a few seconds and try again."
                else -> "Couldn't reach the server. Try again later."
            }
        }
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

        bubble.addView(TextView(requireContext()).apply {
            text = applyMarkdown(content)
            textSize = 15f
            maxWidth = bubbleMaxWidth
            setTextColor(requireContext().getColor(R.color.chat_bubble_text))
            setLineSpacing(3.dp().toFloat(), 1f)
            // Make [[doc:…]] citation links tappable.
            movementMethod = LinkMovementMethod.getInstance()
        })
        bubble.addView(TextView(requireContext()).apply {
            text = formatTime(timestamp)
            textSize = 10f
            setTextColor(requireContext().getColor(R.color.chat_bubble_text_secondary))
            alpha = 0.85f
            gravity = Gravity.END
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = 4.dp() }
        })

        outer.addView(bubble)
        return outer
    }

    private fun applyMarkdown(text: String): CharSequence {
        // One pass over **bold** and the AI doctor's doc-citation tokens
        // [[doc:<class_key>]]. A token renders as the matching guide article's
        // title, tappable to open the offline guide; unknown keys are dropped.
        val pattern = Regex("""\*\*(.+?)\*\*|\[\[doc:([a-z0-9_]+)]]""")
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
                        start, sb.length, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
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
                            start, sb.length, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
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
        if (!::plant.isInitialized) return
        val repo = (requireActivity().application as PlantDiseasesApp).plantRepository
        lifecycleScope.launch {
            repo.clearChat(plant.id)
            messagesLayout.removeAllViews()
            suggestionsContainer.visibility = View.VISIBLE
        }
    }

    private fun scrollToBottom() {
        scrollView.post { scrollView.fullScroll(ScrollView.FOCUS_DOWN) }
    }

    private fun formatTime(timestamp: Long): String =
        SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(timestamp))

    private fun Int.dp(): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, this.toFloat(), resources.displayMetrics
    ).toInt()

    companion object {
        private const val TAG = "DoctorChatSheet"
        private const val ARG_PLANT_ID = "arg_plant_id"

        fun show(fm: FragmentManager, plant: PlantEntity) {
            val sheet = DoctorChatSheet().apply {
                this.plant = plant
                // Stash the id so the sheet can self-heal after process death.
                arguments = bundleOf(ARG_PLANT_ID to plant.id)
            }
            sheet.show(fm, TAG)
        }
    }
}
