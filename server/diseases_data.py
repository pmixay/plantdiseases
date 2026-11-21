"""
Bilingual disease information database for PlantDiseases.

Stage 2 classifier outputs one of the 9 trainable v2.x classes — the
bilingual content for each lives in ``DISEASES_DATABASE`` below.

Trainable v2.x classes (alphabetical): ``gray_mold_botrytis``,
``healthy``, ``leaf_scorch_underwatering``, ``leaf_spot``,
``not_a_plant``, ``nutrient_deficiency``, ``pests``, ``powdery_mildew``,
``viral_disease``. ``leaf_spot`` is the merged bacterial+fungal
leaf-spot class and ``pests`` is the merged mealybug+scale class — in
each case the two are not reliably separable from a phone photo, so they
share one content card.

Authoritative list: ``server/models/classes.json``.

Forward-compat entries for conditions not yet in the classifier head
(``edema``, ``sunburn_or_scorch``, and the staged ``pests`` class — a
single combined head for mealybugs + scale) are kept in the database —
production lookup is ``DISEASES_DATABASE.get(cls, DISEASES_DATABASE['unknown'])``
so unknown labels are tolerated, and keeping the cards means enabling
those classes later (``INCLUDE_PEST_CLASSES`` in ``train_notebook.ipynb``
§1 for the pests) is a training-time switch, not a content port. The
per-pest ``mealybug`` / ``scale`` cards are retained for richer copy if
the combined class is ever split back out.
"""

import copy

DISEASES_DATABASE = {
    "healthy": {
        "name_en": "Healthy Plant",
        "name_ru": "Здоровое растение",
        "description_en": "The plant looks healthy - leaves show even green colour, no spots, no webbing, no powder. Keep up the current care routine.",
        "description_ru": "Растение выглядит здоровым - листья равномерно-зелёные, без пятен, паутины и налёта. Продолжайте текущий уход.",
        "treatment_en": [
            "No treatment needed - your plant is doing well",
            "Continue regular watering and feeding schedule",
            "Wipe dust from leaves once a week to keep them breathing"
        ],
        "treatment_ru": [
            "Лечение не требуется - растение чувствует себя хорошо",
            "Продолжайте регулярный полив и подкормку",
            "Раз в неделю протирайте листья от пыли - это помогает им «дышать»"
        ],
        "prevention_en": [
            "Water only after the top 2-3 cm of substrate dry out",
            "Provide bright, indirect light for most species",
            "Fertilise during spring and summer growth",
            "Inspect new leaves weekly for the first signs of trouble"
        ],
        "prevention_ru": [
            "Поливайте только когда верхние 2-3 см субстрата просохли",
            "Обеспечьте яркое, но рассеянное освещение",
            "Весной и летом вносите удобрения каждые 2-3 недели",
            "Еженедельно осматривайте новые листья на ранние признаки проблем"
        ],
        "is_healthy": True,
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
            "Stop misting - keep the foliage dry"
        ],
        "treatment_ru": [
            "Удалите наиболее поражённые листья и выбросьте их (не в компост)",
            "Опрыскайте раствором: 1 ч. ложка соды + 1 литр воды + капля мыла",
            "Или применяйте масло нима / фунгицид на основе серы раз в неделю",
            "Переставьте растение в проветриваемое и более светлое место",
            "Прекратите опрыскивание - держите листья сухими"
        ],
        "prevention_en": [
            "Keep humidity between 40-60 %; avoid stagnant, damp air",
            "Ensure good air circulation - don't crowd plants",
            "Water at the soil level, not over the leaves",
            "Quarantine new plants for two weeks before placing them next to the rest"
        ],
        "prevention_ru": [
            "Поддерживайте влажность 40-60 %; избегайте застойного сырого воздуха",
            "Обеспечьте циркуляцию воздуха - не ставьте растения вплотную",
            "Поливайте под корень, а не сверху по листьям",
            "Новые растения держите 2 недели на карантине"
        ],
        "is_healthy": False,
    },

    "gray_mold_botrytis": {
        "name_en": "Gray Mold (Botrytis)",
        "name_ru": "Серая гниль (ботритис)",
        "description_en": "Botrytis cinerea causes a fluffy gray mold on flowers, buds, soft new growth and damaged leaves. Develops in cool (15-22 °C) high-humidity conditions with poor airflow - typical for crowded windowsills in winter. Affected tissue first looks water-soaked, then collapses under a dusty gray coating.",
        "description_ru": "Botrytis cinerea - пушистый серый налёт на цветках, бутонах, молодых побегах и повреждённых листьях. Развивается в прохладе (15-22 °C) при высокой влажности и слабой циркуляции воздуха - типично для зимних подоконников с тесно стоящими растениями. Поражённая ткань сначала водянистая, потом проседает под серой пыльной коркой.",
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
            "Don't crowd plants - gray mold thrives in stagnant air",
            "Promptly remove faded flowers and dead leaves - Botrytis colonises dying tissue first",
            "Water in the morning so leaves dry before evening",
            "Avoid bumping/handling soft growth - wounds are infection sites"
        ],
        "prevention_ru": [
            "Не размещайте растения вплотную - серая гниль любит застойный воздух",
            "Сразу удаляйте увядшие цветы и сухие листья - ботритис первым заселяет отмершую ткань",
            "Поливайте утром, чтобы листья высохли до вечера",
            "Не задевайте мягкие побеги - раны становятся воротами инфекции"
        ],
        "is_healthy": False,
    },

    "leaf_spot": {
        "name_en": "Leaf Spot",
        "name_ru": "Пятнистость листьев",
        "description_en": "Umbrella class for fungal and bacterial leaf spot. Fungal spots (anthracnose, cercospora, alternaria) appear as dark sunken lesions with yellow halos, grey-brown spots with a dark border, or concentric brown rings. Bacterial spots (Pseudomonas, Xanthomonas) are small angular water-soaked spots between the veins, often with a yellow halo. Both thrive on wet foliage and poor airflow, and the distinction is genuinely hard to make from a phone photo - the care below covers both.",
        "description_ru": "Сборный класс для грибковой и бактериальной пятнистости. Грибковые пятна (антракноз, церкоспороз, альтернариоз) - тёмные вдавленные пятна с жёлтым ореолом, серо-коричневые пятна с тёмной каймой или концентрические коричневые кольца. Бактериальные пятна (Pseudomonas, Xanthomonas) - мелкие угловатые водянистые пятна между жилками, часто с жёлтым ореолом. И те и другие развиваются на мокрых листьях при слабой вентиляции, а различить их по фото с телефона практически невозможно - уход ниже подходит для обоих случаев.",
        "treatment_en": [
            "Cut off the worst-affected leaves with sterile scissors and dispose (do not compost)",
            "Stop misting and overhead watering - keep the foliage dry, water the substrate only",
            "Spray a copper-based fungicide/bactericide (copper hydroxide) or chlorothalonil weekly for 3-4 weeks per label",
            "Improve airflow and isolate the plant for 2-3 weeks",
            "Disinfect tools with 70 % isopropyl alcohol between plants and between cuts"
        ],
        "treatment_ru": [
            "Срежьте сильно поражённые листья стерильными ножницами, выбросьте (не в компост)",
            "Прекратите опрыскивание и полив сверху - листья держите сухими, поливайте только в субстрат",
            "Опрыскивайте медьсодержащим фунгицидом/бактерицидом (гидроксид меди) или хлороталонилом раз в неделю в течение 3-4 недель по инструкции",
            "Обеспечьте циркуляцию воздуха и изолируйте растение на 2-3 недели",
            "Дезинфицируйте инструмент 70% спиртом между растениями и между срезами"
        ],
        "prevention_en": [
            "Avoid wetting the leaves; use bottom watering when possible",
            "Don't crowd plants - spores and bacteria love still, humid air",
            "Quarantine new plants for 2 weeks before adding to the collection",
            "Wipe leaves with a damp cloth monthly so spores don't accumulate"
        ],
        "prevention_ru": [
            "Не мочите листья - по возможности поливайте снизу",
            "Не размещайте растения вплотную - споры и бактерии любят застойный влажный воздух",
            "Новые растения держите 2 недели на карантине",
            "Раз в месяц протирайте листья влажной тряпкой - споры не скапливаются"
        ],
        "is_healthy": False,
    },

    "viral_disease": {
        "name_en": "Viral Disease",
        "name_ru": "Вирусное заболевание",
        "description_en": "Umbrella class for plant viruses with leaf-level symptoms - mosaic (light/dark green or yellow mottling), leaf curl (deformed, twisted leaves) and ringspot (concentric chlorotic rings). Caused by tobamoviruses, geminiviruses and others, transmitted by sap-sucking insects, contaminated tools and infected planting stock. There is no cure: virus-infected plants stay infected for life and remain a source of spread to neighbours.",
        "description_ru": "Сборный класс для вирусов растений с симптомами на листьях - мозаика (светло-тёмная или жёлтая мозаичная пёстрость), курчавость (скрученные деформированные листья), кольцевая пятнистость (концентрические хлоротичные кольца). Вызываются тобамо-, гемини- и другими вирусами; переносятся сосущими насекомыми, заражённым инструментом и посадочным материалом. Лечения нет: заражённое растение остаётся источником вируса до конца жизни.",
        "treatment_en": [
            "Isolate the plant immediately - viruses spread on hands and tools",
            "There is no cure; if symptoms are severe, the safest option is to dispose of the plant",
            "Sterilise pots, tools, and your hands with 10% bleach or 70% alcohol after handling",
            "Inspect neighbours over the following weeks for early mottling or curl",
            "Control sap-sucking vectors (aphids, thrips, whitefly) on remaining plants"
        ],
        "treatment_ru": [
            "Сразу изолируйте растение - вирусы переносятся через руки и инструмент",
            "Лечения нет; при сильных симптомах безопаснее всего утилизировать растение",
            "Дезинфицируйте горшки, инструмент и руки 10% хлоркой или 70% спиртом",
            "В течение нескольких недель осматривайте соседние растения на мозаику и курчавость",
            "Боритесь с сосущими насекомыми (тля, трипсы, белокрылка) на остальных растениях"
        ],
        "prevention_en": [
            "Buy plants from reputable sources; reject any with mottled or curled leaves",
            "Sterilise scissors and stakes between plants",
            "Keep aphid / thrips / whitefly populations under control - they spread the virus",
            "Wash hands after handling tobacco products before touching plants (TMV survives in cured tobacco)"
        ],
        "prevention_ru": [
            "Покупайте растения у проверенных продавцов; не берите с пятнистыми или курчавыми листьями",
            "Дезинфицируйте ножницы и опоры между растениями",
            "Контролируйте тлю, трипсов и белокрылку - они переносят вирус",
            "Мойте руки после контакта с табаком перед работой с растениями (TMV выживает в табаке)"
        ],
        "is_healthy": False,
    },

    "edema": {
        "name_en": "Edema (Oedema)",
        "name_ru": "Эдема (отёчность листьев)",
        "description_en": "Physiological disorder, not an infection. Small water-blister bumps on the underside of leaves that later turn into corky tan or rust-coloured patches. Triggered when the roots take up water faster than the leaves can transpire it - typical on peperomia, succulents, ivy and pelargonium during cool, dim, humid weather with overwatering. No pathogen; tissue damage is permanent on affected leaves but new growth is normal once conditions are corrected.",
        "description_ru": "Физиологическое нарушение, а не инфекция. Мелкие водянистые «пузырьки» с нижней стороны листа, которые позже превращаются в пробковые рыжеватые или ржаво-коричневые пятна. Возникает, когда корни поглощают воду быстрее, чем листья её испаряют - типично для пеперомии, суккулентов, плюща и пеларгонии в прохладную пасмурную влажную погоду при переливе. Возбудителя нет; повреждённая ткань уже не восстановится, но новые листья растут нормально после коррекции условий.",
        "treatment_en": [
            "Reduce watering - let the top 3-5 cm of substrate dry between waterings",
            "Move the plant to brighter, warmer light to boost transpiration",
            "Improve airflow with a small fan; lower room humidity if it's above 70 %",
            "Don't remove affected leaves unless they collapse - the plant still photosynthesises with them",
            "Avoid fertilising until the new growth comes in clean"
        ],
        "treatment_ru": [
            "Сократите полив - верхние 3-5 см субстрата должны просыхать между поливами",
            "Переставьте растение в более светлое и тёплое место для усиления испарения",
            "Включите вентилятор для циркуляции воздуха; снизьте влажность, если она выше 70 %",
            "Не удаляйте поражённые листья, если они не опадают - растение продолжает с ними фотосинтез",
            "Не подкармливайте до появления чистого нового прироста"
        ],
        "prevention_en": [
            "Match watering to season - winter plants drink much less than in summer",
            "Avoid watering on cold dim mornings; water in the early afternoon when light peaks",
            "Use pots with drainage holes and a chunky, well-aerated substrate",
            "Don't combine high humidity (>70 %) with cool air for long periods"
        ],
        "prevention_ru": [
            "Регулируйте полив по сезону - зимой растениям нужно гораздо меньше воды",
            "Не поливайте в холодные пасмурные утра; лучше - днём, когда света максимум",
            "Используйте горшки с дренажом и рыхлый воздухопроницаемый субстрат",
            "Не сочетайте высокую влажность (>70 %) с прохладным воздухом длительное время"
        ],
        "is_healthy": False,
    },

    "sunburn_or_scorch": {
        "name_en": "Sunburn / Light Scorch",
        "name_ru": "Солнечный ожог / световое выгорание",
        "description_en": "Dry, brown, papery patches that appear on the side of leaves facing direct sunlight (typically south or south-west windows) or directly under harsh grow-lights. Tissue feels brittle, not fuzzy or wet. Often after an abrupt move from shade to sun, a window swap, or after watering droplets focus light onto leaves. Common on calathea, fittonia, ferns, dracaena.",
        "description_ru": "Сухие коричневые «бумажные» пятна с той стороны листа, которая обращена к прямому солнцу (обычно южные/юго-западные окна) или прямо под жёсткой фитолампой. Ткань ломкая, не пушистая и не мокрая. Часто после резкой перестановки из тени на свет, смены окна или полива, когда капли воды фокусируют свет на листьях. Часто на калатее, фиттонии, папоротниках, драцене.",
        "treatment_en": [
            "Move the plant 30-50 cm away from the window or behind a thin curtain",
            "Lift grow-lights to ≥30 cm above the canopy or dim them",
            "Trim off heavily damaged leaves once the rest of the plant has stabilised",
            "Don't water any harder than usual - burned tissue won't grow back",
            "Acclimate plants to brighter spots gradually (15-30 minutes more sun per day)"
        ],
        "treatment_ru": [
            "Отодвиньте растение на 30-50 см от окна или повесьте лёгкую штору",
            "Поднимите фитолампу на ≥30 см над кроной или уменьшите её мощность",
            "Сильно повреждённые листья срежьте, когда остальная часть растения стабилизируется",
            "Не увеличивайте полив - обожжённая ткань уже не восстановится",
            "Приучайте растения к более светлым местам постепенно (по 15-30 мин больше солнца в день)"
        ],
        "prevention_en": [
            "Use a thin curtain or paper sheet on south/south-west windows in summer",
            "Don't move shade plants directly into bright light - acclimate over a week",
            "Keep grow-lights at the manufacturer-recommended distance, never lower",
            "Wipe water droplets off the leaves after misting if the plant sits in sun"
        ],
        "prevention_ru": [
            "Летом на южных/юго-западных окнах используйте лёгкую штору или лист бумаги",
            "Не переставляйте теневые растения сразу на яркий свет - акклиматизируйте неделю",
            "Держите фитолампу на рекомендованном производителем расстоянии, не ниже",
            "Если растение стоит на солнце, после опрыскивания убирайте капли воды с листьев"
        ],
        "is_healthy": False,
    },

    "leaf_scorch_underwatering": {
        "name_en": "Leaf Scorch (Under-watering)",
        "name_ru": "Краевой ожог (недолив)",
        "description_en": "Dry crispy browning that starts at leaf tips and edges and works inward, often with curling and a generally limp plant. Caused by chronic under-watering, prolonged low humidity, or salt build-up from hard tap water - the leaf can't keep up with transpiration. Different from sunburn: damage is on the leaf rim regardless of which side faces light. Common on calathea, maranta, ferns, peace lily.",
        "description_ru": "Сухое хрустящее побурение, которое начинается с кончиков и краёв листа и идёт внутрь, часто со скручиванием и общим увядшим видом растения. Причина - хронический недолив, длительная низкая влажность или накопление солей от жёсткой водопроводной воды: лист не успевает за испарением. Отличие от солнечного ожога: повреждение на кромке листа независимо от того, какая сторона обращена к свету. Часто на калатее, маранте, папоротниках, спатифиллуме.",
        "treatment_en": [
            "Soak the pot in a bowl of room-temperature water for 20-30 minutes, then drain fully",
            "Establish a calendar watering check - finger-test the substrate every 2-3 days",
            "Switch to filtered or rainwater if your tap water is hard (over 200 ppm)",
            "Trim off the dry brown edges with sharp scissors - they won't recover",
            "Raise humidity around sensitive plants with a pebble tray or small humidifier"
        ],
        "treatment_ru": [
            "Опустите горшок в таз с водой комнатной температуры на 20-30 минут, затем дайте стечь",
            "Введите регулярные проверки полива - пальцем проверяйте субстрат каждые 2-3 дня",
            "Если водопроводная вода жёсткая (>200 ppm), перейдите на фильтрованную или дождевую",
            "Сухие коричневые края обрежьте острыми ножницами - они уже не восстановятся",
            "Чувствительным растениям поднимите влажность поддоном с галькой или увлажнителем"
        ],
        "prevention_en": [
            "Don't rely on a fixed-day schedule - substrate dryness drives watering, not the calendar",
            "Use light pots so you can lift them and feel when they're dry",
            "Group humidity-loving plants together to raise local humidity",
            "Flush the substrate with plain water every 2-3 months to wash out salts"
        ],
        "prevention_ru": [
            "Не привязывайтесь к жёсткому графику - поливайте по сухости субстрата, а не по календарю",
            "Используйте лёгкие горшки - по весу легко понять, когда субстрат сухой",
            "Ставьте влаголюбивые растения группой - это поднимает локальную влажность",
            "Раз в 2-3 месяца промывайте субстрат чистой водой, чтобы вымыть соли"
        ],
        "is_healthy": False,
    },

    "nutrient_deficiency": {
        "name_en": "Nutrient Deficiency",
        "name_ru": "Дефицит питательных элементов",
        "description_en": "Umbrella class for the four most common deficiencies in potted and garden plants - iron (Fe), nitrogen (N), magnesium (Mg) and potassium (K). Iron and magnesium show interveinal yellowing with green veins (Fe on the youngest leaves, Mg on older ones). Nitrogen yellows the whole oldest leaves first. Potassium burns the leaf edges of older leaves. Often triggered by hard alkaline tap water, depleted substrate, or root issues that block uptake.",
        "description_ru": "Сборный класс для четырёх самых частых дефицитов у горшечных и садовых растений - железо (Fe), азот (N), магний (Mg), калий (K). Железо и магний - межжилковый хлороз с зелёными жилками (Fe на самых молодых листьях, Mg на старых). Азот - равномерное пожелтение самых старых листьев. Калий - бурение по краю старых листьев. Часто провоцируется жёсткой щелочной водой, истощённым субстратом или проблемами с корнями, мешающими усвоению.",
        "treatment_en": [
            "Check the substrate first - overwatering or root rot looks just like deficiency",
            "Switch to a balanced liquid fertiliser (NPK ~10-10-10) at half strength every 2-3 weeks during growth",
            "If yellowing is between the veins on new leaves, add a chelated iron + magnesium feed",
            "If the oldest leaves yellow uniformly, you're under-feeding nitrogen - bump the schedule",
            "If roots smell sour or look brown, repot into fresh, well-draining substrate"
        ],
        "treatment_ru": [
            "Сначала проверьте субстрат - перелив или корневая гниль выглядят как дефицит",
            "Перейдите на сбалансированное жидкое удобрение (NPK ~10-10-10) в половинной дозе раз в 2-3 недели в активный сезон",
            "Если жилки зелёные, а ткань между ними жёлтая на молодых листьях - добавьте хелат железа + магний",
            "Если самые старые листья равномерно желтеют - мало азота, увеличьте подкормку",
            "Если корни пахнут кислым или потемнели - пересадите в свежий рыхлый субстрат"
        ],
        "prevention_en": [
            "Water only after the top of the substrate dries - don't keep it constantly wet",
            "Feed during active growth (spring-summer); skip fertiliser in winter",
            "Use pots with drainage holes; never let the pot sit in water",
            "Refresh the substrate every 1-2 years - even good soil gets depleted"
        ],
        "prevention_ru": [
            "Поливайте только после просыхания верха субстрата - не держите постоянно сырым",
            "Подкармливайте в активный сезон (весна-лето), зимой подкормки прекратите",
            "Используйте горшки с дренажными отверстиями; не оставляйте в поддоне с водой",
            "Меняйте субстрат каждые 1-2 года - даже хороший грунт истощается"
        ],
        "is_healthy": False,
    },

    "pests": {
        "name_en": "Sap-sucking Pests (Mealybugs / Scale)",
        "name_ru": "Сосущие вредители (червецы / щитовки)",
        "description_en": "Sap-sucking insects on the leaves and stems. Mealybugs look like small tufts of white cotton wool in leaf axils and along veins; scale insects sit motionless under small brown, tan or waxy dome-shaped shells lined along stems and leaf undersides. Both excrete sticky honeydew that turns into black sooty mould, and both stunt growth and yellow the leaves. Common on ficus, citrus, succulents, orchids, hoya, ivy, ferns and palms - especially in warm, still indoor air.",
        "description_ru": "Сосущие насекомые на листьях и стеблях. Мучнистые червецы похожи на комочки белой ваты в пазухах листьев и вдоль жилок; щитовки неподвижно сидят под мелкими коричневыми или восковыми щитками-куполами вдоль стеблей и на нижней стороне листьев. И те, и другие выделяют липкую медвяную росу, на которой развивается чёрный сажистый грибок, угнетают рост и вызывают пожелтение листьев. Часто поражают фикусы, цитрусовые, суккуленты, орхидеи, хойю, плющ, папоротники и пальмы - особенно в тёплом застойном воздухе.",
        "treatment_en": [
            "Isolate the plant immediately - both pests crawl/spread to neighbours",
            "Dab visible clusters with a cotton swab dipped in 70 % isopropyl alcohol; scrape scale shells off with a soft toothbrush",
            "Spray the whole plant with insecticidal soap, neem or horticultural oil to smother survivors; repeat weekly for 3-4 weeks",
            "Prune and bin badly encrusted stems",
            "For heavy or persistent infestations use a systemic insecticide per the label; check the root ball too - root mealybugs hide in the substrate"
        ],
        "treatment_ru": [
            "Сразу изолируйте растение - оба вредителя переползают на соседние",
            "Обработайте видимые скопления ватной палочкой с 70 % изопропиловым спиртом; щитки соскоблите мягкой зубной щёткой",
            "Опрыскайте всё растение инсектицидным мылом, маслом нима или садовым маслом, чтобы задушить выживших; повторяйте раз в неделю 3-4 недели",
            "Сильно облепленные стебли срежьте и выбросьте",
            "При сильном или упорном заражении примените системный инсектицид по инструкции; проверьте корневой ком - корневые червецы прячутся в субстрате"
        ],
        "prevention_en": [
            "Quarantine new plants for two weeks and inspect leaf axils, stems and leaf undersides before mixing them in",
            "Wipe leaves and check hidden crevices during weekly care",
            "Avoid over-fertilising - lush soft growth attracts both pests",
            "Act at the first white speck or bump; small infestations are far easier to clear"
        ],
        "prevention_ru": [
            "Держите новые растения 2 недели на карантине и осматривайте пазухи листьев, стебли и низ листьев",
            "Протирайте листья и проверяйте скрытые места при еженедельном уходе",
            "Не перекармливайте - мягкий жирующий прирост привлекает обоих вредителей",
            "Реагируйте при первом белом комочке или щитке - мелкое заражение убрать намного легче"
        ],
        "is_healthy": False,
    },

    "mealybug": {
        "name_en": "Mealybugs",
        "name_ru": "Мучнистые червецы",
        "description_en": "Soft-bodied sap-sucking insects that look like small tufts of white cotton wool, usually tucked into leaf axils, along veins and on the undersides of leaves. They excrete sticky honeydew that turns into black sooty mould. Common on succulents, ficus, orchids, hoya and citrus, especially in warm, still indoor air. Heavy infestations stunt growth and yellow the leaves.",
        "description_ru": "Мягкотелые сосущие насекомые, похожие на комочки белой ваты; прячутся в пазухах листьев, вдоль жилок и на нижней стороне листа. Выделяют липкую медвяную росу, на которой развивается чёрный сажистый грибок. Часто поражают суккуленты, фикусы, орхидеи, хойю и цитрусовые, особенно в тёплом застойном воздухе. При сильном заражении рост угнетается, листья желтеют.",
        "treatment_en": [
            "Isolate the plant immediately - mealybugs crawl to neighbours",
            "Dab each cluster with a cotton swab dipped in 70 % isopropyl alcohol",
            "Spray the whole plant with insecticidal soap or neem oil, repeat weekly for 3-4 weeks",
            "For heavy infestations use a systemic insecticide per the label",
            "Check the root ball too - root mealybugs hide in the substrate"
        ],
        "treatment_ru": [
            "Сразу изолируйте растение - червецы переползают на соседние",
            "Обработайте каждое скопление ватной палочкой с 70 % изопропиловым спиртом",
            "Опрыскайте всё растение инсектицидным мылом или маслом нима, повторяйте раз в неделю 3-4 недели",
            "При сильном заражении примените системный инсектицид по инструкции",
            "Проверьте корневой ком - корневые червецы прячутся в субстрате"
        ],
        "prevention_en": [
            "Quarantine new plants for two weeks and inspect leaf axils before mixing them in",
            "Wipe leaves and check hidden crevices during weekly care",
            "Avoid over-fertilising - lush soft growth attracts mealybugs",
            "Act at the first white speck; small infestations are far easier to clear"
        ],
        "prevention_ru": [
            "Держите новые растения 2 недели на карантине и осматривайте пазухи листьев",
            "Протирайте листья и проверяйте скрытые места при еженедельном уходе",
            "Не перекармливайте - мягкий жирующий прирост привлекает червецов",
            "Реагируйте при первом белом комочке - мелкое заражение убрать намного легче"
        ],
        "is_healthy": False,
    },

    "scale": {
        "name_en": "Scale Insects",
        "name_ru": "Щитовки и ложнощитовки",
        "description_en": "Sap-sucking insects that sit motionless under small brown, tan or waxy dome-shaped shells, lined up along stems and leaf veins. Soft scale also produces sticky honeydew and sooty mould; armoured scale does not. Common on ficus, citrus, ivy, ferns and palms. Because the shell protects the insect, scale is stubborn and often noticed only once leaves yellow and drop.",
        "description_ru": "Сосущие насекомые, неподвижно сидящие под мелкими коричневыми или восковыми щитками-куполами, выстроенными вдоль стеблей и жилок листьев. Ложнощитовки выделяют липкую медвяную росу и сажистый грибок, щитовки - нет. Часто встречаются на фикусах, цитрусовых, плюще, папоротниках и пальмах. Щиток защищает насекомое, поэтому щитовка живуча и нередко заметна лишь когда листья желтеют и опадают.",
        "treatment_en": [
            "Isolate the plant so the mobile 'crawler' stage can't spread",
            "Scrape bumps off with a fingernail or soft toothbrush, then dab with 70 % isopropyl alcohol",
            "Coat stems and leaves with horticultural oil or neem to smother survivors; repeat weekly",
            "Prune and bin badly encrusted stems",
            "For persistent infestations use a systemic insecticide per the label"
        ],
        "treatment_ru": [
            "Изолируйте растение, чтобы подвижные «бродяжки» не расселились",
            "Соскоблите щитки ногтем или мягкой зубной щёткой и протрите 70 % спиртом",
            "Покройте стебли и листья садовым/минеральным маслом или нимом, чтобы задушить выживших; повторяйте еженедельно",
            "Сильно облепленные стебли срежьте и выбросьте",
            "При упорном заражении примените системный инсектицид по инструкции"
        ],
        "prevention_en": [
            "Quarantine and inspect new plants - check stems and leaf undersides",
            "Wipe leaves and stems during weekly care so early scale is spotted",
            "Keep plants vigorous; stressed plants are colonised faster",
            "Treat at the first few bumps before the colony armours up"
        ],
        "prevention_ru": [
            "Держите новые растения на карантине и осматривайте стебли и низ листьев",
            "Протирайте листья и стебли при еженедельном уходе, чтобы заметить щитовку рано",
            "Поддерживайте растения крепкими - ослабленные заселяются быстрее",
            "Боритесь при первых щитках, пока колония не покрылась панцирем"
        ],
        "is_healthy": False,
    },

    "not_a_plant": {
        "name_en": "Not a Plant",
        "name_ru": "Не растение",
        "description_en": "The image doesn't appear to contain a plant leaf that the model can analyse. It might be mostly hand, floor, wall, fabric, or another object. Please retake the photo so the leaf fills most of the frame.",
        "description_ru": "На фото модель не нашла листа растения для анализа. Возможно, в кадре рука, стена, пол или другой предмет. Сделайте снимок заново - так, чтобы лист занимал большую часть кадра.",
        "treatment_en": [
            "Move closer so the plant leaf fills most of the frame",
            "Improve lighting - natural daylight near a window works best",
            "Hold the camera steady; wait for autofocus to lock on the leaf",
            "Keep your fingers, background objects and shadows out of the frame"
        ],
        "treatment_ru": [
            "Подойдите ближе - лист должен занимать большую часть кадра",
            "Улучшите освещение - лучше всего естественный свет у окна",
            "Держите камеру ровно, дождитесь автофокуса на листе",
            "Уберите из кадра пальцы, посторонние предметы и тени"
        ],
        "prevention_en": [
            "Frame the single leaf you want diagnosed - avoid wide room shots",
            "Don't photograph through glass - reflections confuse the detector",
            "Use the camera grid to centre the leaf"
        ],
        "prevention_ru": [
            "Фотографируйте один конкретный лист - не весь интерьер",
            "Не снимайте через стекло - блики путают детектор",
            "Используйте сетку камеры, чтобы разместить лист по центру"
        ],
        "is_healthy": False,
    },

    "unknown": {
        "name_en": "Unidentified Condition",
        "name_ru": "Неопределённое состояние",
        "description_en": "The model could not confidently match this image to any known disease. The plant may have a condition that's not in the training set, or the image quality may be insufficient.",
        "description_ru": "Модель не смогла уверенно отнести изображение ни к одной известной болезни. Возможно, у растения состояние, которого нет в обучающей выборке, или снимок недостаточно чёткий.",
        "treatment_en": [
            "Try taking another photo in brighter, natural light",
            "Fill the frame with the single suspicious leaf",
            "Make sure the image is sharp - wait for autofocus to lock",
            "If symptoms persist, consult a local plant specialist"
        ],
        "treatment_ru": [
            "Сделайте другой снимок при ярком естественном освещении",
            "Заполните кадр одним подозрительным листом",
            "Убедитесь, что фото резкое - дождитесь автофокуса",
            "Если симптомы сохраняются, обратитесь к специалисту по растениям"
        ],
        "prevention_en": [
            "Inspect your plant weekly to catch early symptoms",
            "Keep a consistent watering and lighting routine",
            "Keep the growing area clean and clutter-free"
        ],
        "prevention_ru": [
            "Осматривайте растение раз в неделю - так легче заметить ранние симптомы",
            "Поддерживайте постоянный режим полива и освещения",
            "Содержите зону выращивания в чистоте"
        ],
        "is_healthy": False,
    },
}


# ── Toxicity-to-pets database ────────────────────────────────────────
#
# Pet toxicity is a property of the plant species, not of the disease,
# so it lives in its own lookup. Sources cross-reference the ASPCA
# toxic / non-toxic plant lists; species we couldn't confirm with the
# ASPCA fall back to ``("unknown", [])``.
#
# Keys are lower-cased scientific names (genus + species, no authority)
# OR an English common name normalised the same way — so the lookup
# can succeed off either the PlantNet ``scientific_name`` or a user-
# typed name.
#
# ``species_list`` is the list of animal species for which the plant is
# toxic. Empty list means safe; non-empty implies ``toxic_to_pets``.


def _toxic_entry(species_list: list[str]) -> dict:
    return {
        "toxic_to_pets": bool(species_list),
        "species_list": list(species_list),
    }


PET_TOXICITY_DATABASE: dict[str, dict] = {
    # Common toxic houseplants — ASPCA "Toxic to Dogs / Cats".
    "dieffenbachia":          _toxic_entry(["cats", "dogs", "horses"]),
    "dieffenbachia seguine":  _toxic_entry(["cats", "dogs", "horses"]),
    "philodendron":           _toxic_entry(["cats", "dogs"]),
    "monstera deliciosa":     _toxic_entry(["cats", "dogs"]),
    "monstera":               _toxic_entry(["cats", "dogs"]),
    "epipremnum aureum":      _toxic_entry(["cats", "dogs"]),
    "pothos":                 _toxic_entry(["cats", "dogs"]),
    "syngonium podophyllum":  _toxic_entry(["cats", "dogs"]),
    "syngonium":              _toxic_entry(["cats", "dogs"]),
    "anthurium":              _toxic_entry(["cats", "dogs"]),
    "spathiphyllum":          _toxic_entry(["cats", "dogs"]),
    "peace lily":             _toxic_entry(["cats", "dogs"]),
    "lily":                   _toxic_entry(["cats"]),
    "lilium":                 _toxic_entry(["cats"]),
    "sago palm":              _toxic_entry(["cats", "dogs", "horses"]),
    "cycas revoluta":         _toxic_entry(["cats", "dogs", "horses"]),
    "oleander":               _toxic_entry(["cats", "dogs", "horses"]),
    "nerium oleander":        _toxic_entry(["cats", "dogs", "horses"]),
    "aloe":                   _toxic_entry(["cats", "dogs"]),
    "aloe vera":              _toxic_entry(["cats", "dogs"]),
    "zamioculcas zamiifolia": _toxic_entry(["cats", "dogs"]),
    "zz plant":               _toxic_entry(["cats", "dogs"]),
    "ficus benjamina":        _toxic_entry(["cats", "dogs"]),
    "ficus":                  _toxic_entry(["cats", "dogs"]),
    "schefflera":             _toxic_entry(["cats", "dogs"]),
    "dracaena":               _toxic_entry(["cats", "dogs"]),
    "sansevieria":            _toxic_entry(["cats", "dogs"]),
    "snake plant":            _toxic_entry(["cats", "dogs"]),
    "dracaena trifasciata":   _toxic_entry(["cats", "dogs"]),
    "kalanchoe":              _toxic_entry(["cats", "dogs"]),
    "english ivy":            _toxic_entry(["cats", "dogs"]),
    "hedera helix":           _toxic_entry(["cats", "dogs"]),

    # Common non-toxic houseplants.
    "calathea":               _toxic_entry([]),
    "calathea ornata":        _toxic_entry([]),
    "maranta":                _toxic_entry([]),
    "maranta leuconeura":     _toxic_entry([]),
    "peperomia":              _toxic_entry([]),
    "peperomia obtusifolia":  _toxic_entry([]),
    "pilea peperomioides":    _toxic_entry([]),
    "pilea":                  _toxic_entry([]),
    "boston fern":            _toxic_entry([]),
    "nephrolepis exaltata":   _toxic_entry([]),
    "fittonia":               _toxic_entry([]),
    "fittonia albivenis":     _toxic_entry([]),
    "haworthia":              _toxic_entry([]),
    "echeveria":              _toxic_entry([]),
    "african violet":         _toxic_entry([]),
    "saintpaulia":            _toxic_entry([]),
    "spider plant":           _toxic_entry([]),
    "chlorophytum comosum":   _toxic_entry([]),
    "phalaenopsis":           _toxic_entry([]),
    "orchid":                 _toxic_entry([]),
    "areca palm":             _toxic_entry([]),
    "dypsis lutescens":       _toxic_entry([]),
}


def _normalise_species(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def lookup_pet_toxicity(species: str | None) -> dict:
    """Look up toxicity for a species string (scientific or common).

    Returns ``{"toxic_to_pets": bool, "species_list": [...], "source": str}``.
    ``source`` is ``"db"`` when the species was matched, ``"unknown"``
    otherwise — callers can render a "not confirmed" copy instead of
    falsely reassuring the user that an unknown plant is safe.

    Matching strategy:
      1. Exact normalised match.
      2. Genus-level fallback ("ficus elastica" → "ficus") to catch
         species PlantNet returns that aren't in the DB but whose
         genus is uniformly toxic / non-toxic.
    """
    key = _normalise_species(species)
    if not key:
        return {"toxic_to_pets": False, "species_list": [], "source": "unknown"}
    entry = PET_TOXICITY_DATABASE.get(key)
    if entry is None:
        genus = key.split(" ", 1)[0]
        entry = PET_TOXICITY_DATABASE.get(genus)
    if entry is None:
        return {"toxic_to_pets": False, "species_list": [], "source": "unknown"}
    # Deep-copy so callers that mutate the returned ``species_list``
    # (e.g. a route appending "humans" for a UI hint) can't poison the
    # global ``PET_TOXICITY_DATABASE`` entry for every subsequent
    # request. ``{**entry, ...}`` is a shallow merge and the list is
    # still aliased.
    return {**copy.deepcopy(entry), "source": "db", "matched": key}


# ─── Treatment products (Garden refactor — treatment-plan "Recommended
# products" card) ─────────────────────────────────────────────────────
#
# Hand-maintained, bilingual, keyed by the SAME protocol key
# ``ai_services.py::generate_care_plan`` resolves against
# ``_DISEASE_PROTOCOL`` — never generated, never LLM-sourced. A wrong
# product recommendation has a physical consequence (someone sprays a
# plant, or themselves), so a class with no entry here gets NO products
# card at all rather than a generic fallback. ``active_ingredient`` is
# the load-bearing field: brand names differ by market and go out of
# production, the chemistry doesn't. Scoped to the classes the model can
# actually return AND that have a real product-based intervention —
# ``viral_disease`` (no cure), ``leaf_scorch_underwatering`` (a watering
# fix, not a product) and ``healthy``/``not_a_plant`` deliberately have
# no entry. ``edema``/``sunburn_or_scorch`` are deferred-feedback-only
# classes the classifier head doesn't yet predict (see module docstring
# above) so a scan can't diagnose them today either — no entry until
# that changes. ``pests`` IS a shipped head class with a real protocol,
# but has no entry here yet: naming a specific insecticide brand needs
# the same market/registration check the fungicides above went through,
# so its plan names the chemistry (insecticidal soap, neem, horticultural
# oil) in the treatment steps and shows no products card.
TREATMENT_PRODUCTS: dict[str, list[dict]] = {
    "powdery_mildew": [
        {
            "key": "physan20",
            "kind": "fungicide",
            "name_en": "Physan 20",
            "name_ru": "Physan 20",
            "active_ingredient_en": "quaternary ammonium (didecyl dimethyl ammonium chloride)",
            "active_ingredient_ru": "четвертичное аммониевое соединение "
            "(дидецилдиметиламмоний хлорид)",
            "note_en": "Broad-spectrum contact fungicide; safe on most "
            "houseplant foliage at label dilution.",
            "note_ru": "Контактный фунгицид широкого спектра; при "
            "разведении по инструкции безопасен для листвы "
            "большинства комнатных растений.",
        },
        {
            "key": "potassium_bicarbonate",
            "kind": "fungicide",
            "name_en": "GreenCure",
            "name_ru": "GreenCure",
            "active_ingredient_en": "potassium bicarbonate",
            "active_ingredient_ru": "гидрокарбонат калия",
            "note_en": "Contact-only, no systemic action - reapply after "
            "watering or misting washes it off.",
            "note_ru": "Только контактное действие, не системный "
            "препарат - нанесите заново после полива или "
            "опрыскивания, смывающего состав.",
        },
    ],
    "gray_mold_botrytis": [
        {
            "key": "switch",
            "kind": "fungicide",
            "name_en": "Switch 62.5WG",
            "name_ru": "Свитч 62,5 WG",
            "active_ingredient_en": "cyprodinil + fludioxonil",
            "active_ingredient_ru": "ципродинил + флудиоксонил",
            "note_en": "Contact + systemic pair; alternate with a "
            "different mode of action across applications to "
            "avoid resistance.",
            "note_ru": "Контактно-системная пара; чередуйте с "
            "препаратом другого механизма действия между "
            "обработками, чтобы не выработалась устойчивость.",
        },
        {
            "key": "cease_biofungicide",
            "kind": "biofungicide",
            "name_en": "CEASE (Bacillus subtilis)",
            "name_ru": "CEASE (Bacillus subtilis)",
            "active_ingredient_en": "Bacillus subtilis strain QST 713",
            "active_ingredient_ru": "Bacillus subtilis, штамм QST 713",
            "note_en": "Biological option - lower resistance risk than "
            "a synthetic fungicide, safe for repeated weekly use.",
            "note_ru": "Биологический препарат - ниже риск выработки "
            "устойчивости, чем у синтетического фунгицида, "
            "можно применять еженедельно.",
        },
    ],
    "leaf_spot": [
        {
            "key": "copper_hydroxide",
            "kind": "fungicide/bactericide",
            "name_en": "Bonide Copper Fungicide",
            "name_ru": "Bonide Copper Fungicide",
            "active_ingredient_en": "copper hydroxide",
            "active_ingredient_ru": "гидроксид меди",
            "note_en": "Covers both fungal and bacterial leaf spot - "
            "useful when the cause isn't visually certain.",
            "note_ru": "Действует и на грибковую, и на бактериальную "
            "пятнистость - полезно, если возбудитель точно "
            "не определён.",
        },
        {
            "key": "chlorothalonil",
            "kind": "fungicide",
            "name_en": "Daconil (Chlorothalonil)",
            "name_ru": "Daconil (хлороталонил)",
            "active_ingredient_en": "chlorothalonil",
            "active_ingredient_ru": "хлороталонил",
            "note_en": "Fungal cases only - has no effect on a "
            "bacterial leaf spot.",
            "note_ru": "Только для грибковой пятнистости - не "
            "действует на бактериальную.",
        },
    ],
    "nutrient_deficiency": [
        {
            "key": "balanced_liquid_fertiliser",
            "kind": "fertiliser",
            "name_en": "A balanced liquid houseplant fertiliser (e.g. 10-10-10 or 20-20-20)",
            "name_ru": "Сбалансированное жидкое удобрение для "
            "комнатных растений (например, 10-10-10 или 20-20-20)",
            "active_ingredient_en": "N-P-K + micronutrients",
            "active_ingredient_ru": "N-P-K + микроэлементы",
            "note_en": "Dilute to half label strength for the first "
            "feed - a starved plant's roots can be scorched by "
            "a full-strength dose.",
            "note_ru": "Для первой подкормки разведите вдвое слабее "
            "указанного на упаковке - корни истощённого "
            "растения легче обжечь удобрением полной "
            "концентрации.",
        },
    ],
}

# Application-window guidance shown alongside the matching products
# entry (Garden refactor task-detail "Recommendations" card). Same
# scoping rule as TREATMENT_PRODUCTS: only the spray/feed-based
# protocols get one.
TREATMENT_CONDITIONS: dict[str, dict] = {
    "powdery_mildew": {
        "best_time_en": "Early morning or evening - never in direct sun.",
        "best_time_ru": "Рано утром или вечером - не под прямым солнцем.",
        "temp_c": [15, 26],
        "dry_hours": 12,
    },
    "gray_mold_botrytis": {
        "best_time_en": "Morning, after leaves have fully dried from any overnight condensation.",
        "best_time_ru": "Утром, когда листья полностью высохнут от "
        "ночного конденсата.",
        "temp_c": [15, 25],
        "dry_hours": 24,
    },
    "leaf_spot": {
        "best_time_en": "Morning or evening - never in direct midday sun.",
        "best_time_ru": "Утром или вечером - не под прямым полуденным солнцем.",
        "temp_c": [15, 27],
        "dry_hours": 24,
    },
    "nutrient_deficiency": {
        "best_time_en": "Any time of day; avoid feeding within 24 h of repotting.",
        "best_time_ru": "В любое время суток; не подкармливайте в "
        "течение 24 ч после пересадки.",
        "temp_c": [18, 26],
        "dry_hours": 0,
    },
}
