"""Bilingual care-plan rule tables for ``care_plan.generate_care_plan``.

Pure data: light tiers, base care, per-disease protocols (EN and RU), the
follow-up checkpoint text and the summary copy. The logic that reads them
stays in ``care_plan``.
"""

from __future__ import annotations

# ── Care plan ───────────────────────────────────────────────────────
#
# The care-plan generator is the only deterministic surface in this
# module — it must produce a usable plan even when the chat backend
# is missing. The rule engine below is built around four primitives:
#
#   * **Light tier** — translates the user-supplied light bucket (low /
#     medium / bright_indirect / direct_sun) into concrete lux targets,
#     window placement guidance, and acclimation notes.
#   * **Base care** — watering interval, watering method, soil mix,
#     fertiliser cadence + NPK, humidity band, day/night temperature
#     window. These are the "if the plant were healthy" defaults.
#   * **Season modifier** — growing season (Mar–Oct N-hemi default)
#     vs dormant. Reduces watering / feeding intensity in dormant
#     months to prevent rot.
#   * **Disease protocol** — per-class overrides + an explicit
#     treatment checklist, sanitation steps, isolation flag and red
#     flags (escalate-to-doctor symptoms).
#
# Backwards-compat: the v1 caller (Android v1.x) reads
# ``watering_days``, ``fertilise_weeks``, ``humidity_target``,
# ``key_action`` and ``follow_up_days``. Those five keys keep their
# v1 meaning. The richer fields are additive — clients that ignore
# unknown keys still render the v1 card unchanged.

_VALID_LIGHT_TIERS: tuple[str, ...] = (
    "low_light",
    "medium_light",
    "bright_indirect",
    "direct_sun",
)

# Light tier → (target lux band, window placement hint, acclimation
# hint). Lux is the canonical unit so the LightMeterActivity readout
# can be compared directly.
_LIGHT_TIER_PROFILE: dict[str, dict] = {
    "low_light": {
        "lux_target": {"min": 50, "max": 250},
        "placement": "north-facing window or 2-3 m back from a bright window",
        "acclimation": "Tolerates shade indefinitely; never move suddenly into direct sun.",
    },
    "medium_light": {
        "lux_target": {"min": 250, "max": 1_000},
        "placement": "east-facing window or 1-2 m back from a south/west window",
        "acclimation": "Rotate the pot a quarter-turn weekly for even growth.",
    },
    "bright_indirect": {
        "lux_target": {"min": 1_000, "max": 5_000},
        "placement": "1 m from a south/west window with a sheer curtain",
        "acclimation": "Acclimate over 7-10 days when moving between light tiers.",
    },
    "direct_sun": {
        "lux_target": {"min": 5_000, "max": 25_000},
        "placement": "south or west window with no curtain, or outdoors after acclimation",
        "acclimation": "Acclimate slowly (1 h/day extra) to avoid sunburn.",
    },
}

# Base care defaults per light tier (assumed healthy plant). All
# numeric ranges are inclusive.
_BASE_CARE: dict[str, dict] = {
    "low_light": {
        "watering_days": 10,
        "watering_method": "soak_and_drain",
        "soil_mix": "1 part peat-based mix + 1 part perlite for drainage",
        "fertilise_weeks": 6,
        "fertiliser_npk": "balanced 3-1-2 at half strength",
        "humidity_band": {"min": 40, "max": 60},
        "temperature_c": {"day": [18, 24], "night": [15, 20]},
    },
    "medium_light": {
        "watering_days": 7,
        "watering_method": "soak_and_drain",
        "soil_mix": "2 parts peat or coco + 1 part perlite + 1 part bark",
        "fertilise_weeks": 4,
        "fertiliser_npk": "balanced 3-1-2 at half strength",
        "humidity_band": {"min": 40, "max": 60},
        "temperature_c": {"day": [18, 26], "night": [15, 21]},
    },
    "bright_indirect": {
        "watering_days": 5,
        "watering_method": "soak_and_drain",
        "soil_mix": "2 parts peat or coco + 1 part perlite + 1 part bark",
        "fertilise_weeks": 3,
        "fertiliser_npk": "balanced 3-1-2 at full strength",
        "humidity_band": {"min": 50, "max": 70},
        "temperature_c": {"day": [20, 27], "night": [16, 22]},
    },
    "direct_sun": {
        "watering_days": 4,
        "watering_method": "soak_and_drain",
        "soil_mix": "fast-draining cactus/succulent mix or 1:1 with grit",
        "fertilise_weeks": 3,
        "fertiliser_npk": "low-N 2-3-3 to support hardening off",
        "humidity_band": {"min": 30, "max": 50},
        "temperature_c": {"day": [22, 30], "night": [16, 22]},
    },
}

# Disease-specific overrides. Anything missing falls back to the base
# light-tier plan. ``treatment`` is the per-disease checklist that
# replaces the v1 single-line ``note``; ``sanitation`` and ``red_flags``
# are surfaced as their own client sections.
_DISEASE_PROTOCOL: dict[str, dict] = {
    "powdery_mildew": {
        "key_action": "Lower humidity, improve airflow, and dust spores with a baking-soda spray.",
        "humidity_band": {"min": 30, "max": 45},
        "watering_method": "bottom_water",
        "treatment": [
            "Move the plant to a brighter, drier spot with airflow.",
            "Wipe visibly infected leaves with a 1:9 milk-water solution once a week.",
            "If it spreads, apply potassium-bicarbonate or sulfur fungicide per label.",
            "Discard heavily infected leaves into a sealed bag - do not compost.",
        ],
        "sanitation": ["Sanitise pruners with 70% isopropyl between cuts."],
        "isolation": True,
        "red_flags": [
            "White fuzz reappears within 7 days of treatment.",
            "Spreads to a second plant despite isolation.",
        ],
        "follow_up_days": 7,
        "fertilise_weeks": 6,
    },
    "gray_mold_botrytis": {
        "key_action": "Lower humidity, clear debris weekly, and apply copper or biofungicide.",
        "humidity_band": {"min": 30, "max": 50},
        "watering_method": "bottom_water",
        "treatment": [
            "Remove all fallen / mushy leaves and discard in a sealed bag.",
            "Cut visibly infected tissue back to clean green, sterilising the blade between cuts.",
            "Apply a copper-octanoate or Bacillus-subtilis spray every 7 days for 3 weeks.",
            "Run a fan nearby for 4-6 hours/day to keep leaves dry.",
        ],
        "sanitation": [
            "Wash the saucer and outer pot with 1:9 bleach-water.",
            "Top-dress the soil with fresh, dry medium to bury spores.",
        ],
        "isolation": True,
        "red_flags": [
            "New gray fuzz appears on stems or growth points.",
            "Leaves continue to collapse despite drier conditions.",
        ],
        "follow_up_days": 7,
        "fertilise_weeks": 6,
    },
    "leaf_spot": {
        "key_action": "Remove infected leaves, stop overhead watering, and apply a copper-based spray weekly.",
        "watering_method": "bottom_water",
        "treatment": [
            "Strip all leaves with >25% spot coverage; discard sealed.",
            "Stop all overhead watering and misting - keep the foliage dry.",
            "Spray copper-hydroxide (covers fungal and bacterial) or chlorothalonil weekly for 3-4 weeks per label dose.",
            "Improve airflow with a fan during the wettest part of the day.",
            "Isolate the plant until new growth comes in clean.",
        ],
        "sanitation": [
            "Sterilise pruners with 70% isopropyl between cuts; do not reuse infected soil.",
            "Wash hands between handling this plant and your other plants.",
        ],
        "isolation": True,
        "red_flags": [
            "Spots expand or new spots appear after 14 days.",
            "Lesions ooze or smell bad, or the stem darkens at the soil line - bacterial soft rot.",
        ],
        "follow_up_days": 7,
    },
    "viral_disease": {
        "key_action": "Isolate the plant. Sanitise tools. There is no chemical cure.",
        "treatment": [
            "Quarantine the plant at least 2 m from other plants.",
            "Sterilise pruners with 70% isopropyl before touching any other plant.",
            "Watch for symptoms on neighbours for 30 days; cull if symptoms appear.",
            "Discard the infected plant if symptoms spread to >50% of foliage.",
        ],
        "sanitation": [
            "Wash hands with soap after handling the infected plant.",
            "Do not propagate cuttings or share the soil.",
        ],
        "isolation": True,
        "red_flags": [
            "Stunted new growth or mosaic patterns on emerging leaves.",
            "Symptoms appearing on a neighbour within 30 days.",
        ],
        "follow_up_days": 14,
        "fertilise_weeks": 0,
    },
    "pests": {
        "key_action": "Isolate the plant, wipe the colonies off with 70% isopropyl, then smother survivors with a weekly oil or soap spray.",
        "treatment": [
            "Isolate the plant immediately - mealybugs and scale crawl to neighbours.",
            "Dab visible clusters with a cotton swab dipped in 70% isopropyl; scrape scale shells off with a soft toothbrush.",
            "Spray the whole plant - leaf undersides, axils and stems - with insecticidal soap, neem or horticultural oil; repeat every 7 days for 3-4 weeks.",
            "Prune badly encrusted stems and bin them in a sealed bag.",
            "For a heavy or persistent infestation use a systemic insecticide per label, and check the root ball - root mealybugs hide in the substrate.",
        ],
        "sanitation": [
            "Wipe the pot, saucer and nearby shelf with soapy water - crawlers and egg sacs sit off the plant too.",
            "Wash hands and sanitise pruners with 70% isopropyl before touching another plant.",
        ],
        "isolation": True,
        "red_flags": [
            "New white tufts or brown bumps within 7 days of a treatment round.",
            "Sticky honeydew or black sooty mould keeps coming back.",
            "A neighbouring plant develops the same bumps despite isolation.",
        ],
        "follow_up_days": 7,
        "fertilise_weeks": 6,
    },
    "edema": {
        "key_action": "Reduce watering frequency, raise light and airflow.",
        "watering_days": "as_needed",
        "watering_method": "let_top_inch_dry",
        "treatment": [
            "Skip the next watering and wait for the top 3 cm of soil to dry fully.",
            "Move to brighter light to raise transpiration.",
            "Improve airflow with a fan or open window.",
            "Avoid fertilising until new growth comes in clean.",
        ],
        "isolation": False,
        "red_flags": [
            "Blisters keep forming on new growth.",
            "Yellowing or stem softening appears.",
        ],
        "follow_up_days": 14,
        "fertilise_weeks": 0,
    },
    "sunburn_or_scorch": {
        "key_action": "Move out of direct midday sun. Bright indirect light only.",
        "treatment": [
            "Move the plant 1-2 m away from any direct south/west sun.",
            "Trim leaves with >50% burn back to the petiole.",
            "Resume a light fertiliser schedule once new growth appears.",
        ],
        "isolation": False,
        "red_flags": [
            "Burn keeps spreading even in shade - check for a root-rot underlay.",
        ],
        "follow_up_days": 14,
    },
    "leaf_scorch_underwatering": {
        "key_action": "Soak-water; flush salts; raise humidity.",
        "watering_days": "as_needed",
        "watering_method": "bottom_water_soak",
        "treatment": [
            "Bottom-soak the rootball in tepid water for 20 minutes.",
            "Flush the topsoil with 2× the pot volume of water to wash out salts.",
            "Move to a slightly shadier spot for 7 days while the plant recovers.",
            "Raise local humidity (pebble tray or humidifier) to 50-60%.",
        ],
        "isolation": False,
        "red_flags": [
            "Crispy edges keep appearing on new leaves.",
            "Soil pulls away from the pot wall - root system has collapsed.",
        ],
        "follow_up_days": 7,
    },
    "nutrient_deficiency": {
        "key_action": "Feed half-strength balanced NPK; chelated Fe + Mg for interveinal yellowing.",
        "fertilise_weeks": 2,
        "fertiliser_npk": "balanced 3-1-2 at half strength + chelated iron if interveinal",
        "treatment": [
            "Apply a balanced 3-1-2 NPK at half strength with the next watering.",
            "If interveinal yellowing (yellow leaf, green veins): add chelated iron + Epsom salt at label dose.",
            "If older leaves yellow first: nitrogen deficit - switch to a higher-N feed for 2 cycles.",
            "Reassess in 14 days; new leaves should emerge fully green.",
        ],
        "isolation": False,
        "red_flags": [
            "Yellowing spreads despite feeding - check for root rot or pH drift.",
        ],
        "follow_up_days": 14,
    },
    "healthy": {
        "key_action": "Keep your current routine; monitor weekly.",
        "treatment": [
            "Continue the current watering and feeding cadence.",
            "Wipe leaves with a damp cloth monthly to clear dust.",
            "Rotate the pot a quarter-turn each week for even growth.",
        ],
        "isolation": False,
        "red_flags": [
            "New leaves emerge smaller or paler than usual.",
            "Any spots, stickiness, webbing or new leaf-edge crisping.",
        ],
        "follow_up_days": 30,
    },
    "not_a_plant": {
        "key_action": "Try a clearer photo of a single leaf.",
        "treatment": [
            "Move closer so a single leaf fills most of the frame.",
            "Wait for autofocus to lock and re-shoot in good, even light.",
        ],
        "isolation": False,
        "follow_up_days": 30,
    },
}


# Russian counterparts for every translatable string in
# ``_DISEASE_PROTOCOL`` / ``_LIGHT_TIER_PROFILE`` / ``_BASE_CARE``.
# Only the strings change — numeric ranges, isolation flags and
# follow-up cadences are reused as-is from the canonical English
# tables above. Pulled into a separate dict so the English copy
# stays the single source of truth for the rule logic.

_DISEASE_PROTOCOL_RU: dict[str, dict] = {
    "powdery_mildew": {
        "key_action": "Снизьте влажность, улучшите проветривание и протрите листья содовым раствором.",
        "treatment": [
            "Перенесите растение в более светлое и сухое место с движением воздуха.",
            "Раз в неделю протирайте поражённые листья раствором молока и воды 1:9.",
            "Если распространяется - применяйте калия бикарбонат или серу по инструкции.",
            "Сильно поражённые листья выбрасывайте в пакете - не в компост.",
        ],
        "sanitation": ["Дезинфицируйте секатор изопропиловым 70%-м спиртом между срезами."],
        "red_flags": [
            "Белый налёт возвращается в течение 7 дней после обработки.",
            "Болезнь перешла на соседнее растение, несмотря на изоляцию.",
        ],
    },
    "gray_mold_botrytis": {
        "key_action": "Снизьте влажность, убирайте опавшие листья и применяйте препараты меди или биофунгицид.",
        "treatment": [
            "Удалите все опавшие и подгнившие листья и выбросьте в пакете.",
            "Срежьте видимо поражённые ткани до здоровой зелёной, стерилизуя лезвие между срезами.",
            "Каждые 7 дней в течение 3 недель опрыскивайте меди октаноатом или Bacillus subtilis.",
            "Включайте вентилятор рядом 4-6 часов в день, чтобы листья оставались сухими.",
        ],
        "sanitation": [
            "Промойте поддон и горшок раствором отбеливателя 1:9.",
            "Присыпьте субстрат слоем свежего сухого грунта, чтобы заглубить споры.",
        ],
        "red_flags": [
            "Новый серый налёт появляется на стеблях или точках роста.",
            "Листья продолжают опадать даже при более сухих условиях.",
        ],
    },
    "leaf_spot": {
        "key_action": "Удалите поражённые листья, прекратите полив сверху и применяйте препарат меди раз в неделю.",
        "treatment": [
            "Срежьте все листья с пятнистостью более 25% и выбросьте в пакете.",
            "Прекратите полив сверху и опрыскивание - держите листву сухой.",
            "Раз в неделю в течение 3-4 недель применяйте гидроксид меди (подходит и против грибка, и против бактерий) или хлороталонил по инструкции.",
            "Включайте вентилятор в самое влажное время дня.",
            "Изолируйте растение до появления чистого нового прироста.",
        ],
        "sanitation": [
            "Стерилизуйте секатор 70%-м изопропиловым спиртом между срезами; не используйте заражённый грунт повторно.",
            "Мойте руки между этим растением и другими.",
        ],
        "red_flags": [
            "Через 14 дней пятна разрастаются или появляются новые.",
            "Очаги мокнут или плохо пахнут, либо стебель темнеет у почвы - бактериальная мягкая гниль.",
        ],
    },
    "viral_disease": {
        "key_action": "Изолируйте растение. Стерилизуйте инструменты. Химического лечения нет.",
        "treatment": [
            "Перенесите растение в карантин не ближе 2 м от других.",
            "Перед работой с другими растениями стерилизуйте секатор 70%-м изопропиловым спиртом.",
            "30 дней наблюдайте за соседями; при появлении симптомов - утилизируйте.",
            "Если симптомы охватили более 50% листвы - утилизируйте растение целиком.",
        ],
        "sanitation": [
            "Мойте руки с мылом после контакта с заражённым растением.",
            "Не используйте черенки и не передавайте грунт.",
        ],
        "red_flags": [
            "Угнетённый рост или мозаичные узоры на новых листьях.",
            "Симптомы появились у соседнего растения в течение 30 дней.",
        ],
    },
    "pests": {
        "key_action": "Изолируйте растение, снимите колонии 70%-м изопропиловым спиртом, затем еженедельно опрыскивайте маслом или инсектицидным мылом.",
        "treatment": [
            "Сразу изолируйте растение - червецы и щитовки переползают на соседние.",
            "Обработайте видимые скопления ватной палочкой с 70%-м изопропиловым спиртом; щитки соскоблите мягкой зубной щёткой.",
            "Опрыскайте всё растение - низ листьев, пазухи и стебли - инсектицидным мылом, маслом нима или садовым маслом; повторяйте каждые 7 дней в течение 3-4 недель.",
            "Сильно облепленные стебли срежьте и выбросьте в закрытом пакете.",
            "При сильном или упорном заражении примените системный инсектицид по инструкции и проверьте корневой ком - корневые червецы прячутся в субстрате.",
        ],
        "sanitation": [
            "Промойте горшок, поддон и полку рядом мыльной водой - личинки и яйцекладки прячутся и вне растения.",
            "Мойте руки и стерилизуйте секатор 70%-м изопропиловым спиртом, прежде чем трогать другое растение.",
        ],
        "red_flags": [
            "Новые белые комочки или коричневые щитки в течение 7 дней после обработки.",
            "Липкая медвяная роса или чёрный сажистый налёт возвращаются снова.",
            "У соседнего растения появились такие же щитки, несмотря на изоляцию.",
        ],
    },
    "edema": {
        "key_action": "Сократите частоту полива, добавьте света и движения воздуха.",
        "treatment": [
            "Пропустите следующий полив и дайте верхним 3 см почвы полностью просохнуть.",
            "Перенесите в более светлое место для усиления транспирации.",
            "Усильте движение воздуха вентилятором или открытым окном.",
            "Не подкармливайте до появления чистого нового прироста.",
        ],
        "red_flags": [
            "Пузыри продолжают появляться на новом приросте.",
            "Появилось пожелтение или размягчение стебля.",
        ],
    },
    "sunburn_or_scorch": {
        "key_action": "Уберите с прямого полуденного солнца. Только яркий рассеянный свет.",
        "treatment": [
            "Перенесите растение на 1-2 м от прямого южного/западного солнца.",
            "Обрежьте листья с ожогом более 50% до черешка.",
            "Возобновите лёгкую подкормку, когда появится новый прирост.",
        ],
        "red_flags": [
            "Ожог продолжает распространяться даже в тени - проверьте корни на гниль.",
        ],
    },
    "leaf_scorch_underwatering": {
        "key_action": "Глубокий полив снизу; промывка от солей; повышение влажности.",
        "treatment": [
            "На 20 минут поставьте горшок в тазик с тёплой водой для замачивания корневого кома.",
            "Промойте верхний слой почвы количеством воды равным двойному объёму горшка, чтобы вымыть соли.",
            "На 7 дней перенесите в чуть более тенистое место для восстановления.",
            "Повысьте локальную влажность поддоном с галькой или увлажнителем до 50-60%.",
        ],
        "red_flags": [
            "Сухие края продолжают появляться на новых листьях.",
            "Почва отстаёт от стенок горшка - корневая система пострадала.",
        ],
    },
    "nutrient_deficiency": {
        "key_action": "Подкормка половинной дозой балансированного NPK; хелаты Fe + Mg при межжилковом хлорозе.",
        "fertiliser_npk": "сбалансированный 3-1-2 в половинной дозе + хелат железа при межжилковом хлорозе",
        "treatment": [
            "При следующем поливе внесите сбалансированный NPK 3-1-2 в половинной дозе.",
            "При межжилковом хлорозе (жёлтый лист, зелёные жилки): добавьте хелат железа + соль Эпсома по инструкции.",
            "Если первыми желтеют старые листья - дефицит азота; на 2 цикла перейдите на азотную подкормку.",
            "Через 14 дней повторите оценку; новые листья должны выходить ровно зелёными.",
        ],
        "red_flags": [
            "Пожелтение продолжается даже при подкормке - проверьте корни и pH.",
        ],
    },
    "healthy": {
        "key_action": "Поддерживайте текущий режим, наблюдайте раз в неделю.",
        "treatment": [
            "Сохраняйте текущий режим полива и подкормки.",
            "Раз в месяц протирайте листья влажной тряпкой от пыли.",
            "Раз в неделю поворачивайте горшок на четверть для равномерного роста.",
        ],
        "red_flags": [
            "Новые листья выходят мельче или бледнее обычного.",
            "Любые пятна, липкость, паутинка или сухие края.",
        ],
    },
    "not_a_plant": {
        "key_action": "Попробуйте сделать более чёткое фото одного листа.",
        "treatment": [
            "Подойдите ближе, чтобы один лист занимал большую часть кадра.",
            "Дождитесь автофокуса и снимите при ровном освещении.",
        ],
    },
}

# Russian copy for the light-tier text fields. Lux bands, humidity
# bands and temperature ranges keep the English table's numbers.
_LIGHT_TIER_PROFILE_RU: dict[str, dict] = {
    "low_light": {
        "placement": "северное окно или 2-3 м от яркого окна",
        "acclimation": "Переносит тень неограниченно долго; не выносите резко на прямое солнце.",
    },
    "medium_light": {
        "placement": "восточное окно или 1-2 м от южного/западного окна",
        "acclimation": "Раз в неделю поворачивайте горшок на четверть для равномерного роста.",
    },
    "bright_indirect": {
        "placement": "1 м от южного/западного окна с лёгкой занавеской",
        "acclimation": "Привыкание 7-10 дней при переходе между уровнями освещения.",
    },
    "direct_sun": {
        "placement": "южное или западное окно без занавески или на улице после акклиматизации",
        "acclimation": "Приучайте к солнцу медленно (+1 ч в день), чтобы избежать ожогов.",
    },
}

# Russian copy for the small handful of base-care strings that live
# in plain English in ``_BASE_CARE``.
_BASE_CARE_TEXT_RU: dict[str, dict] = {
    "low_light": {
        "soil_mix": "1 часть торфяной смеси + 1 часть перлита для дренажа",
        "fertiliser_npk": "сбалансированный 3-1-2 в половинной дозе",
        "watering_method": "soak_and_drain",
    },
    "medium_light": {
        "soil_mix": "2 части торфа или кокоса + 1 часть перлита + 1 часть коры",
        "fertiliser_npk": "сбалансированный 3-1-2 в половинной дозе",
        "watering_method": "soak_and_drain",
    },
    "bright_indirect": {
        "soil_mix": "2 части торфа или кокоса + 1 часть перлита + 1 часть коры",
        "fertiliser_npk": "сбалансированный 3-1-2 в полной дозе",
        "watering_method": "soak_and_drain",
    },
    "direct_sun": {
        "soil_mix": "быстродренируемая смесь для кактусов/суккулентов или 1:1 с крошкой",
        "fertiliser_npk": "пониженный N 2-3-3 для закалки",
        "watering_method": "soak_and_drain",
    },
}

# Checkpoint and season-note text per locale.
_CHECKPOINT_TEXT = {
    "en": {
        "spot_check_short": "Spot-check for new spores / spots / wilting.",
        "spot_check": "Spot-check for new symptoms.",
        "underside": "Inspect the underside of the leaves.",
        "rescan": "Repeat scan; expect signs of recovery.",
        "rescan_compare": "Repeat scan and compare progression.",
        "monthly_check": "Quick visual check; wipe dust.",
        "season_note": "Dormant season: reduce watering and pause feeding to prevent rot.",
    },
    "ru": {
        "spot_check_short": "Осмотрите листья: новые споры, пятна или увядание?",
        "spot_check": "Осмотрите на наличие новых симптомов.",
        "underside": "Проверьте нижнюю сторону листьев.",
        "rescan": "Повторите скан; ждите признаков улучшения.",
        "rescan_compare": "Повторите скан и сравните прогрессию.",
        "monthly_check": "Быстрый осмотр и удаление пыли.",
        "season_note": "Период покоя: уменьшите полив и пропустите подкормку, чтобы избежать гнили.",
    },
}


_SUMMARY_COPY = {
    "en": {
        "water_as_needed": "Water only when the top 3 cm of soil is dry.",
        "water_every": "Water every **{n} days**; let the top 2 cm dry between waterings.",
        "water_fallback": "Water per the species' preference.",
        "feed_every": "Feed every **{n} weeks** with {npk}.",
        "feed_skip": "Skip fertilising for now - the plant is recovering.",
        "humidity": "Keep humidity in the {min}-{max}% range.",
        "temp": "Daytime **{day0}-{day1}°C**, night **{night0}-{night1}°C**.",
        "isolate": "**Isolate this plant** until the protocol completes.",
        "treatment_heading": "**Treatment**",
        "balanced_npk": "a balanced NPK",
    },
    "ru": {
        "water_as_needed": "Поливайте только когда верхние 3 см почвы просохли.",
        "water_every": "Полив раз в **{n} дн.**; верхние 2 см должны подсохнуть между поливами.",
        "water_fallback": "Полив по предпочтениям вида.",
        "feed_every": "Подкормка раз в **{n} нед.** препаратом {npk}.",
        "feed_skip": "Пропустите подкормку - растению нужно восстановление.",
        "humidity": "Поддерживайте влажность {min}-{max}%.",
        "temp": "Днём **{day0}-{day1}°C**, ночью **{night0}-{night1}°C**.",
        "isolate": "**Изолируйте растение** до окончания протокола.",
        "treatment_heading": "**Лечение**",
        "balanced_npk": "балансированным NPK",
    },
}
