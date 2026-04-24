package com.plantdiseases.app.data

import com.plantdiseases.app.R
import com.plantdiseases.app.data.model.GuideCategory
import com.plantdiseases.app.data.model.GuideItem

object GuideDataProvider {

    private val items: List<GuideItem> by lazy { buildItems() }
    private val byCategory: Map<GuideCategory, List<GuideItem>> by lazy { items.groupBy { it.category } }

    fun getGuideItems(): List<GuideItem> = items

    private fun buildItems(): List<GuideItem> = listOf(
        // Common diseases — houseplant-focused guide articles

        GuideItem(
            id = "powdery_mildew",
            titleEn = "Powdery Mildew",
            titleRu = "Мучнистая роса",
            descriptionEn = "White powdery spots on leaves and stems",
            descriptionRu = "Белые порошкообразные пятна на листьях и стеблях",
            contentEn = """
Powdery mildew is one of the most common fungal diseases in houseplants. It appears as white or gray powdery patches on leaf surfaces.

SYMPTOMS:
• White/gray powder-like coating on leaves
• Yellowing and curling of affected leaves
• Stunted growth
• Leaf drop in severe cases

CAUSES:
• High humidity with poor air circulation
• Overcrowding of plants
• Low light conditions

TREATMENT:
1. Isolate the affected plant immediately
2. Remove severely infected leaves
3. Spray with a baking soda solution (1 tsp per liter of water + a drop of dish soap)
4. Apply neem oil solution weekly
5. Improve air circulation around the plant

PREVENTION:
• Ensure good air circulation
• Avoid overhead watering
• Don't crowd plants together
• Keep leaves dry
            """.trimIndent(),
            contentRu = """
Мучнистая роса — одно из самых распространённых грибковых заболеваний комнатных растений. Проявляется в виде белого или серого порошкообразного налёта на поверхности листьев.

СИМПТОМЫ:
• Белый/серый порошкообразный налёт на листьях
• Пожелтение и скручивание поражённых листьев
• Замедление роста
• Опадение листьев в тяжёлых случаях

ПРИЧИНЫ:
• Высокая влажность при плохой циркуляции воздуха
• Скученность растений
• Недостаток света

ЛЕЧЕНИЕ:
1. Немедленно изолируйте поражённое растение
2. Удалите сильно заражённые листья
3. Опрыскайте раствором соды (1 ч.л. на литр воды + капля моющего средства)
4. Применяйте раствор масла нима еженедельно
5. Улучшите циркуляцию воздуха вокруг растения

ПРОФИЛАКТИКА:
• Обеспечьте хорошую циркуляцию воздуха
• Избегайте полива сверху
• Не ставьте растения слишком близко друг к другу
• Держите листья сухими
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "leaf_spot",
            titleEn = "Leaf Spot (Bacterial / Fungal)",
            titleRu = "Пятнистость листьев (бактериальная / грибковая)",
            descriptionEn = "Dark round or irregular spots, often with a yellow halo",
            descriptionRu = "Тёмные округлые или неправильные пятна, часто с жёлтым ореолом",
            contentEn = """
Leaf spot is a broad category covering bacterial and fungal infections that show up as dark, clearly bordered spots on the leaf. Common on ficus, dracaena, calathea, and many other houseplants whose leaves stay wet for too long.

SYMPTOMS:
• Dark brown / black spots with yellow halos
• Spots may merge into larger dead patches
• Affected leaves yellow and drop
• Progresses during high humidity or after misting

TREATMENT:
1. Cut off affected leaves with sterile scissors
2. Apply a copper-based fungicide / bactericide weekly for 2–3 weeks
3. Switch to bottom watering — keep foliage dry
4. Improve airflow and let the top of the substrate dry between waterings
5. Disinfect tools with 70% alcohol between plants

PREVENTION:
• Water the substrate, not the leaves
• Remove fallen leaves from the pot surface
• Quarantine new plants for two weeks
• Sterilise pots and saucers before reuse
            """.trimIndent(),
            contentRu = """
Пятнистость листьев — общее название для бактериальных и грибковых инфекций, проявляющихся в виде тёмных пятен с чёткими краями. Часто встречается на фикусах, драценах, калатеях и многих других комнатных растениях, когда листья слишком долго остаются влажными.

СИМПТОМЫ:
• Тёмно-коричневые / чёрные пятна с жёлтым ореолом
• Пятна сливаются в крупные отмирающие участки
• Поражённые листья желтеют и опадают
• Болезнь прогрессирует при высокой влажности или после опрыскивания

ЛЕЧЕНИЕ:
1. Обрежьте поражённые листья стерильными ножницами
2. Обрабатывайте фунгицидом на основе меди раз в неделю 2–3 недели
3. Перейдите на полив снизу — листья держите сухими
4. Улучшите воздухообмен, давайте верхнему слою субстрата просыхать
5. Дезинфицируйте инструмент 70% спиртом между растениями

ПРОФИЛАКТИКА:
• Поливайте субстрат, а не листья
• Убирайте опавшие листья с поверхности горшка
• Новые растения держите 2 недели на карантине
• Перед повторным использованием стерилизуйте горшки и поддоны
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "blight",
            titleEn = "Blight",
            titleRu = "Фитофтороз",
            descriptionEn = "Rapidly spreading water-soaked lesions on leaves and stems",
            descriptionRu = "Быстро растущие водянистые поражения на листьях и стеблях",
            contentEn = """
Blight is a fast-moving fungal disease that causes large, dark, water-soaked lesions on leaves and stems. On houseplants it is most often triggered by overwatering combined with cool temperatures.

SYMPTOMS:
• Large dark, water-soaked patches on leaves
• Stems develop soft black lesions near the soil line
• Infected parts wilt and collapse within days
• White fuzzy mold may appear on the underside in humid rooms

TREATMENT:
1. Remove and destroy every affected part — do not compost
2. Apply a systemic or copper-based fungicide
3. Reduce watering and let the substrate dry deeply
4. Raise the room temperature above 20 °C and lower humidity
5. If more than half the plant is lost, discard it to protect the rest

PREVENTION:
• Never let pots stand in water
• Keep plants spaced for good airflow
• Keep the room above 18 °C in winter
• Inspect the collection weekly during cool, damp weather
            """.trimIndent(),
            contentRu = """
Фитофтороз — быстро прогрессирующее грибковое заболевание: крупные тёмные водянистые поражения на листьях и стеблях. На комнатных растениях чаще всего возникает при переувлажнении в сочетании с прохладой.

СИМПТОМЫ:
• Крупные тёмные водянистые пятна на листьях
• Мягкие чёрные поражения у основания стебля
• Поражённые части увядают и сморщиваются за несколько дней
• Во влажных помещениях на нижней стороне появляется белая пушистая плесень

ЛЕЧЕНИЕ:
1. Удалите и уничтожьте все поражённые части — не в компост
2. Обработайте системным фунгицидом или препаратом на основе меди
3. Сократите полив, дайте субстрату глубоко просохнуть
4. Поднимите температуру выше 20 °C и снизьте влажность
5. Если поражено больше половины растения — утилизируйте его

ПРОФИЛАКТИКА:
• Никогда не оставляйте горшки в воде
• Расставляйте растения свободно для вентиляции
• Зимой поддерживайте температуру выше 18 °C
• В сырую прохладную погоду осматривайте коллекцию еженедельно
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "root_rot",
            titleEn = "Root Rot",
            titleRu = "Корневая гниль",
            descriptionEn = "Mushy, brown roots caused by overwatering",
            descriptionRu = "Мягкие, коричневые корни из-за переувлажнения",
            contentEn = """
Root rot is a deadly disease caused by overwatering or poor drainage. The roots become waterlogged, lose oxygen, and begin to decay.

SYMPTOMS:
• Wilting despite wet soil
• Yellowing lower leaves
• Mushy, brown/black roots
• Foul smell from soil
• Stunted or no new growth

TREATMENT:
1. Remove plant from pot
2. Wash away all old soil
3. Cut away all brown/mushy roots with sterile scissors
4. Let roots dry for a few hours
5. Repot in fresh, well-draining soil mix
6. Water sparingly for the first 2 weeks

PREVENTION:
• Use pots with drainage holes
• Water only when topsoil is dry
• Use well-draining soil mix
• Don't let pots sit in water
            """.trimIndent(),
            contentRu = """
Корневая гниль — опасное заболевание, вызванное переувлажнением или плохим дренажом. Корни заливаются водой, теряют кислород и начинают разлагаться.

СИМПТОМЫ:
• Увядание при влажной почве
• Пожелтение нижних листьев
• Мягкие, коричневые/чёрные корни
• Неприятный запах от почвы
• Замедление или отсутствие нового роста

ЛЕЧЕНИЕ:
1. Извлеките растение из горшка
2. Смойте всю старую почву
3. Обрежьте все коричневые/мягкие корни стерильными ножницами
4. Дайте корням подсохнуть несколько часов
5. Пересадите в свежую, хорошо дренированную почву
6. Поливайте умеренно в течение первых 2 недель

ПРОФИЛАКТИКА:
• Используйте горшки с дренажными отверстиями
• Поливайте только когда верхний слой почвы сухой
• Используйте хорошо дренированную почвенную смесь
• Не оставляйте горшки стоять в воде
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "leaf_mold",
            titleEn = "Leaf Mold",
            titleRu = "Листовая плесень",
            descriptionEn = "Olive-green fuzzy mold on leaf undersides",
            descriptionRu = "Оливково-зелёная пушистая плесень на нижней стороне листьев",
            contentEn = """
Leaf mold causes pale green or yellowish spots on upper leaf surfaces with olive-green to brown fuzzy mold on the undersides. Thrives in high humidity.

SYMPTOMS:
• Pale green/yellow spots on upper leaf surface
• Olive-green to brown mold on undersides
• Leaves may curl and wilt
• Severely affected leaves turn brown and die

TREATMENT:
1. Remove affected leaves carefully
2. Improve ventilation dramatically
3. Reduce humidity below 70%
4. Apply fungicide spray (sulfur or copper-based)
5. Space out plants to improve airflow

PREVENTION:
• Maintain humidity between 40-65%
• Ensure excellent air circulation
• Avoid crowding plants
• Water in the morning so leaves dry quickly
            """.trimIndent(),
            contentRu = """
Листовая плесень вызывает бледно-зелёные или желтоватые пятна на верхней стороне листьев с оливково-зелёной или коричневой пушистой плесенью снизу.

СИМПТОМЫ:
• Бледно-зелёные/жёлтые пятна на верхней стороне
• Оливково-зелёная или коричневая плесень снизу
• Листья могут скручиваться и вянуть
• Сильно поражённые листья буреют и отмирают

ЛЕЧЕНИЕ:
1. Аккуратно удалите поражённые листья
2. Существенно улучшите вентиляцию
3. Снизьте влажность ниже 70%
4. Примените фунгицидный спрей (на основе серы или меди)
5. Расставьте растения для улучшения воздушного потока

ПРОФИЛАКТИКА:
• Поддерживайте влажность 40-65%
• Обеспечьте отличную циркуляцию воздуха
• Не ставьте растения слишком близко
• Поливайте утром, чтобы листья быстро высохли
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "mosaic_virus",
            titleEn = "Mosaic Virus",
            titleRu = "Мозаичный вирус",
            descriptionEn = "Mottled light/dark pattern, no cure exists",
            descriptionRu = "Пёстрый узор из светлых и тёмных участков, не лечится",
            contentEn = """
Mosaic virus causes a distinctive mottled pattern of light and dark green on leaves. Leaves may be distorted or curled. There is no cure.

SYMPTOMS:
• Mottled yellow/light green and dark green areas
• Distorted, curled, or blistered leaves
• Stunted growth
• Reduced flowering

TREATMENT:
1. There is no cure for viral infections in plants
2. Remove and destroy severely infected plants
3. Mildly affected plants can be kept but won't recover
4. Control aphids and other vectors that spread the virus
5. Sterilize all tools that contact infected plants

PREVENTION:
• Buy certified disease-free plants
• Control insect vectors (especially aphids)
• Wash hands before handling plants
• Don't use tobacco products near plants
            """.trimIndent(),
            contentRu = """
Мозаичный вирус вызывает характерный пёстрый узор из светло- и тёмно-зелёных участков на листьях. Листья могут быть деформированы или скручены. Лечения нет.

СИМПТОМЫ:
• Пёстрые жёлто-зелёные и тёмно-зелёные участки
• Деформированные, скрученные или вздутые листья
• Замедление роста
• Уменьшение цветения

ЛЕЧЕНИЕ:
1. Вирусные инфекции растений не лечатся
2. Удалите и уничтожьте сильно поражённые растения
3. Слабо поражённые можно оставить, но они не выздоровеют
4. Контролируйте тлю и других переносчиков вируса
5. Стерилизуйте все инструменты

ПРОФИЛАКТИКА:
• Покупайте сертифицированные здоровые растения
• Контролируйте насекомых-переносчиков (особенно тлю)
• Мойте руки перед работой с растениями
• Не используйте табачные изделия рядом с растениями
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "rust",
            titleEn = "Rust Disease",
            titleRu = "Ржавчина",
            descriptionEn = "Orange/brown pustules on leaf undersides",
            descriptionRu = "Оранжевые/коричневые пустулы на нижней стороне листьев",
            contentEn = """
Rust fungus produces orange, yellow, or brown pustules on the undersides of leaves. Upper surfaces show yellow spots corresponding to pustule locations.

SYMPTOMS:
• Orange/brown powdery pustules on leaf undersides
• Yellow spots on upper leaf surface
• Premature leaf drop
• Weakened plant growth

TREATMENT:
1. Remove and destroy all infected leaves
2. Apply sulfur-based or copper fungicide
3. Do not compost infected material
4. Improve air circulation
5. Treat every 7-14 days until symptoms stop

PREVENTION:
• Water in the morning at soil level
• Keep foliage dry
• Remove fallen leaves promptly
• Provide adequate spacing between plants
            """.trimIndent(),
            contentRu = """
Грибок ржавчины образует оранжевые, жёлтые или коричневые пустулы на нижней стороне листьев. На верхней поверхности видны жёлтые пятна.

СИМПТОМЫ:
• Оранжевые/коричневые порошковые пустулы снизу листьев
• Жёлтые пятна на верхней стороне
• Преждевременное опадение листьев
• Ослабление роста

ЛЕЧЕНИЕ:
1. Удалите и уничтожьте все заражённые листья
2. Примените фунгицид на основе серы или меди
3. Не компостируйте заражённый материал
4. Улучшите циркуляцию воздуха
5. Обрабатывайте каждые 7-14 дней

ПРОФИЛАКТИКА:
• Поливайте утром на уровне почвы
• Содержите листву сухой
• Своевременно убирайте опавшие листья
• Обеспечьте расстояние между растениями
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        GuideItem(
            id = "botrytis",
            titleEn = "Gray Mold (Botrytis)",
            titleRu = "Серая гниль (Ботритис)",
            descriptionEn = "Fuzzy gray-brown mold on flowers and leaves",
            descriptionRu = "Пушистая серо-коричневая плесень на цветах и листьях",
            contentEn = """
Botrytis, or gray mold, produces fuzzy gray-brown mold on flowers, buds, leaves, and stems. It thrives in cool, damp conditions.

SYMPTOMS:
• Fuzzy gray mold on decaying plant parts
• Brown/tan spots on flowers and buds
• Soft, water-soaked areas on stems
• Dead flowers may be covered in gray fuzz

TREATMENT:
1. Remove all infected parts immediately
2. Improve ventilation and reduce humidity
3. Avoid misting or overhead watering
4. Apply fungicide if infection is widespread
5. Remove dead or dying flowers promptly

PREVENTION:
• Keep humidity below 70%
• Provide excellent air circulation
• Remove dead plant material promptly
• Water plants in the morning
            """.trimIndent(),
            contentRu = """
Ботритис, или серая гниль, образует пушистую серо-коричневую плесень на цветах, бутонах, листьях и стеблях. Развивается в прохладных, влажных условиях.

СИМПТОМЫ:
• Пушистая серая плесень на отмирающих частях
• Коричневые/бежевые пятна на цветах и бутонах
• Мягкие водянистые участки на стеблях
• Мёртвые цветы покрыты серым налётом

ЛЕЧЕНИЕ:
1. Немедленно удалите все заражённые части
2. Улучшите вентиляцию и снизьте влажность
3. Избегайте опрыскивания и верхнего полива
4. Примените фунгицид при обширном поражении
5. Своевременно удаляйте увядшие цветы

ПРОФИЛАКТИКА:
• Поддерживайте влажность ниже 70%
• Обеспечьте отличную циркуляцию воздуха
• Своевременно удаляйте мёртвые части растений
• Поливайте растения утром
            """.trimIndent(),
            iconRes = R.drawable.ic_disease,
            category = GuideCategory.COMMON_DISEASES
        ),

        // Pests

        GuideItem(
            id = "spider_mites",
            titleEn = "Spider Mites",
            titleRu = "Паутинный клещ",
            descriptionEn = "Tiny pests creating fine webs on plant leaves",
            descriptionRu = "Мелкие вредители, создающие тонкую паутину на листьях",
            contentEn = """
Spider mites are tiny arachnids that suck sap from plant cells, causing speckled, discolored leaves with fine webbing.

SYMPTOMS:
• Fine webs between leaves and stems
• Tiny yellow/white speckles on leaf surfaces
• Leaves become pale, dry and brittle
• Visible tiny dots (mites) on leaf undersides

TREATMENT:
1. Spray plant thoroughly with water to dislodge mites
2. Apply insecticidal soap or neem oil
3. Repeat treatment every 5-7 days for 3 weeks
4. Increase humidity around the plant
5. Isolate the affected plant

PREVENTION:
• Mist plants regularly
• Keep humidity above 50%
• Inspect new plants before bringing them home
• Clean leaves regularly with a damp cloth
            """.trimIndent(),
            contentRu = """
Паутинный клещ — мелкие паукообразные, которые высасывают сок из клеток растений, вызывая пятнистость и обесцвечивание листьев с тонкой паутиной.

СИМПТОМЫ:
• Тонкая паутина между листьями и стеблями
• Мелкие жёлтые/белые крапинки на поверхности листьев
• Листья становятся бледными, сухими и ломкими
• Видны мелкие точки (клещи) на нижней стороне листьев

ЛЕЧЕНИЕ:
1. Тщательно опрыскайте растение водой
2. Нанесите инсектицидное мыло или масло нима
3. Повторяйте обработку каждые 5-7 дней в течение 3 недель
4. Увеличьте влажность вокруг растения
5. Изолируйте поражённое растение

ПРОФИЛАКТИКА:
• Регулярно опрыскивайте растения
• Поддерживайте влажность выше 50%
• Осматривайте новые растения перед покупкой
• Регулярно протирайте листья влажной тканью
            """.trimIndent(),
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS
        ),

        GuideItem(
            id = "aphids",
            titleEn = "Aphids",
            titleRu = "Тля",
            descriptionEn = "Small green or black insects clustering on new growth",
            descriptionRu = "Мелкие зелёные или чёрные насекомые на молодых побегах",
            contentEn = """
Aphids are small soft-bodied insects that feed on plant sap. They cluster on new growth and undersides of leaves.

SYMPTOMS:
• Clusters of tiny insects on stems and buds
• Sticky residue (honeydew) on leaves
• Curled or distorted new growth
• Black sooty mold on honeydew

TREATMENT:
1. Blast with strong water spray
2. Apply insecticidal soap
3. Use neem oil solution
4. Introduce ladybugs (natural predators)
5. For severe infestations, use systemic insecticide

PREVENTION:
• Inspect plants regularly, especially new growth
• Quarantine new plants for 2 weeks
• Avoid over-fertilizing with nitrogen
• Encourage beneficial insects
            """.trimIndent(),
            contentRu = """
Тля — мелкие мягкотелые насекомые, питающиеся соком растений. Скапливаются на молодых побегах и нижней стороне листьев.

СИМПТОМЫ:
• Скопления мелких насекомых на стеблях и бутонах
• Липкий налёт (медвяная роса) на листьях
• Скрученные или деформированные молодые побеги
• Чёрная сажистая плесень на медвяной росе

ЛЕЧЕНИЕ:
1. Смойте сильной струёй воды
2. Нанесите инсектицидное мыло
3. Используйте раствор масла нима
4. Подселите божьих коровок (естественные враги)
5. При сильном поражении используйте системный инсектицид

ПРОФИЛАКТИКА:
• Регулярно осматривайте растения
• Карантинируйте новые растения на 2 недели
• Избегайте чрезмерного удобрения азотом
• Привлекайте полезных насекомых
            """.trimIndent(),
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS
        ),

        GuideItem(
            id = "mealybugs",
            titleEn = "Mealybugs",
            titleRu = "Мучнистые червецы",
            descriptionEn = "White cottony masses on stems and leaf joints",
            descriptionRu = "Белые ватообразные скопления на стеблях и пазухах листьев",
            contentEn = """
Mealybugs are soft-bodied insects covered in white, waxy coating. They cluster in leaf axils, on stems, and underneath leaves.

SYMPTOMS:
• White cottony or waxy masses on plant
• Sticky honeydew on leaves and surfaces below
• Yellowing and wilting of leaves
• Stunted growth and leaf drop

TREATMENT:
1. Dab individual mealybugs with rubbing alcohol on a cotton swab
2. Spray with insecticidal soap or neem oil
3. For severe infestations, use systemic insecticide
4. Repeat weekly for at least 3-4 weeks
5. Isolate infected plants immediately

PREVENTION:
• Inspect plants regularly, especially leaf axils
• Quarantine new plants for 2-3 weeks
• Keep plants healthy with proper care
• Avoid overwatering and excessive nitrogen fertilizer
            """.trimIndent(),
            contentRu = """
Мучнистые червецы — мягкотелые насекомые, покрытые белым восковым налётом. Скапливаются в пазухах листьев, на стеблях и нижней стороне листьев.

СИМПТОМЫ:
• Белые ватообразные или восковые скопления
• Липкая медвяная роса на листьях
• Пожелтение и увядание листьев
• Замедление роста и опадение листьев

ЛЕЧЕНИЕ:
1. Обработайте отдельных вредителей спиртом на ватной палочке
2. Опрыскайте инсектицидным мылом или маслом нима
3. При сильном поражении используйте системный инсектицид
4. Повторяйте еженедельно минимум 3-4 недели
5. Немедленно изолируйте заражённые растения

ПРОФИЛАКТИКА:
• Регулярно осматривайте пазухи листьев
• Карантинируйте новые растения 2-3 недели
• Поддерживайте здоровье растений
• Избегайте переувлажнения и избытка азота
            """.trimIndent(),
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS
        ),

        GuideItem(
            id = "scale_insects",
            titleEn = "Scale Insects",
            titleRu = "Щитовки",
            descriptionEn = "Brown or white bumps firmly attached to stems and leaves",
            descriptionRu = "Коричневые или белые бугорки на стеблях и листьях",
            contentEn = """
Scale insects are small, immobile pests that attach to stems and leaves under a protective shell. They suck plant sap and weaken the plant over time.

SYMPTOMS:
• Small brown or white bumps on stems and leaves
• Sticky honeydew residue
• Yellowing leaves and reduced vigor
• Sooty mold growing on honeydew

TREATMENT:
1. Scrape off individual scales with a soft brush
2. Apply rubbing alcohol with a cotton swab
3. Spray with horticultural oil to suffocate them
4. Use systemic insecticide for heavy infestations
5. Repeat treatment every 2 weeks

PREVENTION:
• Inspect new plants before purchase
• Keep plants well-ventilated
• Avoid overcrowding
• Monitor regularly — early detection is key
            """.trimIndent(),
            contentRu = """
Щитовки — мелкие неподвижные вредители, прикрепляющиеся к стеблям и листьям под защитным щитком. Высасывают сок и ослабляют растение.

СИМПТОМЫ:
• Мелкие коричневые или белые бугорки на стеблях
• Липкая медвяная роса
• Пожелтение листьев и снижение тонуса
• Сажистый грибок на медвяной росе

ЛЕЧЕНИЕ:
1. Соскребите отдельных щитовок мягкой щёткой
2. Обработайте спиртом на ватной палочке
3. Опрыскайте садовым маслом для удушения
4. При сильном поражении — системный инсектицид
5. Повторяйте обработку каждые 2 недели

ПРОФИЛАКТИКА:
• Осматривайте новые растения перед покупкой
• Обеспечьте хорошую вентиляцию
• Избегайте скученности
• Регулярно осматривайте — ранее обнаружение ключевое
            """.trimIndent(),
            iconRes = R.drawable.ic_pest,
            category = GuideCategory.PESTS
        ),

        // Watering

        GuideItem(
            id = "watering_basics",
            titleEn = "Watering Basics",
            titleRu = "Основы полива",
            descriptionEn = "How to water houseplants correctly",
            descriptionRu = "Как правильно поливать комнатные растения",
            contentEn = """
Proper watering is the single most important factor in houseplant health. Most problems come from overwatering.

GOLDEN RULES:
1. Check before watering — stick your finger 2-3 cm into soil
2. Water thoroughly until it drains from the bottom
3. Empty saucers after 30 minutes
4. Use room-temperature water
5. Water less in winter, more in summer

SIGNS OF OVERWATERING:
• Yellowing lower leaves
• Soft, mushy stems
• Mold on soil surface
• Root rot

SIGNS OF UNDERWATERING:
• Dry, crispy leaf edges
• Wilting that recovers after watering
• Soil pulling away from pot edges
• Slow growth

TIPS:
• Morning watering is best
• Group plants by water needs
• Consider self-watering pots for consistent moisture
• Rainwater or filtered water is ideal
            """.trimIndent(),
            contentRu = """
Правильный полив — самый важный фактор здоровья комнатных растений. Большинство проблем связано с переувлажнением.

ЗОЛОТЫЕ ПРАВИЛА:
1. Проверяйте перед поливом — воткните палец на 2-3 см в почву
2. Поливайте обильно, пока вода не потечёт из дренажного отверстия
3. Сливайте воду из поддонов через 30 минут
4. Используйте воду комнатной температуры
5. Зимой поливайте реже, летом — чаще

ПРИЗНАКИ ПЕРЕУВЛАЖНЕНИЯ:
• Пожелтение нижних листьев
• Мягкие, рыхлые стебли
• Плесень на поверхности почвы
• Корневая гниль

ПРИЗНАКИ НЕДОСТАТОЧНОГО ПОЛИВА:
• Сухие, хрустящие края листьев
• Увядание, которое проходит после полива
• Почва отходит от стенок горшка
• Замедление роста

СОВЕТЫ:
• Лучше поливать утром
• Группируйте растения по потребности в воде
• Рассмотрите горшки с автополивом
• Дождевая или фильтрованная вода — идеальный вариант
            """.trimIndent(),
            iconRes = R.drawable.ic_watering,
            category = GuideCategory.WATERING
        ),

        GuideItem(
            id = "humidity_guide",
            titleEn = "Humidity Control",
            titleRu = "Контроль влажности",
            descriptionEn = "Managing humidity for tropical houseplants",
            descriptionRu = "Управление влажностью для тропических растений",
            contentEn = """
Many popular houseplants are tropical and need higher humidity than typical indoor environments provide (30-40%). Target 50-70% for most tropicals.

WAYS TO INCREASE HUMIDITY:
1. Group plants together (they create a microclimate)
2. Place pots on pebble trays with water
3. Use a humidifier near plants
4. Mist leaves in the morning (not evening)
5. Place plants in naturally humid rooms (bathroom, kitchen)

SIGNS OF LOW HUMIDITY:
• Brown, crispy leaf tips and edges
• Curling leaves
• Flower buds dropping before opening
• Spider mite infestations (thrive in dry air)

TIPS:
• Measure humidity with a hygrometer
• Don't mist plants prone to fungal issues
• Keep plants away from heating vents
• Terrariums create perfect humidity for small plants
            """.trimIndent(),
            contentRu = """
Многие популярные комнатные растения тропические и нуждаются в более высокой влажности, чем обычный воздух в помещении (30-40%). Оптимально 50-70% для большинства тропических.

СПОСОБЫ ПОВЫШЕНИЯ ВЛАЖНОСТИ:
1. Группируйте растения вместе (создают микроклимат)
2. Ставьте горшки на поддоны с галькой и водой
3. Используйте увлажнитель воздуха
4. Опрыскивайте листья утром (не вечером)
5. Размещайте в влажных комнатах (ванная, кухня)

ПРИЗНАКИ НИЗКОЙ ВЛАЖНОСТИ:
• Коричневые, хрустящие кончики и края листьев
• Скручивание листьев
• Опадение бутонов до раскрытия
• Паутинный клещ (процветает в сухом воздухе)

СОВЕТЫ:
• Измеряйте влажность гигрометром
• Не опрыскивайте растения, склонные к грибковым болезням
• Держите растения подальше от батарей
• Террариумы создают идеальную влажность для мелких растений
            """.trimIndent(),
            iconRes = R.drawable.ic_watering,
            category = GuideCategory.WATERING
        ),

        // Lighting

        GuideItem(
            id = "light_guide",
            titleEn = "Light Requirements",
            titleRu = "Требования к освещению",
            descriptionEn = "Understanding light needs for houseplants",
            descriptionRu = "Потребности комнатных растений в освещении",
            contentEn = """
Light is essential for photosynthesis. Understanding your plant's light needs prevents many common problems.

LIGHT LEVELS:
• Direct bright: South-facing window, 6+ hours of sun
• Bright indirect: Near a sunny window but not in direct rays
• Medium: A few feet from a window, or north-facing
• Low: Far from windows, hallways

SIGNS OF TOO MUCH LIGHT:
• Scorched/bleached patches on leaves
• Crispy brown edges
• Faded leaf color
• Wilting in midday heat

SIGNS OF TOO LITTLE LIGHT:
• Leggy, stretched growth toward light
• Small, pale new leaves
• No flowering
• Dropping lower leaves

TIPS:
• Rotate plants quarterly for even growth
• Clean windows to maximize light
• Use grow lights in dark rooms (12-16 hrs/day)
• Sheer curtains diffuse harsh direct sun
            """.trimIndent(),
            contentRu = """
Свет необходим для фотосинтеза. Понимание потребностей растения в свете предотвращает многие проблемы.

УРОВНИ ОСВЕЩЕНИЯ:
• Прямой яркий: южное окно, 6+ часов солнца
• Яркий рассеянный: рядом с солнечным окном, но не под прямыми лучами
• Средний: в нескольких шагах от окна или северное окно
• Низкий: далеко от окон, коридоры

ПРИЗНАКИ ИЗБЫТКА СВЕТА:
• Ожоги/обесцвеченные участки на листьях
• Хрустящие коричневые края
• Блёклый цвет листьев
• Увядание в полуденную жару

ПРИЗНАКИ НЕДОСТАТКА СВЕТА:
• Вытянутый рост в сторону света
• Мелкие, бледные новые листья
• Отсутствие цветения
• Опадение нижних листьев

СОВЕТЫ:
• Поворачивайте растения каждый сезон
• Мойте окна для максимального света
• Используйте фитолампы в тёмных комнатах (12-16 ч/день)
• Тюлевые шторы рассеивают жёсткий прямой свет
            """.trimIndent(),
            iconRes = R.drawable.ic_light,
            category = GuideCategory.LIGHTING
        ),

        // Care tips

        GuideItem(
            id = "repotting",
            titleEn = "Repotting Guide",
            titleRu = "Руководство по пересадке",
            descriptionEn = "When and how to repot your plants",
            descriptionRu = "Когда и как пересаживать растения",
            contentEn = """
Repotting gives your plant fresh soil and room to grow. Most houseplants need repotting every 1-2 years.

SIGNS IT'S TIME TO REPOT:
• Roots growing out of drainage holes
• Water runs straight through without absorbing
• Plant is top-heavy and tips over
• Growth has stalled
• Soil dries out very quickly

HOW TO REPOT:
1. Water plant the day before
2. Choose a pot 2-3 cm larger in diameter
3. Add fresh potting mix to the bottom
4. Gently remove plant and loosen root ball
5. Place in new pot and fill around with soil
6. Water thoroughly
7. Skip fertilizing for 4-6 weeks

BEST TIME: Spring (start of growing season)

TIPS:
• Always use pots with drainage holes
• Don't go too big — oversized pots hold excess moisture
• Use appropriate soil mix for your plant type
            """.trimIndent(),
            contentRu = """
Пересадка даёт растению свежую почву и место для роста. Большинство комнатных растений нужно пересаживать каждые 1-2 года.

ПРИЗНАКИ ТОГО, ЧТО ПОРА ПЕРЕСАЖИВАТЬ:
• Корни растут из дренажных отверстий
• Вода проходит насквозь, не впитываясь
• Растение перевешивается и падает
• Рост остановился
• Почва высыхает очень быстро

КАК ПЕРЕСАЖИВАТЬ:
1. Полейте растение за день до пересадки
2. Выберите горшок на 2-3 см больше в диаметре
3. Насыпьте свежую почву на дно
4. Аккуратно извлеките растение и расправьте корни
5. Поместите в новый горшок и засыпьте почвой
6. Тщательно полейте
7. Не удобряйте 4-6 недель

ЛУЧШЕЕ ВРЕМЯ: весна (начало вегетации)

СОВЕТЫ:
• Всегда используйте горшки с дренажными отверстиями
• Не выбирайте слишком большой горшок
• Используйте подходящую почву для вашего растения
            """.trimIndent(),
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS
        ),

        GuideItem(
            id = "fertilizing",
            titleEn = "Fertilizing Guide",
            titleRu = "Руководство по подкормке",
            descriptionEn = "How and when to feed your houseplants",
            descriptionRu = "Как и когда подкармливать комнатные растения",
            contentEn = """
Fertilizing provides essential nutrients that potting soil eventually depletes. Feed during the growing season (spring-summer).

NPK BASICS:
• N (Nitrogen) — leaf and stem growth
• P (Phosphorus) — root and flower development
• K (Potassium) — overall plant health and disease resistance

WHEN TO FERTILIZE:
• Active growing season: every 2-4 weeks
• Winter: reduce to once a month or stop entirely
• Never fertilize a dry plant — water first
• Skip for 4-6 weeks after repotting

HOW TO FERTILIZE:
1. Dilute liquid fertilizer to half recommended strength
2. Water the plant lightly first
3. Apply fertilizer solution to moist soil
4. Water again lightly to distribute

SIGNS OF OVER-FERTILIZING:
• White crust on soil surface (salt buildup)
• Burned/brown leaf tips and edges
• Wilting despite adequate water
• Slow growth (paradoxically)

TIPS:
• Less is more — it's easier to fix under-feeding than over-feeding
• Use balanced fertilizer (10-10-10) for most plants
• Flowering plants need higher phosphorus (e.g., 10-30-20)
• Organic options: worm castings, compost tea, fish emulsion
            """.trimIndent(),
            contentRu = """
Подкормка обеспечивает необходимые питательные вещества, которые со временем истощаются в почве. Подкармливайте в период вегетации (весна-лето).

ОСНОВЫ NPK:
• N (Азот) — рост листьев и стеблей
• P (Фосфор) — развитие корней и цветов
• K (Калий) — общее здоровье и устойчивость к болезням

КОГДА ПОДКАРМЛИВАТЬ:
• Период роста: каждые 2-4 недели
• Зимой: раз в месяц или прекратить полностью
• Никогда не удобряйте сухое растение — сначала полейте
• Пропустите 4-6 недель после пересадки

КАК ПОДКАРМЛИВАТЬ:
1. Разведите жидкое удобрение до половины рекомендуемой дозы
2. Слегка полейте растение
3. Нанесите раствор удобрения на влажную почву
4. Слегка полейте снова для распределения

ПРИЗНАКИ ПЕРЕКОРМКИ:
• Белый налёт на поверхности почвы (скопление солей)
• Ожоги/коричневые кончики и края листьев
• Увядание при достаточном поливе
• Замедление роста (парадоксально)

СОВЕТЫ:
• Меньше — лучше, перекормку исправить сложнее
• Используйте сбалансированное удобрение (10-10-10) для большинства
• Цветущим нужен повышенный фосфор (10-30-20)
• Органика: биогумус, компостный чай, рыбная эмульсия
            """.trimIndent(),
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS
        ),

        GuideItem(
            id = "soil_guide",
            titleEn = "Soil & Substrates",
            titleRu = "Почва и субстраты",
            descriptionEn = "Choosing the right growing medium",
            descriptionRu = "Выбор правильного грунта для растений",
            contentEn = """
The right soil mix ensures proper drainage, aeration, and nutrient availability for your plants.

SOIL COMPONENTS:
• Peat moss/coco coir — retains moisture
• Perlite — improves drainage and aeration
• Vermiculite — retains moisture and nutrients
• Bark chips — drainage and aeration for orchids/epiphytes
• Sand — improves drainage for succulents

COMMON MIXES:
• Standard houseplant: 2 parts peat + 1 part perlite + 1 part compost
• Succulents/cacti: 1 part soil + 1 part sand + 1 part perlite
• Orchids: bark chips + sphagnum moss + perlite
• Aroids (Monstera, Philodendron): 1 part soil + 1 part perlite + 1 part bark

SIGNS OF BAD SOIL:
• Water pools on surface and doesn't absorb
• Soil is compacted and hard
• Moldy surface layer
• Roots circling the pot without spreading

TIPS:
• Replace soil every 1-2 years
• Never use garden soil for indoor plants — it compacts
• Sterilize homemade mixes to prevent pests
• pH of 6.0-7.0 suits most houseplants
            """.trimIndent(),
            contentRu = """
Правильная почвенная смесь обеспечивает дренаж, аэрацию и доступность питательных веществ.

КОМПОНЕНТЫ ПОЧВЫ:
• Торф/кокосовое волокно — удерживает влагу
• Перлит — улучшает дренаж и аэрацию
• Вермикулит — удерживает влагу и питательные вещества
• Кора — дренаж для орхидей/эпифитов
• Песок — улучшает дренаж для суккулентов

РАСПРОСТРАНЁННЫЕ СМЕСИ:
• Стандартная: 2 части торфа + 1 часть перлита + 1 часть компоста
• Суккуленты/кактусы: 1 часть почвы + 1 часть песка + 1 часть перлита
• Орхидеи: кора + мох сфагнум + перлит
• Ароидные (Монстера, Филодендрон): 1 часть почвы + 1 часть перлита + 1 часть коры

ПРИЗНАКИ ПЛОХОЙ ПОЧВЫ:
• Вода скапливается на поверхности
• Почва уплотнённая и твёрдая
• Заплесневевший верхний слой
• Корни закручиваются без разрастания

СОВЕТЫ:
• Обновляйте почву каждые 1-2 года
• Не используйте садовую землю — она уплотняется
• Стерилизуйте самодельные смеси
• pH 6.0-7.0 подходит для большинства комнатных
            """.trimIndent(),
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS
        ),

        GuideItem(
            id = "seasonal_care",
            titleEn = "Seasonal Plant Care",
            titleRu = "Сезонный уход за растениями",
            descriptionEn = "Adjusting care with changing seasons",
            descriptionRu = "Изменение ухода в зависимости от сезона",
            contentEn = """
Plants respond to seasonal changes even indoors. Adjust your care routine throughout the year.

SPRING (March-May):
• Increase watering as growth accelerates
• Start fertilizing again (every 2-4 weeks)
• Repot if needed — best time of year
• Prune leggy growth from winter
• Check for pests waking up

SUMMER (June-August):
• Water more frequently (may need daily checks)
• Continue regular fertilizing
• Provide shade from intense afternoon sun
• Monitor for pests — they're most active now
• Increase humidity if using AC

FALL (September-November):
• Gradually reduce watering and fertilizing
• Bring outdoor plants inside before frost
• Move plants to brighter spots as days shorten
• Stop repotting — plants are slowing down
• Check for hitchhiker pests on returning plants

WINTER (December-February):
• Reduce watering significantly
• Stop or reduce fertilizing
• Keep away from cold drafts and heating vents
• Provide supplemental light if needed
• Increase humidity (heating dries indoor air)
            """.trimIndent(),
            contentRu = """
Растения реагируют на сезонные изменения даже в помещении. Корректируйте уход в течение года.

ВЕСНА (Март-Май):
• Увеличьте полив по мере ускорения роста
• Начните подкормки (каждые 2-4 недели)
• Пересадите при необходимости — лучшее время
• Обрежьте вытянувшийся зимний рост
• Проверьте на пробуждающихся вредителей

ЛЕТО (Июнь-Август):
• Поливайте чаще (проверяйте ежедневно)
• Продолжайте регулярные подкормки
• Защитите от интенсивного полуденного солнца
• Следите за вредителями — они наиболее активны
• Увеличьте влажность при работающем кондиционере

ОСЕНЬ (Сентябрь-Ноябрь):
• Постепенно сокращайте полив и подкормки
• Занесите уличные растения до морозов
• Переместите к более светлым местам
• Прекратите пересадки — рост замедляется
• Проверьте вернувшиеся растения на вредителей

ЗИМА (Декабрь-Февраль):
• Существенно сократите полив
• Прекратите или сократите подкормки
• Держите подальше от сквозняков и батарей
• Обеспечьте дополнительное освещение
• Увеличьте влажность (отопление сушит воздух)
            """.trimIndent(),
            iconRes = R.drawable.ic_care,
            category = GuideCategory.CARE_TIPS
        )
    )

    fun getByCategory(category: GuideCategory): List<GuideItem> =
        byCategory[category].orEmpty()
}
