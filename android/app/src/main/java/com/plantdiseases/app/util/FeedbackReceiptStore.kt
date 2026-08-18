package com.plantdiseases.app.util

import android.content.Context

interface FeedbackReceiptStore {
    fun put(scanToken: String, receipt: String)

    fun get(scanToken: String): String?

    fun remove(scanToken: String)

    fun clear()
}

class SharedPreferencesFeedbackReceiptStore(context: Context) : FeedbackReceiptStore {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    override fun put(scanToken: String, receipt: String) {
        if (scanToken.isNotBlank() && receipt.isNotBlank()) {
            prefs.edit().putString(scanToken, receipt).apply()
        }
    }

    override fun get(scanToken: String): String? = prefs.getString(scanToken, null)

    override fun remove(scanToken: String) {
        prefs.edit().remove(scanToken).apply()
    }

    override fun clear() {
        prefs.edit().clear().apply()
    }

    private companion object {
        const val PREFS = "feedback_receipts_v1"
    }
}

object NoOpFeedbackReceiptStore : FeedbackReceiptStore {
    override fun put(scanToken: String, receipt: String) = Unit

    override fun get(scanToken: String): String? = null

    override fun remove(scanToken: String) = Unit

    override fun clear() = Unit
}
