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
            setTextColor(requireContext().getColor(R.color.on_primary))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        })
        titles.addView(TextView(requireContext()).apply {
            text = plant.nickname?.takeIf { it.isNotBlank() } ?: plant.name
            textSize = 14f
            setTextColor(requireContext().getColor(android.R.color.white))
            alpha = 0.8f
        })
        header.addView(titles)

        // Clear chat button
        val clearBtn = MaterialButton(requireContext()).apply {
            text = getString(R.string.doctor_clear)
            textSize = 11f
            cornerRadius = 12.dp()
            setBackgroundColor(0x40FFFFFF)
            setTextColor(requireContext().getColor(R.color.on_primary))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                36.dp(),
            )
            setOnClickListener { clearChat() }
        }
        header.addView(clearBtn)
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
            setPadding(12.dp(), 12.dp(), 12.dp(), 12.dp())
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
            ).apply { setMargins(12.dp(), 4.dp(), 12.dp(), 4.dp()) }
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

        // Input row
        val inputRow = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(requireContext().getColor(R.color.surface))
            setPadding(12.dp(), 8.dp(), 12.dp(), 8.dp())
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

        val repo = (requireActivity().application as PlantDiseasesApp).plantRepository
        val isRu = LocaleHelper.isRussian(requireContext())
        val now = System.currentTimeMillis()

        addMessageBubble("user", text, now)
        scrollToBottom()

        // Thinking placeholder
        val thinkingBubble = buildBubble(
            getString(R.string.doctor_thinking),
            isUser = false,
            timestamp = now,
        )
        thinkingView = thinkingBubble
        messagesLayout.addView(thinkingBubble)
        scrollToBottom()

        lifecycleScope.launch {
            // Persist user message
            repo.saveChatMessage(
                ChatMessageEntity(plantId = plant.id, role = "user", content = text, timestamp = now)
            )

            val reply = buildLocalDoctorReply(text, isRu)

            // Remove thinking bubble
            thinkingView?.let { messagesLayout.removeView(it) }
            thinkingView = null

            val replyTime = System.currentTimeMillis()
            addMessageBubble("bot", reply, replyTime)
            scrollToBottom()

            repo.saveChatMessage(
                ChatMessageEntity(plantId = plant.id, role = "bot", content = reply, timestamp = replyTime)
            )
        }
    }

    /**
     * Local placeholder for the future server-backed assistant.
     *
     * To connect the real AI later, replace this method with the existing
     * Retrofit `/api/chat` request using `sessionId = "plant_${plant.id}"`,
     * `message = userMessage`, and context fields below. The bottom sheet,
     * Markdown rendering, follow-up chips, and Room persistence are already
     * wired around this single reply-producing boundary.
     */
    private fun buildLocalDoctorReply(userMessage: String, isRu: Boolean): String {
        val displayName = plant.nickname?.takeIf { it.isNotBlank() } ?: plant.name
        val species = plant.species?.takeIf { it.isNotBlank() }
        val disease = latestDisease?.takeIf { it.isNotBlank() }
        val topic = userMessage.take(120)

        return if (isRu) {
            buildString {
                append("**Заглушка Plant Doctor.** Я пока работаю без серверного ИИ, но уже сохраняю диалог для растения **")
                append(displayName)
                append("**.\n\n")
                append("**Контекст:** ")
                append(species ?: "вид не указан")
                disease?.let { append(", последний диагноз: ").append(it) }
                append(".\n\n")
                append("По вопросу: \"").append(topic).append("\".\n")
                append("1. Проверьте новые пятна, влажность почвы и освещение.\n")
                append("2. Сравните состояние с последним сканом в Timeline.\n")
                append("3. Если симптомы усиливаются, сделайте новый скан и добавьте заметку в журнал.\n\n")
                append("Позже этот ответ будет заменён серверным `/api/chat`, без переделки UI.")
            }
        } else {
            buildString {
                append("**Plant Doctor placeholder.** I am running locally for now, but this chat is already persisted for **")
                append(displayName)
                append("**.\n\n")
                append("**Context:** ")
                append(species ?: "species not set")
                disease?.let { append(", latest diagnosis: ").append(it) }
                append(".\n\n")
                append("For: \"").append(topic).append("\".\n")
                append("1. Check new spots, soil moisture, and current light.\n")
                append("2. Compare changes against the latest scan in Timeline.\n")
                append("3. If symptoms progress, take a fresh scan and add a journal note.\n\n")
                append("Later this reply can be replaced by server `/api/chat` without changing the UI.")
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
            setTextColor(
                requireContext().getColor(if (isUser) R.color.on_primary else R.color.on_surface)
            )
            setLineSpacing(3.dp().toFloat(), 1f)
        })
        bubble.addView(TextView(requireContext()).apply {
            text = formatTime(timestamp)
            textSize = 10f
            setTextColor(
                requireContext().getColor(
                    if (isUser) android.R.color.white else R.color.on_surface_secondary
                )
            )
            alpha = 0.7f
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
