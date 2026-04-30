"""
Bilingual disease information database for PlantVision.

Stage 2 classifier outputs one of 10 disease classes — the bilingual
content for each lives in ``DISEASES_DATABASE`` below. Pests are not
classifier outputs; they remain a UI hint section via ``PEST_INFO``
and each disease's ``possible_pest_causes`` field.

Final 10 classes (alphabetical): ``anthracnose``,
``bacterial_leaf_spot``, ``gray_mold``, ``healthy``, ``iron_chlorosis``,
``late_blight``, ``mosaic_virus``, ``not_a_plant``, ``powdery_mildew``,
``sunburn``. Authoritative list: ``server/models/classes.json``.
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
        "is_healthy": True,
        "possible_pest_causes": []
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
        "is_healthy": False,
        "possible_pest_causes": ["mealybug"]
    },

    "iron_chlorosis": {
        "name_en": "Iron Chlorosis",
        "name_ru": "Железный хлороз",
        "description_en": "Interveinal yellowing of leaves with green veins still visible — the textbook sign of iron deficiency. The newest leaves go pale first. Often triggered by alkaline tap water, depleted substrate, or root issues caused by overwatering. Sap-sucking pests (spider mites, aphids, scale, mealybugs, whitefly) cause similar symptoms by draining the leaf — check the underside before treating.",
        "description_ru": "Жёлтые листья с зелёными жилками — классический признак нехватки железа. Самые молодые листья бледнеют первыми. Часто возникает из-за щелочной водопроводной воды, истощённого субстрата или проблем с корнями от перелива. Сосущие вредители (паутинный клещ, тля, щитовка, мучнистый червец, белокрылка) дают похожие симптомы — обязательно осмотрите обратную сторону листьев.",
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
        "is_healthy": False,
        "possible_pest_causes": ["spider_mites", "aphids", "scale_insect", "mealybug", "whitefly"]
    },

    "anthracnose": {
        "name_en": "Anthracnose",
        "name_ru": "Антракноз",
        "description_en": "Fungal disease (Colletotrichum) showing dark brown-to-black sunken spots, often with a darker border or yellow halo. Common on ficus, philodendron, palms, anthurium and dracaena, especially after watering with cold water or during prolonged high humidity.",
        "description_ru": "Грибковое заболевание (Colletotrichum) — тёмно-коричневые или чёрные вдавленные пятна, часто с более тёмной каймой или жёлтым ореолом. Часто на фикусах, филодендронах, пальмах, антуриуме и драцене, особенно после полива холодной водой или при длительной высокой влажности.",
        "treatment_en": [
            "Cut off the worst-affected leaves with sterile scissors and dispose (do not compost)",
            "Spray with a copper-based fungicide or chlorothalonil weekly for 3 weeks",
            "Reduce overhead watering — keep foliage dry, water only the substrate",
            "Improve airflow; isolate the plant for 2–3 weeks",
            "Disinfect tools with 70 % isopropyl alcohol between plants"
        ],
        "treatment_ru": [
            "Срежьте сильно поражённые листья стерильными ножницами, выбросьте (не в компост)",
            "Опрыскивайте медьсодержащим фунгицидом или хлороталонилом раз в неделю в течение 3 недель",
            "Прекратите полив сверху — листья держите сухими, поливайте только в субстрат",
            "Обеспечьте циркуляцию воздуха, изолируйте растение на 2–3 недели",
            "Дезинфицируйте инструмент 70% спиртом между растениями"
        ],
        "prevention_en": [
            "Avoid wetting the leaves; use bottom watering when possible",
            "Don't crowd plants — anthracnose loves still humid air",
            "Quarantine new plants for 2 weeks before adding to the collection",
            "Wipe leaves with a damp cloth monthly so spores don't accumulate"
        ],
        "prevention_ru": [
            "Не мочите листья — по возможности поливайте снизу",
            "Не размещайте растения вплотную — антракноз любит застойный влажный воздух",
            "Новые растения держите 2 недели на карантине",
            "Раз в месяц протирайте листья влажной тряпкой — споры не скапливаются"
        ],
        "is_healthy": False,
        "possible_pest_causes": ["spider_mites"]
    },

    "gray_mold": {
        "name_en": "Gray Mold (Botrytis)",
        "name_ru": "Серая гниль (ботритис)",
        "description_en": "Botrytis cinerea causes a fluffy gray mold on flowers, buds, soft new growth and damaged leaves. Develops in cool (15–22 °C) high-humidity conditions with poor airflow — typical for crowded windowsills in winter. Affected tissue first looks water-soaked, then collapses under a dusty gray coating.",
        "description_ru": "Botrytis cinerea — пушистый серый налёт на цветках, бутонах, молодых побегах и повреждённых листьях. Развивается в прохладе (15–22 °C) при высокой влажности и слабой циркуляции воздуха — типично для зимних подоконников с тесно стоящими растениями. Поражённая ткань сначала водянистая, потом проседает под серой пыльной коркой.",
        "treatment_en": [
            "Remove all affected parts (leaves, flowers, buds) with sterile shears; bag and discard",
            "Spray remaining tissue with a Botrytis-targeted fungicide (e.g. fenhexamid or boscalid)",
            "Lower humidity below 65 % and stop misting until the infection clears",
            "Increase airflow with a small fan; do not let leaves stay wet overnight",
            "Move the plant to a warmer (>22 °C), brighter location"
        ],
        "treatment_ru": [
            "Удалите все поражённые части (листья, цветы, бутоны) стерильным секатором, упакуйте и выбросьте",
            "Обработайте оставшуюся ткань фунгицидом против ботритиса (фенгексамид, боскалид)",
            "Снизьте влажность ниже 65 %, прекратите опрыскивание до устранения инфекции",
            "Включите небольшой вентилятор, не оставляйте листья мокрыми на ночь",
            "Переставьте растение в более тёплое (>22 °C) и светлое место"
        ],
        "prevention_en": [
            "Don't crowd plants — gray mold thrives in stagnant air",
            "Promptly remove faded flowers and dead leaves — Botrytis colonises dying tissue first",
            "Water in the morning so leaves dry before evening",
            "Avoid bumping/handling soft growth — wounds are infection sites"
        ],
        "prevention_ru": [
            "Не размещайте растения вплотную — серая гниль любит застойный воздух",
            "Сразу удаляйте увядшие цветы и сухие листья — ботритис первым заселяет отмершую ткань",
            "Поливайте утром, чтобы листья высохли до вечера",
            "Не задевайте мягкие побеги — раны становятся воротами инфекции"
        ],
        "is_healthy": False,
        "possible_pest_causes": ["mealybug"]
    },

    "late_blight": {
        "name_en": "Late Blight (Phytophthora)",
        "name_ru": "Фитофтороз",
        "description_en": "Phytophthora infestans (and related oomycetes) cause large brown water-soaked patches that spread very fast — a healthy leaf can collapse in 24–48 hours. The patches may have a fuzzy white margin on the underside in humid conditions. Common on tomato seedlings indoors, but also affects calathea, peperomia and seedlings of any species.",
        "description_ru": "Phytophthora infestans (и родственные оомицеты) — крупные коричневые водянистые пятна, расползающиеся очень быстро: здоровый лист может погибнуть за 24–48 часов. По краю пятна снизу может быть пушистый белый налёт при высокой влажности. Часто на рассаде томатов в помещении, а также на калатее, пеперомии и рассаде любых видов.",
        "treatment_en": [
            "Quickly isolate the plant — late blight spreads through air and water splash",
            "Cut off all affected leaves and stems plus 2 cm of healthy tissue around them",
            "Apply a systemic fungicide effective against oomycetes (e.g. metalaxyl, fosetyl-Al)",
            "Repot into fresh substrate; old soil may carry resting spores",
            "If more than 50 % of the plant is affected, dispose of the plant — recovery is unlikely"
        ],
        "treatment_ru": [
            "Срочно изолируйте растение — фитофтороз распространяется по воздуху и брызгами воды",
            "Срежьте все поражённые листья и стебли с захватом 2 см здоровой ткани",
            "Примените системный фунгицид против оомицетов (металаксил, фосэтил-Al)",
            "Пересадите в свежий субстрат — в старом могут быть покоящиеся споры",
            "Если поражено больше 50 % растения, выбросьте его — спасти, скорее всего, не получится"
        ],
        "prevention_en": [
            "Never reuse old substrate from a sick plant",
            "Sterilise pots and seedling trays with a 1:10 bleach solution before reuse",
            "Water the substrate, not the leaves; keep the plant dry overnight",
            "Don't crowd seedlings — Phytophthora spreads fastest in dense clusters"
        ],
        "prevention_ru": [
            "Никогда не используйте субстрат от больного растения повторно",
            "Перед повторным использованием стерилизуйте горшки и рассадные кассеты раствором 1:10 хлорки",
            "Поливайте субстрат, а не листья; не оставляйте растение мокрым на ночь",
            "Не загущайте посадку рассады — фитофтороз быстрее всего летит между растениями вплотную"
        ],
        "is_healthy": False,
        "possible_pest_causes": []
    },

    "sunburn": {
        "name_en": "Sunburn / Leaf Scorch",
        "name_ru": "Солнечный ожог",
        "description_en": "Dry, brown, papery patches that appear on the side of leaves facing direct sunlight (typically south or south-west windows). Tissue feels brittle, not fuzzy or wet. Often after an abrupt move from shade to sun, or after watering droplets focus light onto leaves. Common on calathea, fittonia, ferns, dracaena.",
        "description_ru": "Сухие коричневые «бумажные» пятна с той стороны листа, которая обращена к прямому солнцу (обычно южные/юго-западные окна). Ткань ломкая, не пушистая и не мокрая. Часто после резкой перестановки из тени на свет или после полива, когда капли воды фокусируют свет на листьях. Часто на калатее, фиттонии, папоротниках, драцене.",
        "treatment_en": [
            "Move the plant 30–50 cm away from the window or behind a thin curtain",
            "Trim off heavily damaged leaves once the rest of the plant has stabilised",
            "Don't water any harder than usual — burned tissue won't grow back, but the plant won't get worse if light is reduced",
            "Avoid misting at midday — water droplets can act like lenses",
            "Acclimate plants to brighter spots gradually (15–30 minutes more sun per day)"
        ],
        "treatment_ru": [
            "Отодвиньте растение на 30–50 см от окна или повесьте лёгкую штору",
            "Сильно повреждённые листья срежьте, когда остальная часть растения стабилизируется",
            "Не увеличивайте полив — обожжённая ткань не восстановится, но растение остановит ухудшение при снижении света",
            "Не опрыскивайте растение в обед — капли воды работают как линзы",
            "Приучайте растения к более светлым местам постепенно (по 15–30 мин больше солнца в день)"
        ],
        "prevention_en": [
            "Use a thin curtain or paper sheet on south/south-west windows in summer",
            "Don't move shade plants directly into bright light — acclimate over a week",
            "Water in the morning or evening, not under direct sun",
            "Wipe water droplets off the leaves after misting if the plant sits in sun"
        ],
        "prevention_ru": [
            "Летом на южных/юго-западных окнах используйте лёгкую штору или лист бумаги",
            "Не переставляйте теневые растения сразу на яркий свет — акклиматизируйте неделю",
            "Поливайте утром или вечером, не под прямым солнцем",
            "Если растение стоит на солнце, после опрыскивания убирайте капли воды с листьев"
        ],
        "is_healthy": False,
        "possible_pest_causes": []
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
        "is_healthy": False,
        "possible_pest_causes": []
    },

    "bacterial_leaf_spot": {
        "name_en": "Bacterial Leaf Spot",
        "name_ru": "Бактериальная пятнистость",
        "description_en": "Small angular brown-to-black water-soaked spots, often surrounded by a yellow halo. Caused by Pseudomonas or Xanthomonas bacteria — common on dieffenbachia, philodendron, syngonium, anthurium and other Araceae kept in damp, poorly ventilated rooms. Spots stay confined to areas between leaf veins because the bacteria spread through the water film on the leaf surface.",
        "description_ru": "Мелкие угловатые тёмные пятна со слегка водянистой каймой и жёлтым ореолом вокруг. Вызывают бактерии Pseudomonas и Xanthomonas — часто встречаются на диффенбахиях, филодендронах, сингониумах и других ароидных в сырых плохо проветриваемых комнатах. Пятна остаются между жилками — бактерии переносятся плёнкой воды на поверхности листа.",
        "treatment_en": [
            "Remove and bin (do not compost) all visibly affected leaves",
            "Move the plant away from neighbours — bacterial spots spread quickly",
            "Stop misting and overhead watering; water at the soil level only",
            "Spray a copper-based fungicide (copper hydroxide / copper sulfate) per label",
            "Sterilise scissors with isopropyl alcohol before each cut"
        ],
        "treatment_ru": [
            "Удалите и выбросьте (не в компост) все поражённые листья",
            "Изолируйте растение от соседей — бактерия быстро перемещается",
            "Прекратите опрыскивание и полив сверху; поливайте только под корень",
            "Обработайте препаратом на основе меди (хлорокись меди / медный купорос) по инструкции",
            "Ножницы перед каждым срезом протирайте изопропиловым спиртом"
        ],
        "prevention_en": [
            "Keep leaves dry — water the soil, not the foliage",
            "Provide good air circulation; avoid crowding plants together",
            "Quarantine new plants for 2 weeks before placing them with the rest",
            "Avoid handling plants when leaves are wet"
        ],
        "prevention_ru": [
            "Держите листья сухими — поливайте под корень, а не сверху",
            "Обеспечьте циркуляцию воздуха; не ставьте растения вплотную",
            "Новые растения держите 2 недели на карантине",
            "Не трогайте растения, когда листья мокрые — бактерии переносятся руками"
        ],
        "is_healthy": False,
        "possible_pest_causes": []
    },

    "mosaic_virus": {
        "name_en": "Mosaic Virus",
        "name_ru": "Мозаичный вирус",
        "description_en": "Irregular light-and-dark green or yellow mottling, often with crinkled or distorted leaves. Caused by tobamoviruses (TMV / ToMV) and related viruses transmitted by aphids, thrips or contaminated tools. Common on hoya, brugmansia, dracaena and indoor edibles like peppers. There is no cure: virus-infected plants stay infected for life and remain a source of spread to healthy neighbours.",
        "description_ru": "Светло-тёмная зелёно-жёлтая мозаика на листьях, часто со скрученностью или искривлением. Вызывают тобамовирусы (TMV / ToMV) и родственные вирусы; переносятся тлёй, трипсами и заражённым инструментом. Часто встречается на хойе, бругмансии, драцене и комнатных перцах. Лекарства нет: заражённое растение остаётся источником вируса до конца жизни.",
        "treatment_en": [
            "Isolate the plant immediately — viruses spread on hands and tools",
            "There is no cure; if symptoms are severe, the safest option is to dispose of the plant",
            "Sterilise pots, tools, and your hands with 10% bleach or 70% alcohol after handling",
            "Inspect neighbours over the following weeks for early mottling",
            "Control aphids and thrips, the main vector pests, on remaining plants"
        ],
        "treatment_ru": [
            "Сразу изолируйте растение — вирусы переносятся через руки и инструмент",
            "Лечения нет; при сильных симптомах безопаснее всего утилизировать растение",
            "Дезинфицируйте горшки, инструмент и руки 10% хлоркой или 70% спиртом",
            "В течение нескольких недель осматривайте соседние растения на мозаику",
            "Боритесь с тлёй и трипсами на остальных растениях — это переносчики"
        ],
        "prevention_en": [
            "Buy plants from reputable sources; reject any with mottled leaves",
            "Sterilise scissors and stakes between plants",
            "Keep aphid and thrips populations under control — they spread the virus",
            "Wash hands after handling tobacco products before touching plants (TMV survives in cured tobacco)"
        ],
        "prevention_ru": [
            "Покупайте растения у проверенных продавцов; не берите с пятнистыми листьями",
            "Дезинфицируйте ножницы и опоры между растениями",
            "Контролируйте тлю и трипсов — они переносят вирус",
            "Мойте руки после контакта с табаком перед работой с растениями (TMV выживает в табаке)"
        ],
        "is_healthy": False,
        "possible_pest_causes": ["aphids", "whitefly"]
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
        "is_healthy": False,
        "possible_pest_causes": []
    }
}


# ── Pest hint registry ───────────────────────────────────────────────
#
# Pests are NOT classifier outputs; they're surfaced as a UI hint
# section ("possible pest causes") on each disease card. Diseases
# reference these keys via ``possible_pest_causes``. Single source of
# truth: name, short summary (what it looks like), the 1-line
# "if you see this" hint and a category for grouping.

PEST_INFO = {
    "spider_mites": {
        "name_en": "Spider Mites",
        "name_ru": "Паутинный клещ",
        "category": "mite",
        "summary_en": "Tiny mites that cause fine webbing and stippled yellow spots on leaves; hide on the leaf underside.",
        "summary_ru": "Мелкие клещи: тонкая паутина и светлые точки на листьях, прячутся снизу листа.",
        "hint_en": "Look on leaf undersides with a magnifier — fine webs and red/brown specks.",
        "hint_ru": "Посмотрите на нижнюю сторону листа с лупой — тонкая паутина и красно-коричневые точки."
    },
    "aphids": {
        "name_en": "Aphids",
        "name_ru": "Тля",
        "category": "sap_sucker",
        "summary_en": "Soft-bodied insects (1-3 mm) that cluster on young shoots and flower buds.",
        "summary_ru": "Мягкотелые насекомые (1-3 мм), скапливаются на молодых побегах и бутонах.",
        "hint_en": "Check soft new growth and the underside of new leaves for green/black clusters.",
        "hint_ru": "Осмотрите молодые побеги и обратную сторону свежих листьев — зелёные или чёрные скопления."
    },
    "mealybug": {
        "name_en": "Mealybugs",
        "name_ru": "Мучнистый червец",
        "category": "sap_sucker",
        "summary_en": "Soft-bodied insects covered in white cottony wax; hide in leaf axils and along stems.",
        "summary_ru": "Мягкотелые насекомые в белом ватообразном воске; прячутся в пазухах листьев и на стеблях.",
        "hint_en": "Look for white cottony tufts in leaf axils and where stems join.",
        "hint_ru": "Ищите белые ватные комочки в пазухах листьев и в местах соединения стеблей."
    },
    "scale_insect": {
        "name_en": "Scale Insects",
        "name_ru": "Щитовка",
        "category": "sap_sucker",
        "summary_en": "Brown or yellowish hard or soft 'plaques' (2-5 mm) glued to stems and leaf veins.",
        "summary_ru": "Коричневые или желтоватые «бляшки» (2-5 мм), приклеенные к стеблям и жилкам листьев.",
        "hint_en": "Try scraping a brown 'spot' on a stem with your nail — if it lifts off, it's scale.",
        "hint_ru": "Попробуйте сковырнуть «пятно» на стебле ногтем — если отделяется, это щитовка."
    },
    "whitefly": {
        "name_en": "Whitefly",
        "name_ru": "Белокрылка",
        "category": "sap_sucker",
        "summary_en": "Small (~1.5 mm) white flying insects on leaf undersides; a cloud takes off when shaken.",
        "summary_ru": "Мелкие (~1.5 мм) белые летающие насекомые на обратной стороне листьев; при встряхивании взлетают облачком.",
        "hint_en": "Gently shake the plant — a cloud of tiny white moths is whitefly.",
        "hint_ru": "Слегка встряхните растение — если поднимается облачко белых «мошек», это белокрылка."
    }
}


def pest_hints(disease_class: str) -> list[dict]:
    """Resolve ``possible_pest_causes`` into a list of pest info dicts.

    Returns an empty list when the disease has no associated pests
    (e.g. ``healthy``, ``not_a_plant``, or purely environmental
    classes like ``sunburn`` and ``late_blight``).
    """
    info = DISEASES_DATABASE.get(disease_class) or DISEASES_DATABASE["unknown"]
    keys = info.get("possible_pest_causes") or []
    out: list[dict] = []
    for k in keys:
        pest = PEST_INFO.get(k)
        if pest is None:
            continue
        out.append({"key": k, **pest})
    return out
