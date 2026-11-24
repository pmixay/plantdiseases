package com.plantdiseases.app.util

import com.plantdiseases.app.data.model.CarePlanResponse

/**
 * Local, bilingual fallback content for fully on-device (Aqua) scans.
 *
 * Online scans get their description / treatment / prevention text and the
 * rich care plan from the server. Offline there is no server — so the result
 * screen rendered blank sections. This object closes that gap with a compact
 * rules table derived from the shipped offline guide articles
 * ([com.plantdiseases.app.data.GuideDataProvider]) and a simplified subset of
 * the server's care-plan rules.
 *
 * Deliberately SIMPLER than the online plan: v1 care-plan fields only
 * (watering interval, light tier, humidity band, one key action, follow-up),
 * no species-specific tailoring, no LLM polish. The UI marks it as a basic
 * offline plan.
 *
 * Pure JVM — no Android dependencies — so it is unit-testable.
 */
object OfflineScanContent {

    /** Bilingual result-card content for one diagnosis class. */
    data class CareCard(
        val descriptionEn: String,
        val descriptionRu: String,
        val treatmentEn: List<String>,
        val treatmentRu: List<String>,
        val preventionEn: List<String>,
        val preventionRu: List<String>,
    )

    /** Simplified care-plan rules for one diagnosis class. */
    data class CareRules(
        val wateringDays: Int,
        val lightTier: String,
        val humidityMin: Int,
        val humidityMax: Int,
        val keyActionEn: String,
        val keyActionRu: String,
        val followUpDays: Int,
    )

    /** The Aqua model's in-head class keys (plus the canonical legacy
     *  aliases normalised in [normalizeKey]). */
    val SUPPORTED_CLASSES: Set<String> = setOf(
        "healthy",
        "powdery_mildew",
        "gray_mold_botrytis",
        "leaf_spot",
        "pests",
        "viral_disease",
        "leaf_scorch_underwatering",
        "nutrient_deficiency",
    )

    /** Map legacy / sibling class keys onto the canonical content key. */
    fun normalizeKey(classKey: String): String = when (classKey.trim().lowercase()) {
        "leaf_spot_fungal", "bacterial_leaf_spot" -> "leaf_spot"
        "mealybugs", "mealybug", "scale", "scale_insects",
        "spider_mites", "aphids" -> "pests"
        else -> classKey.trim().lowercase()
    }

    /** Bilingual card content for [classKey], or null for classes we have no
     *  local copy for (e.g. not_a_plant — the result screen hides those
     *  sections anyway). */
    fun careCard(classKey: String): CareCard? = cards[normalizeKey(classKey)]

    /** Care-plan rules for [classKey]; unknown classes get a safe generic
     *  "monitor" plan so the card never renders empty. */
    fun careRules(classKey: String): CareRules =
        rules[normalizeKey(classKey)] ?: DEFAULT_RULES

    /**
     * Build a locally-computed, v1-only [CarePlanResponse] for an offline
     * scan so the existing care-plan card can render it without a network
     * call. `summaryMd` stays null and all v2 fields stay absent — the
     * offline plan is intentionally basic.
     */
    fun localCarePlan(classKey: String, isRu: Boolean): CarePlanResponse {
        val r = careRules(classKey)
        return CarePlanResponse(
            disease = normalizeKey(classKey),
            lightTier = r.lightTier,
            wateringDaysRaw = r.wateringDays,
            fertiliseWeeks = if (normalizeKey(classKey) == "nutrient_deficiency") 2 else 4,
            humidityTarget = "${r.humidityMin}-${r.humidityMax}%",
            keyAction = if (isRu) r.keyActionRu else r.keyActionEn,
            followUpDays = r.followUpDays,
        )
    }

    private val DEFAULT_RULES = CareRules(
        wateringDays = 7,
        lightTier = "bright_indirect",
        humidityMin = 40,
        humidityMax = 60,
        keyActionEn = "Monitor the plant and re-scan online for a full diagnosis.",
        keyActionRu = "Наблюдайте за растением и повторите скан онлайн для полного диагноза.",
        followUpDays = 14,
    )

    private val rules: Map<String, CareRules> = mapOf(
        "healthy" to CareRules(
            7,
            "bright_indirect",
            40,
            60,
            "Keep the current routine: water when the top 2-3 cm of soil is dry.",
            "Сохраняйте текущий уход: поливайте, когда верхние 2-3 см почвы просохли.",
            30,
        ),
        "powdery_mildew" to CareRules(
            7,
            "bright_indirect",
            40,
            55,
            "Improve air circulation, remove affected leaves and keep foliage dry.",
            "Улучшите циркуляцию воздуха, удалите поражённые листья и держите листву сухой.",
            14,
        ),
        "gray_mold_botrytis" to CareRules(
            9,
            "bright_indirect",
            40,
            55,
            "Remove infected parts immediately and lower humidity with better ventilation.",
            "Немедленно удалите заражённые части и снизьте влажность за счёт вентиляции.",
            10,
        ),
        "leaf_spot" to CareRules(
            8,
            "bright_indirect",
            40,
            55,
            "Switch to bottom watering, keep leaves dry and cut off spotted leaves.",
            "Перейдите на нижний полив, держите листья сухими и обрежьте пятнистые листья.",
            14,
        ),
        "pests" to CareRules(
            7,
            "bright_indirect",
            45,
            60,
            "Isolate the plant and treat weekly with neem oil or insecticidal soap.",
            "Изолируйте растение и обрабатывайте раз в неделю маслом нима или инсектицидным мылом.",
            7,
        ),
        "viral_disease" to CareRules(
            7,
            "bright_indirect",
            40,
            60,
            "Isolate the plant - viral infections have no cure; control pests that spread them.",
            "Изолируйте растение - вирусные инфекции не лечатся; боритесь с вредителями-переносчиками.",
            7,
        ),
        "leaf_scorch_underwatering" to CareRules(
            4,
            "bright_indirect",
            50,
            70,
            "Water thoroughly until it drains, then keep a steadier watering rhythm.",
            "Полейте обильно до стока воды и далее поливайте более регулярно.",
            7,
        ),
        "nutrient_deficiency" to CareRules(
            7,
            "bright_indirect",
            40,
            60,
            "Feed with a balanced liquid fertiliser at half strength; refresh old soil.",
            "Подкормите сбалансированным жидким удобрением в половинной дозе; обновите старый грунт.",
            21,
        ),
    )

    private val cards: Map<String, CareCard> = mapOf(
        "healthy" to CareCard(
            descriptionEn = "No signs of disease were detected on this leaf. The plant looks " +
                "healthy - firm, evenly coloured foliage with no spots, mold or pests.",
            descriptionRu = "Признаков заболевания на этом листе не обнаружено. Растение выглядит " +
                "здоровым - плотные, равномерно окрашенные листья без пятен, плесени и вредителей.",
            treatmentEn = listOf(
                "No treatment needed - keep the current care routine",
                "Water only when the top 2-3 cm of soil is dry",
                "Wipe dust off leaves so they can breathe",
            ),
            treatmentRu = listOf(
                "Лечение не требуется - сохраняйте текущий уход",
                "Поливайте, только когда верхние 2-3 см почвы просохли",
                "Протирайте пыль с листьев, чтобы они «дышали»",
            ),
            preventionEn = listOf(
                "Check leaf undersides for pests during watering",
                "Give bright, indirect light",
                "Feed lightly during spring and summer",
            ),
            preventionRu = listOf(
                "Проверяйте изнанку листьев на вредителей при поливе",
                "Обеспечьте яркий рассеянный свет",
                "Слегка подкармливайте весной и летом",
            ),
        ),
        "powdery_mildew" to CareCard(
            descriptionEn = "Powdery mildew - a common fungal disease that shows up as white or " +
                "gray powdery patches on leaves and stems. It thrives in humid, poorly " +
                "ventilated spots.",
            descriptionRu = "Мучнистая роса - распространённое грибковое заболевание: белый или " +
                "серый порошкообразный налёт на листьях и стеблях. Развивается при высокой " +
                "влажности и плохой вентиляции.",
            treatmentEn = listOf(
                "Isolate the affected plant immediately",
                "Remove severely infected leaves",
                "Spray with a baking-soda solution (1 tsp per litre + a drop of dish soap)",
                "Apply neem oil weekly until symptoms stop",
            ),
            treatmentRu = listOf(
                "Немедленно изолируйте поражённое растение",
                "Удалите сильно заражённые листья",
                "Опрыскайте раствором соды (1 ч. л. на литр + капля моющего средства)",
                "Еженедельно применяйте масло нима до исчезновения симптомов",
            ),
            preventionEn = listOf(
                "Ensure good air circulation",
                "Avoid overhead watering and keep leaves dry",
                "Don't crowd plants together",
            ),
            preventionRu = listOf(
                "Обеспечьте хорошую циркуляцию воздуха",
                "Избегайте полива сверху, держите листья сухими",
                "Не ставьте растения слишком близко друг к другу",
            ),
        ),
        "gray_mold_botrytis" to CareCard(
            descriptionEn = "Gray mold (Botrytis) - fuzzy gray-brown mold on flowers, buds, " +
                "leaves and stems. It spreads fast in cool, damp, stagnant air.",
            descriptionRu = "Серая гниль (ботритис) - пушистая серо-коричневая плесень на цветах, " +
                "бутонах, листьях и стеблях. Быстро распространяется в прохладном влажном " +
                "застойном воздухе.",
            treatmentEn = listOf(
                "Remove all infected parts immediately",
                "Improve ventilation and reduce humidity",
                "Stop misting and overhead watering",
                "Apply a fungicide if the infection is widespread",
            ),
            treatmentRu = listOf(
                "Немедленно удалите все заражённые части",
                "Улучшите вентиляцию и снизьте влажность",
                "Прекратите опрыскивание и верхний полив",
                "При обширном поражении примените фунгицид",
            ),
            preventionEn = listOf(
                "Keep humidity below 70%",
                "Remove dead plant material promptly",
                "Water in the morning so foliage dries quickly",
            ),
            preventionRu = listOf(
                "Поддерживайте влажность ниже 70%",
                "Своевременно удаляйте отмершие части растений",
                "Поливайте утром, чтобы листва быстро просыхала",
            ),
        ),
        "leaf_spot" to CareCard(
            descriptionEn = "Leaf spot - bacterial or fungal infection appearing as dark, " +
                "clearly bordered spots, often with a yellow halo. Progresses when leaves " +
                "stay wet for too long.",
            descriptionRu = "Пятнистость листьев - бактериальная или грибковая инфекция: тёмные " +
                "пятна с чёткими краями, часто с жёлтым ореолом. Прогрессирует, когда листья " +
                "долго остаются влажными.",
            treatmentEn = listOf(
                "Cut off affected leaves with sterile scissors",
                "Apply a copper-based fungicide weekly for 2-3 weeks",
                "Switch to bottom watering - keep foliage dry",
                "Improve airflow around the plant",
            ),
            treatmentRu = listOf(
                "Обрежьте поражённые листья стерильными ножницами",
                "Обрабатывайте медным фунгицидом раз в неделю 2-3 недели",
                "Перейдите на нижний полив - листья держите сухими",
                "Улучшите воздухообмен вокруг растения",
            ),
            preventionEn = listOf(
                "Water the substrate, not the leaves",
                "Remove fallen leaves from the pot surface",
                "Quarantine new plants for two weeks",
            ),
            preventionRu = listOf(
                "Поливайте субстрат, а не листья",
                "Убирайте опавшие листья с поверхности горшка",
                "Держите новые растения 2 недели на карантине",
            ),
        ),
        "pests" to CareCard(
            descriptionEn = "Sap-sucking pests (mealybugs, scale, spider mites, aphids) - tiny " +
                "insects that weaken the plant, leaving sticky residue, stippling, webbing or " +
                "cottony masses.",
            descriptionRu = "Сосущие вредители (мучнистые червецы, щитовки, паутинный клещ, тля) - " +
                "мелкие насекомые, ослабляющие растение: липкий налёт, точки, паутинка или " +
                "ватные комочки.",
            treatmentEn = listOf(
                "Isolate the plant from your others right away",
                "Wipe pests off with a cotton swab dipped in diluted alcohol",
                "Spray thoroughly with neem oil or insecticidal soap",
                "Repeat every 5-7 days for 3-4 weeks",
            ),
            treatmentRu = listOf(
                "Сразу изолируйте растение от остальных",
                "Снимите вредителей ватной палочкой со спиртовым раствором",
                "Тщательно опрыскайте маслом нима или инсектицидным мылом",
                "Повторяйте каждые 5-7 дней в течение 3-4 недель",
            ),
            preventionEn = listOf(
                "Inspect new plants before bringing them home",
                "Check leaf undersides during watering",
                "Keep humidity up - spider mites love dry air",
            ),
            preventionRu = listOf(
                "Осматривайте новые растения перед покупкой",
                "Проверяйте изнанку листьев при поливе",
                "Поддерживайте влажность - клещи любят сухой воздух",
            ),
        ),
        "viral_disease" to CareCard(
            descriptionEn = "Viral disease (mosaic / leaf curl / ringspot) - mottled light-and-" +
                "dark patterns, distorted or curled leaves. There is no cure for plant viruses.",
            descriptionRu = "Вирусное заболевание (мозаика / курчавость / кольцевая пятнистость) - " +
                "пёстрый светло-тёмный узор, деформированные или скрученные листья. Лечения у " +
                "вирусов растений нет.",
            treatmentEn = listOf(
                "There is no cure - isolate the plant immediately",
                "Remove and destroy severely infected plants",
                "Control aphids and other vectors that spread the virus",
                "Sterilise tools that touched the infected plant",
            ),
            treatmentRu = listOf(
                "Лечения нет - немедленно изолируйте растение",
                "Сильно поражённые растения удалите и уничтожьте",
                "Боритесь с тлёй и другими переносчиками вируса",
                "Стерилизуйте инструменты после контакта с растением",
            ),
            preventionEn = listOf(
                "Buy certified disease-free plants",
                "Control insect vectors (especially aphids)",
                "Wash hands before handling plants",
            ),
            preventionRu = listOf(
                "Покупайте сертифицированные здоровые растения",
                "Контролируйте насекомых-переносчиков (особенно тлю)",
                "Мойте руки перед работой с растениями",
            ),
        ),
        "leaf_scorch_underwatering" to CareCard(
            descriptionEn = "Leaf scorch from under-watering - crispy brown leaf edges and tips. " +
                "A physiological stress (not an infection) from soil drying out completely.",
            descriptionRu = "Краевой ожог от недолива - сухие коричневые края и кончики листьев. " +
                "Это физиологический стресс (не инфекция) из-за полного пересыхания почвы.",
            treatmentEn = listOf(
                "Water thoroughly until it drains from the bottom",
                "Soak a bone-dry rootball in water for 20-30 minutes",
                "Move away from heat sources and harsh direct sun",
                "Trim badly scorched edges if you like (cosmetic only)",
            ),
            treatmentRu = listOf(
                "Полейте обильно, пока вода не выйдет снизу",
                "Полностью сухой земляной ком замочите в воде на 20-30 минут",
                "Уберите от источников тепла и резкого прямого солнца",
                "При желании обрежьте сильно засохшие края (косметика)",
            ),
            preventionEn = listOf(
                "Check soil moisture before it fully dries",
                "Raise humidity for thirsty tropicals",
                "Re-pot root-bound plants",
            ),
            preventionRu = listOf(
                "Проверяйте влажность до полного пересыхания почвы",
                "Повышайте влажность для влаголюбивых тропиков",
                "Пересаживайте растения из тесных горшков",
            ),
        ),
        "nutrient_deficiency" to CareCard(
            descriptionEn = "Nutrient deficiency - yellowing leaves (chlorosis), often between " +
                "the veins, when the plant can't get enough of a key element from old or " +
                "depleted soil.",
            descriptionRu = "Дефицит питания - пожелтение листьев (хлороз), часто между жилками, " +
                "когда растению не хватает ключевого элемента из-за старого или истощённого " +
                "грунта.",
            treatmentEn = listOf(
                "Feed with a balanced liquid fertiliser at half strength",
                "Re-pot into fresh, quality potting mix",
                "Correct watering so roots can absorb again",
                "Use a feed with micronutrients (iron, magnesium) for stubborn cases",
            ),
            treatmentRu = listOf(
                "Подкормите сбалансированным жидким удобрением в половинной дозе",
                "Пересадите в свежий качественный грунт",
                "Наладьте полив, чтобы корни снова усваивали питание",
                "В упорных случаях используйте удобрение с микроэлементами (железо, магний)",
            ),
            preventionEn = listOf(
                "Feed lightly through spring and summer",
                "Refresh the topsoil or re-pot yearly",
                "Don't over-water",
            ),
            preventionRu = listOf(
                "Слегка подкармливайте весной и летом",
                "Обновляйте верхний слой почвы или пересаживайте ежегодно",
                "Не переливайте",
            ),
        ),
    )
}
