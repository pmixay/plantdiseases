package com.plantdiseases.app.util

import android.content.Context
import kotlin.random.Random

/**
 * Per-install plant-vibe community nickname — e.g. "Томатный Король 42" /
 * "Агроном из Чертаново", or "Leafy Sage 7" / "Botanist of Fernwood".
 *
 * Generated once on first use from the device locale's word bank and then
 * persisted forever, so the user keeps one stable (but anonymous) community
 * identity. No device identifier feeds it. The community avatar's monogram +
 * colour are derived from this string on-device (see [Avatars]), so nothing
 * extra is served from the server.
 */
object Nicknames {
    private const val PREFS_NAME = "plantdiseases_nickname"
    private const val KEY_NICK = "nickname"

    fun nickname(context: Context): String {
        val prefs = context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.getString(KEY_NICK, null)?.takeIf { it.isNotBlank() }?.let { return it }
        val nick = generate(LocaleHelper.isRussian(context))
        prefs.edit().putString(KEY_NICK, nick).apply()
        return nick
    }

    private fun generate(ru: Boolean): String {
        val n = Random.nextInt(2, 100)
        return if (ru) {
            if (Random.nextBoolean()) {
                "${ruAdj.random()} ${ruTitle.random()} $n"
            } else {
                "${ruRole.random()} из ${ruPlace.random()}"
            }
        } else {
            if (Random.nextBoolean()) {
                "${enAdj.random()} ${enTitle.random()} $n"
            } else {
                "${enRole.random()} of ${enPlace.random()}"
            }
        }
    }

    // One list per line: ktlint's argument-list-wrapping wants one-arg-per-line
    // only for multi-line calls, and line length isn't enforced here.
    private val ruAdj = listOf("Томатный", "Кактусовый", "Фикусовый", "Мятный", "Базиликовый", "Лавровый", "Папоротниковый", "Суккулентный", "Бамбуковый", "Орхидейный")
    private val ruTitle = listOf("Король", "Садовник", "Гуру", "Магистр", "Хранитель", "Шаман", "Профессор", "Барон", "Повелитель", "Знаток")
    private val ruRole = listOf("Агроном", "Цветовод", "Садовод", "Ботаник", "Озеленитель", "Фитодоктор")
    private val ruPlace = listOf("Чертаново", "Теплицы", "Подоконника", "Оранжереи", "Грядки", "Дачи")
    private val enAdj = listOf("Leafy", "Mighty", "Mossy", "Thorny", "Verdant", "Rooted", "Sprouting", "Sunny", "Bloomin'", "Potted")
    private val enTitle = listOf("King", "Gardener", "Guru", "Keeper", "Whisperer", "Baron", "Sage", "Wizard", "Champion", "Boss")
    private val enRole = listOf("Agronomist", "Grower", "Botanist", "Planter", "Gardener", "Sprout")
    private val enPlace = listOf("Greenwich", "the Greenhouse", "the Windowsill", "the Allotment", "Fernwood", "Sunnyvale")
}
