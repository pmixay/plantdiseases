"""
Bilingual disease information database for PlantScope v4.x.

Maps Stage 2 classifier class names to houseplant-specific descriptions,
treatments, and prevention tips. v4 drops field-crop-specific classes
(``blight``, ``leaf_mold``, ``mosaic_virus``, ``rust``) and adds
indoor-relevant ones: ``mealybug``, ``scale_insect``, ``nutrient_deficiency``,
``aphids``, ``thrips``, ``whitefly``.

Authoritative class list: ``server/models/classes.json``.
"""

DISEASES_DATABASE = {
    "healthy": {
        "name_en": "Healthy Plant",
        "name_ru": "Здоровое растение",
        "description_en": "The plant looks healthy — leaves show even green colour, no spots, no webbing, no powder. Keep up the current care routine.",
        "description_ru": "Растение выглядит здоровым — листья равномерно-зелёные, без пятен, паутины и налёта. Продолжайте текущий уход.",
        "treatment_en": [
            "No treatment needed — your plant is doing well",
            "Continue regular watering and feeding schedule",
            "Wipe dust from leaves once a week to keep them breathing"
        ],
        "treatment_ru": [
            "Лечение не требуется — растение чувствует себя хорошо",
            "Продолжайте регулярный полив и подкормку",
            "Раз в неделю протирайте листья от пыли — это помогает им «дышать»"
        ],
        "prevention_en": [
            "Water only after the top 2–3 cm of substrate dry out",
            "Provide bright, indirect light for most species",
            "Fertilise during spring and summer growth",
            "Inspect new leaves weekly for the first signs of pests"
        ],
        "prevention_ru": [
            "Поливайте только когда верхние 2–3 см субстрата просохли",
            "Обеспечьте яркое, но рассеянное освещение",
            "Весной и летом вносите удобрения каждые 2–3 недели",
            "Еженедельно осматривайте новые листья на первые признаки вредителей"
        ],
        "is_healthy": True
    },

    "powdery_mildew": {
        "name_en": "Powdery Mildew",
        "name_ru": "Мучнистая роса",
        "description_en": "A fungal disease that appears as white or greyish powder on leaves and stems. Very common on ficus, begonias, violets, and succulents kept in dry air with poor ventilation.",
        "description_ru": "Грибковое заболевание, проявляющееся белым или сероватым налётом на листьях и стеблях. Очень часто встречается на фикусах, бегониях, фиалках и суккулентах в сухом воздухе без вентиляции.",
        "treatment_en": [
            "Remove the worst-affected leaves and dispose of them (do not compost)",
            "Spray the plant with a 1 tsp baking soda + 1 L water + drop of soap solution",
            "Alternatively, use neem oil or a sulfur-based fungicide once a week",
            "Move the plant to a well-ventilated, brighter spot",
            "Stop misting — keep the foliage dry"
        ],
        "treatment_ru": [
            "Удалите наиболее поражённые листья и выбросьте их (не в компост)",
            "Опрыскайте раствором: 1 ч. ложка соды + 1 литр воды + капля мыла",
            "Или применяйте масло нима / фунгицид на основе серы раз в неделю",
            "Переставьте растение в проветриваемое и более светлое место",
            "Прекратите опрыскивание — держите листья сухими"
        ],
        "prevention_en": [
            "Keep humidity between 40–60 %; avoid stagnant, damp air",
            "Ensure good air circulation — don't crowd plants",
            "Water at the soil level, not over the leaves",
            "Quarantine new plants for two weeks before placing them next to the rest"
        ],
        "prevention_ru": [
            "Поддерживайте влажность 40–60 %; избегайте застойного сырого воздуха",
            "Обеспечьте циркуляцию воздуха — не ставьте растения вплотную",
            "Поливайте под корень, а не сверху по листьям",
            "Новые растения держите 2 недели на карантине"
        ],
        "is_healthy": False
    },

    "leaf_spot": {
        "name_en": "Leaf Spot (Bacterial / Fungal)",
        "name_ru": "Пятнистость листьев (бактериальная / грибковая)",
        "description_en": "A broad category covering bacterial and fungal leaf-spot infections. Look for dark, round or irregular spots with yellow halos. Often triggered by water splashing onto leaves in humid indoor conditions.",
        "description_ru": "Обобщённая категория, включающая бактериальные и грибковые пятнистости. Выглядит как тёмные округлые или неправильные пятна с жёлтым ободком. Часто возникает при попадании воды на листья во влажном помещении.",
        "treatment_en": [
            "Cut off affected leaves with sterile scissors and discard them",
            "Apply a copper-based fungicide / bactericide once a week for 2–3 weeks",
            "Switch to bottom watering — never wet the foliage",
            "Improve ventilation and let the substrate dry between waterings",
            "Disinfect all tools with 70 % isopropyl alcohol after each plant"
        ],
        "treatment_ru": [
            "Обрежьте поражённые листья стерильными ножницами и выбросьте",
            "Раз в неделю 2–3 недели подряд обрабатывайте фунгицидом на основе меди",
            "Перейдите на полив снизу — листья держите сухими",
            "Улучшите проветривание, дайте субстрату подсыхать между поливами",
            "После обработки каждого растения дезинфицируйте инструмент спиртом 70 %"
        ],
        "prevention_en": [
            "Water the substrate, not the leaves",
            "Remove fallen leaves from the pot surface regularly",
            "Avoid moving plants between warm and cold rooms — it weakens them",
            "Sterilise pots and saucers before reusing them"
        ],
        "prevention_ru": [
            "Поливайте субстрат, а не листья",
            "Регулярно убирайте опавшие листья с поверхности горшка",
            "Не переставляйте растения между тёплыми и холодными комнатами — это их ослабляет",
            "Перед повторным использованием стерилизуйте горшки и поддоны"
        ],
        "is_healthy": False
    },

    "spider_mites": {
        "name_en": "Spider Mites",
        "name_ru": "Паутинный клещ",
        "description_en": "Tiny sap-sucking arachnids that cause fine stippling, bronze discolouration and a silk-like webbing on the undersides of leaves. Thrive in dry, warm indoor air during winter heating.",
        "description_ru": "Мельчайшие клещи, высасывающие сок: вызывают точечную крапчатость, бронзовый налёт и тончайшую паутинку с нижней стороны листьев. Процветают в сухом и тёплом воздухе в отопительный сезон.",
        "treatment_en": [
            "Rinse the plant in the shower to wash off adults and webs",
            "Spray with insecticidal soap, neem oil or a specialised acaricide",
            "Repeat the treatment every 5–7 days for at least 3 weeks — eggs keep hatching",
            "Raise humidity around the plant with a pebble tray or humidifier",
            "Isolate the affected plant immediately"
        ],
        "treatment_ru": [
            "Промойте растение в душе, чтобы смыть взрослых клещей и паутину",
            "Обработайте инсектицидным мылом, маслом нима или специализированным акарицидом",
            "Повторяйте обработку каждые 5–7 дней минимум 3 недели — из яиц появляются новые",
            "Поднимите влажность рядом с растением (поддон с галькой, увлажнитель)",
            "Немедленно изолируйте заражённое растение"
        ],
        "prevention_en": [
            "Mist or use a humidifier during the heating season",
            "Wipe large leaves with a damp cloth weekly",
            "Inspect new plants with a magnifier before placing them with others",
            "Avoid drought stress — dry plants are magnets for mites"
        ],
        "prevention_ru": [
            "В отопительный сезон опрыскивайте или ставьте увлажнитель",
            "Еженедельно протирайте крупные листья влажной тканью",
            "Осматривайте новые растения с лупой перед размещением в коллекции",
            "Не допускайте пересушивания — пересохшие растения привлекают клещей"
        ],
        "is_healthy": False
    },

    "mealybug": {
        "name_en": "Mealybugs",
        "name_ru": "Мучнистый червец",
        "description_en": "Small white sap-sucking insects covered in a cottony, waxy coating. Look for fluffy white masses in leaf axils, on stems and on the undersides of leaves. Common on succulents, orchids, ficus and citrus indoors.",
        "description_ru": "Мелкие белые сосущие насекомые, покрытые ватообразным восковым налётом. Ищите пушистые белые комочки в пазухах листьев, на стеблях и на нижней стороне листьев. Часто на суккулентах, орхидеях, фикусах и цитрусовых.",
        "treatment_en": [
            "Dab visible insects with a cotton swab dipped in 70 % isopropyl alcohol",
            "Wash the plant with insecticidal soap or neem-oil solution",
            "For severe infestations, use a systemic insecticide (imidacloprid)",
            "Repeat treatment every 7 days for 3–4 weeks to catch hatchlings",
            "Isolate the plant — mealybugs spread to neighbours quickly"
        ],
        "treatment_ru": [
            "Протрите видимых насекомых ватной палочкой со спиртом 70 %",
            "Помойте растение инсектицидным мылом или раствором масла нима",
            "При сильном поражении — системный инсектицид (имидаклоприд)",
            "Повторяйте обработку каждые 7 дней 3–4 недели — добиваете новые поколения",
            "Изолируйте растение — червец быстро переходит на соседей"
        ],
        "prevention_en": [
            "Inspect new plants closely — quarantine for 2–3 weeks",
            "Wipe leaves regularly to spot the first cottony specks early",
            "Avoid over-fertilising with nitrogen — soft growth attracts mealybugs",
            "Keep humidity moderate and ventilate the room"
        ],
        "prevention_ru": [
            "Тщательно осматривайте новые растения — карантин 2–3 недели",
            "Регулярно протирайте листья, чтобы заметить первые белые комочки",
            "Не перекармливайте азотом — рыхлый прирост привлекает червеца",
            "Держите умеренную влажность и проветривайте помещение"
        ],
        "is_healthy": False
    },

    "scale_insect": {
        "name_en": "Scale Insects",
        "name_ru": "Щитовка",
        "description_en": "Small immobile insects that look like brown, tan or black bumps stuck to stems and the undersides of leaves. They produce sticky honeydew that can lead to sooty mould. Common on ficus, palms, citrus and ivy.",
        "description_ru": "Мелкие неподвижные насекомые в виде коричневых, бежевых или чёрных «бляшек» на стеблях и нижней стороне листьев. Выделяют липкую падь, на которой развивается чёрный сажистый грибок. Часто на фикусах, пальмах, цитрусовых и плюще.",
        "treatment_en": [
            "Scrape adult scales off with a soft toothbrush or fingernail",
            "Wipe the plant down with a 70 % alcohol + soap solution",
            "Apply horticultural oil or neem oil — coats and suffocates the scales",
            "Use a systemic insecticide for heavy infestations on hard-to-reach spots",
            "Repeat weekly for 3–4 weeks; scale eggs hatch in waves"
        ],
        "treatment_ru": [
            "Соскоблите взрослых щитовок мягкой зубной щёткой или ногтем",
            "Протрите растение спиртом 70 % с мылом",
            "Обработайте садовым или ниммовым маслом — оно «душит» щитовку",
            "При сильном поражении в труднодоступных местах — системный инсектицид",
            "Повторяйте обработку еженедельно 3–4 недели — новые поколения вылупляются волнами"
        ],
        "prevention_en": [
            "Inspect leaf undersides and stem joints once a week",
            "Quarantine new plants and check carefully before introducing them",
            "Wipe leaves of large-leaved species — early scales come off easily",
            "Don't crowd plants; airflow makes infestations harder to start"
        ],
        "prevention_ru": [
            "Раз в неделю осматривайте обратную сторону листьев и узлы стебля",
            "Новые растения держите на карантине, осматривайте перед размещением",
            "Протирайте листья крупнолистных видов — молодую щитовку легко снять",
            "Не размещайте растения вплотную — циркуляция воздуха мешает заражению"
        ],
        "is_healthy": False
    },

    "nutrient_deficiency": {
        "name_en": "Nutrient Deficiency / Yellowing",
        "name_ru": "Нехватка элементов / пожелтение",
        "description_en": "General chlorosis or yellowing of the leaves without obvious spots, webbing or powder. Causes range from overwatering and root issues to a real shortage of nitrogen, iron or magnesium. Often the leaves turn yellow between the veins, droop, or fall off.",
        "description_ru": "Общий хлороз или пожелтение листьев без явных пятен, паутины или налёта. Причины — от перелива и проблем с корнями до реальной нехватки азота, железа или магния. Часто листья желтеют между жилками, поникают или опадают.",
        "treatment_en": [
            "Check the substrate first — overwatering looks just like deficiency",
            "Let the top 3–4 cm dry out before the next watering",
            "Switch to a balanced liquid fertiliser (NPK ~10-10-10) at half strength",
            "If yellowing is between the veins, try a chelated iron + magnesium feed",
            "If roots smell sour or look brown, repot into fresh, well-draining substrate"
        ],
        "treatment_ru": [
            "Сначала проверьте субстрат — перелив выглядит как нехватка элементов",
            "Дайте верхним 3–4 см просохнуть перед следующим поливом",
            "Перейдите на сбалансированное жидкое удобрение (NPK ~10-10-10) в половинной дозе",
            "Если желтеют участки между жилками — попробуйте хелат железа + магний",
            "Если корни пахнут кислым или потемнели — пересадите в свежий рыхлый субстрат"
        ],
        "prevention_en": [
            "Water only after the top of the substrate dries — don't keep it constantly wet",
            "Feed during active growth (spring–summer); skip fertiliser in winter",
            "Use pots with drainage holes; never let the pot sit in water",
            "Refresh the substrate every 1–2 years — even good soil gets depleted"
        ],
        "prevention_ru": [
            "Поливайте только после просыхания верха субстрата — не держите постоянно сырым",
            "Подкармливайте в активный сезон (весна-лето), зимой подкормки прекратите",
            "Используйте горшки с дренажными отверстиями; не оставляйте в поддоне с водой",
            "Меняйте субстрат каждые 1–2 года — даже хороший грунт истощается"
        ],
        "is_healthy": False
    },

    "aphids": {
        "name_en": "Aphids",
        "name_ru": "Тля",
        "description_en": "Tiny soft-bodied insects (1–3 mm), green, black or pinkish, that cluster on young shoots, flower buds and leaf undersides. They suck sap and excrete sticky honeydew, which can later attract sooty mould. Common on hibiscus, peppers, herbs and fresh growth of most houseplants.",
        "description_ru": "Мелкие мягкотелые насекомые (1–3 мм), зелёные, чёрные или розоватые, скапливаются на молодых побегах, бутонах и обратной стороне листьев. Высасывают сок и оставляют липкую падь, на которую часто оседает сажистый грибок. Чаще всего поражают гибискус, перец, зелень и свежие приросты комнатных растений.",
        "treatment_en": [
            "Rinse the plant with a strong stream of lukewarm water — physically removes most colonies",
            "Spray with insecticidal soap or 1 % neem oil; repeat in 5–7 days for two cycles",
            "For heavy infestations, apply a systemic insecticide (e.g. imidacloprid) per label",
            "Wipe sticky honeydew off leaves so sooty mould doesn't follow",
            "Cut off heavily infested shoot tips — aphids prefer soft new growth"
        ],
        "treatment_ru": [
            "Смойте тлю сильной струёй тёплой воды — большую часть колоний уносит механически",
            "Опрыскайте инсектицидным мылом или 1 % раствором нима; повторите через 5–7 дней (2 цикла)",
            "При сильном заражении применяйте системный инсектицид (например, имидаклоприд) по инструкции",
            "Стирайте липкую падь с листьев — иначе на ней разовьётся сажистый грибок",
            "Срежьте сильно заражённые верхушки — тля любит молодой прирост"
        ],
        "prevention_en": [
            "Inspect new growth and flower buds weekly — early colonies are easy to wash off",
            "Quarantine new plants for 2 weeks before placing them next to the others",
            "Don't over-fertilise with nitrogen — soft, lush growth attracts aphids",
            "Encourage airflow; aphids thrive in still, warm pockets"
        ],
        "prevention_ru": [
            "Раз в неделю осматривайте молодые побеги и бутоны — ранние колонии легко смыть",
            "Новые растения держите 2 недели на карантине",
            "Не перекармливайте азотом — мягкий бурный прирост притягивает тлю",
            "Обеспечьте циркуляцию воздуха — в застойном тепле тля плодится быстрее"
        ],
        "is_healthy": False
    },

    "thrips": {
        "name_en": "Thrips",
        "name_ru": "Трипсы",
        "description_en": "Slender insects (~1 mm), pale to dark, that scrape leaf surfaces and feed on sap. Damage looks like silvery streaks, stippling or a metallic sheen, often with tiny black faecal dots. Especially common on monstera, alocasia, anthurium, ficus and orchids.",
        "description_ru": "Мелкие узкие насекомые (~1 мм), светлые или тёмные. Соскребают эпидермис и пьют сок — на листьях остаются серебристые штрихи, точечные следы или металлический блеск, часто с чёрными точками экскрементов. Часто поражают монстеру, алоказию, антуриум, фикус, орхидеи.",
        "treatment_en": [
            "Isolate the plant — adult thrips fly and spread quickly",
            "Shower the foliage thoroughly, top and bottom, to dislodge larvae",
            "Spray with insecticidal soap or neem oil weekly for 3 cycles (egg → adult takes ~7 days)",
            "Place blue sticky traps next to the plant to catch flying adults",
            "Cut off heavily damaged leaves; bag and discard them, don't compost"
        ],
        "treatment_ru": [
            "Изолируйте растение — взрослые трипсы летают и быстро расселяются",
            "Тщательно промойте листья сверху и снизу, смывая личинок",
            "Опрыскивайте инсектицидным мылом или нимом раз в неделю, 3 цикла (цикл «яйцо → имаго» ~7 дней)",
            "Поставьте рядом синие клеевые ловушки — на них летят взрослые особи",
            "Сильно повреждённые листья срежьте, упакуйте в пакет и выбросьте — не компостируйте"
        ],
        "prevention_en": [
            "Quarantine new plants for at least 2 weeks; check leaf undersides",
            "Keep humidity above 50 % — thrips prefer dry air",
            "Wipe foliage of large-leaved species monthly — early damage is easy to spot",
            "Avoid bringing cut flowers or outdoor plants near houseplants"
        ],
        "prevention_ru": [
            "Новые растения держите 2 недели на карантине, осматривайте обратную сторону листьев",
            "Поддерживайте влажность выше 50 % — трипсы любят сухой воздух",
            "Раз в месяц протирайте листья крупнолистных видов — ранние повреждения видны сразу",
            "Не размещайте срезанные цветы и уличные растения рядом с комнатными"
        ],
        "is_healthy": False
    },

    "whitefly": {
        "name_en": "Whitefly",
        "name_ru": "Белокрылка",
        "description_en": "Small (~1.5 mm) white flying insects that rest on leaf undersides; when the plant is shaken, a cloud of tiny moths takes off. They suck sap and produce honeydew, leading to yellowing leaves and sticky residue. Common on hibiscus, fuchsia, mint, tomato seedlings, gerbera and pelargonium indoors.",
        "description_ru": "Мелкие (~1.5 мм) белые летающие насекомые, сидят на обратной стороне листьев; при встряхивании растения с него взлетает облачко «мошек». Высасывают сок и оставляют липкую падь — листья желтеют, на них остаётся клейкий налёт. Часто поражают гибискус, фуксию, мяту, рассаду томатов, герберы, пеларгонию.",
        "treatment_en": [
            "Place yellow sticky traps near the plant — adults are strongly drawn to yellow",
            "Vacuum adult whiteflies in early morning when they're sluggish",
            "Spray leaf undersides with insecticidal soap or 1 % neem oil every 5 days for 3 weeks",
            "Wipe leaves to remove eggs and honeydew between sprays",
            "Repot if the soil also harbours pupae; discard the top centimetre of substrate"
        ],
        "treatment_ru": [
            "Развесьте рядом жёлтые клеевые ловушки — взрослых сильно тянет на жёлтый",
            "Утром, когда они вялые, можно собирать пылесосом",
            "Опрыскивайте обратную сторону листьев инсектицидным мылом или 1 % нимом каждые 5 дней (3 недели)",
            "Между опрыскиваниями протирайте листья от яиц и пади",
            "Если в субстрате есть куколки — снимите верхний сантиметр почвы или пересадите"
        ],
        "prevention_en": [
            "Check leaf undersides of new plants — that's where eggs hide",
            "Don't crowd plants; whiteflies thrive in still, warm air",
            "Keep windows screened in summer to block adults entering from outside",
            "Wipe leaves regularly so honeydew doesn't accumulate"
        ],
        "prevention_ru": [
            "При покупке осматривайте обратную сторону листьев — там лежат яйца",
            "Не размещайте растения вплотную — белокрылке нужен тёплый застойный воздух",
            "Летом ставьте на окна москитную сетку, чтобы взрослые не залетали с улицы",
            "Регулярно протирайте листья — не давайте пади накапливаться"
        ],
        "is_healthy": False
    },

    "not_a_plant": {
        "name_en": "Not a Plant",
        "name_ru": "Не растение",
        "description_en": "The image doesn't appear to contain a plant leaf that the model can analyse. It might be mostly hand, floor, wall, fabric, or another object. Please retake the photo so the leaf fills most of the frame.",
        "description_ru": "На фото модель не нашла листа растения для анализа. Возможно, в кадре рука, стена, пол или другой предмет. Сделайте снимок заново — так, чтобы лист занимал большую часть кадра.",
        "treatment_en": [
            "Move closer so the plant leaf fills most of the frame",
            "Improve lighting — natural daylight near a window works best",
            "Hold the camera steady; wait for autofocus to lock on the leaf",
            "Keep your fingers, background objects and shadows out of the frame"
        ],
        "treatment_ru": [
            "Подойдите ближе — лист должен занимать большую часть кадра",
            "Улучшите освещение — лучше всего естественный свет у окна",
            "Держите камеру ровно, дождитесь автофокуса на листе",
            "Уберите из кадра пальцы, посторонние предметы и тени"
        ],
        "prevention_en": [
            "Frame the single leaf you want diagnosed — avoid wide room shots",
            "Don't photograph through glass — reflections confuse the detector",
            "Use the camera grid to centre the leaf"
        ],
        "prevention_ru": [
            "Фотографируйте один конкретный лист — не весь интерьер",
            "Не снимайте через стекло — блики путают детектор",
            "Используйте сетку камеры, чтобы разместить лист по центру"
        ],
        "is_healthy": False
    },

    "unknown": {
        "name_en": "Unidentified Condition",
        "name_ru": "Неопределённое состояние",
        "description_en": "The model could not confidently match this image to any known disease. The plant may have a condition that's not in the training set, or the image quality may be insufficient.",
        "description_ru": "Модель не смогла уверенно отнести изображение ни к одной известной болезни. Возможно, у растения состояние, которого нет в обучающей выборке, или снимок недостаточно чёткий.",
        "treatment_en": [
            "Try taking another photo in brighter, natural light",
            "Fill the frame with the single suspicious leaf",
            "Make sure the image is sharp — wait for autofocus to lock",
            "If symptoms persist, consult a local plant specialist"
        ],
        "treatment_ru": [
            "Сделайте другой снимок при ярком естественном освещении",
            "Заполните кадр одним подозрительным листом",
            "Убедитесь, что фото резкое — дождитесь автофокуса",
            "Если симптомы сохраняются, обратитесь к специалисту по растениям"
        ],
        "prevention_en": [
            "Inspect your plant weekly to catch early symptoms",
            "Keep a consistent watering and lighting routine",
            "Keep the growing area clean and clutter-free"
        ],
        "prevention_ru": [
            "Осматривайте растение раз в неделю — так легче заметить ранние симптомы",
            "Поддерживайте постоянный режим полива и освещения",
            "Содержите зону выращивания в чистоте"
        ],
        "is_healthy": False
    }
}
