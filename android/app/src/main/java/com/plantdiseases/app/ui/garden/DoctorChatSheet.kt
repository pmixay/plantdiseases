package com.plantdiseases.app.ui.garden

import android.os.Bundle
import android.text.Spannable
import android.text.SpannableString
import android.text.style.StyleSpan
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.fragment.app.FragmentManager
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import com.google.android.material.button.MaterialButton
import com.google.android.material.chip.Chip
import com.google.android.material.textfield.TextInputEditText
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.local.ChatMessageEntity
import com.plantdiseases.app.data.local.PlantEntity
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.util.Haptics
import com.plantdiseases.app.util.LocaleHelper
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class DoctorChatSheet : BottomSheetDialogFragment() {

    private lateinit var plant: PlantEntity
    private var latestDisease: String? = null

    private lateinit var messagesLayout: LinearLayout
    private lateinit var scrollView: ScrollView
    private lateinit var suggestionsContainer: LinearLayout
    private lateinit var inputField: TextInputEditText
    private var thinkingView: View? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = buildUI()

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val behavior = BottomSheetBehavior.from(view.parent as View)
        behavior.state = BottomSheetBehavior.STATE_EXPANDED
        behavior.peekHeight = 600.dp()
        loadMessages()
    }

    private fun buildUI(): View {
        val root = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(requireContext().getColor(R.color.background))
        }

        // Drag handle
        root.addView(View(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_handle)
            layoutParams = LinearLayout.LayoutParams(40.dp(), 4.dp()).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = 8.dp()
                bottomMargin = 4.dp()
            }
        })

        // Header
        val header = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(20.dp(), 18.dp(), 20.dp(), 18.dp())
            setBackgroundResource(R.drawable.bg_garden_hero)
        }
        header.addView(FrameLayout(requireContext()).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            layoutParams = LinearLayout.LayoutParams(48.dp(), 48.dp())
            addView(ImageView(requireContext()).apply {
                setImageResource(R.drawable.ic_care)
                setColorFilter(requireContext().getColor(R.color.primary))
                layoutParams = FrameLayout.LayoutParams(26.dp(), 26.dp(), Gravity.CENTER)
            })
        })
        val titles = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginStart = 14.dp() }
        }
        titles.addView(TextView(requireContext()).apply {
            text = getString(R.string.doctor_title)
            textSize = 20f
            setTextColor(requireContext().getColor(R.color.hero_stat_text))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        })
        titles.addView(TextView(requireContext()).apply {
            text = plant.name
            textSize = 14f
            setTextColor(requireContext().getColor(R.color.hero_stat_text))
            alpha = 0.8f
        })
        header.addView(titles)

        root.addView(header)

        // Messages area
        scrollView = ScrollView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1f,
            )
            clipToPadding = false
            setPadding(0, 0, 0, 8.dp())
        }
        messagesLayout = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20.dp(), 12.dp(), 20.dp(), 12.dp())
        }
        scrollView.addView(messagesLayout)
        root.addView(scrollView)

        // Suggestion chips
        suggestionsContainer = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.VISIBLE
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { setMargins(20.dp(), 4.dp(), 20.dp(), 8.dp()) }
        }
        val chipScroll = HorizontalScrollView(requireContext()).apply {
            isHorizontalScrollBarEnabled = false
        }
        val chipRow = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 4.dp(), 0, 4.dp())
        }
        listOf(
            getString(R.string.doctor_q1),
            getString(R.string.doctor_q2),
            getString(R.string.doctor_q3),
        ).forEach { q ->
            chipRow.addView(Chip(requireContext()).apply {
                text = q
                isClickable = true
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginEnd = 8.dp() }
                setOnClickListener {
                    Haptics.tick(requireContext())
                    sendMessage(q)
                }
            })
        }
        chipScroll.addView(chipRow)
        suggestionsContainer.addView(chipScroll)
        root.addView(suggestionsContainer)

        val clearRow = FrameLayout(requireContext()).apply {
            setBackgroundColor(requireContext().getColor(R.color.background))
            setPadding(20.dp(), 4.dp(), 20.dp(), 8.dp())
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
        }
        clearRow.addView(MaterialButton(requireContext()).apply {
            text = getString(R.string.doctor_clear)
            textSize = 13f
            cornerRadius = 16.dp()
            minHeight = 48.dp()
            minWidth = 152.dp()
            setTextColor(requireContext().getColor(R.color.primary))
            setBackgroundColor(requireContext().getColor(R.color.surface))
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                48.dp(),
                Gravity.CENTER,
            )
            setOnClickListener { clearChat() }
        })
        root.addView(clearRow)
        // Input row
        val inputRow = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(requireContext().getColor(R.color.surface))
            setPadding(20.dp(), 8.dp(), 20.dp(), 12.dp())
        }
        inputField = TextInputEditText(requireContext()).apply {
            hint = getString(R.string.doctor_hint)
            setSingleLine(false)
            maxLines = 3
            imeOptions = EditorInfo.IME_ACTION_SEND
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            setOnEditorActionListener { _, actionId, _ ->
                if (actionId == EditorInfo.IME_ACTION_SEND) {
                    val txt = text?.toString()?.trim().orEmpty()
                    if (txt.isNotEmpty()) sendMessage(txt)
                    true
                } else false
            }
        }
        inputRow.addView(inputField)
        inputRow.addView(MaterialButton(requireContext()).apply {
            text = getString(R.string.doctor_send)
            cornerRadius = 16.dp()
            setBackgroundColor(requireContext().getColor(R.color.primary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                48.dp(),
            ).apply { marginStart = 8.dp() }
            setOnClickListener {
                val txt = inputField.text?.toString()?.trim().orEmpty()
                if (txt.isNotEmpty()) {
                    Haptics.tick(requireContext())
                    sendMessage(txt)
                }
            }
        })
        ViewCompat.setOnApplyWindowInsetsListener(inputRow) { view, insets ->
            val bottomInset = insets.getInsets(WindowInsetsCompat.Type.systemBars()).bottom
            view.setPadding(20.dp(), 8.dp(), 20.dp(), 12.dp() + bottomInset)
            insets
        }
        root.addView(inputRow)

        return root
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
                    "severity" to severityHint(it.confidence, it.isHealthy),
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

    /** Coarse client-side severity bucket for the diagnosis context. */
    private fun severityHint(confidence: Float, isHealthy: Boolean): String = when {
        isHealthy -> "low"
        confidence >= 0.85f -> "high"
        confidence >= 0.65f -> "medium"
        else -> "low"
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
        val boldRegex = Regex("""\*\*(.+?)\*\*""")
        val matches = boldRegex.findAll(text)
        if (matches.none()) return text

        val spannable = SpannableString(text.replace(boldRegex) { it.groupValues[1] })
        var offset = 0
        boldRegex.findAll(text).forEach { match ->
            val inner = match.groupValues[1]
            val start = match.range.first - offset
            val end = start + inner.length
            spannable.setSpan(
                StyleSpan(android.graphics.Typeface.BOLD),
                start, end,
                Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
            offset += match.value.length - inner.length
        }
        return spannable
    }

    private fun clearChat() {
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

        fun show(fm: FragmentManager, plant: PlantEntity, latestDisease: String?) {
            val sheet = DoctorChatSheet().apply {
                this.plant = plant
                this.latestDisease = latestDisease
            }
            sheet.show(fm, TAG)
        }
    }
}
