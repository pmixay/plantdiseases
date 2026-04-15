"""
Bilingual disease information database.
Maps CNN class names to full disease descriptions, treatments, and prevention tips.
"""

DISEASES_DATABASE = {
    "healthy": {
        "name_en": "Healthy Plant",
        "name_ru": "Здоровое растение",
        "description_en": "Your plant appears healthy! No signs of disease detected. Keep up the great care routine.",
        "description_ru": "Ваше растение выглядит здоровым! Признаков болезни не обнаружено. Продолжайте хороший уход.",
        "treatment_en": [
            "No treatment needed — your plant is doing well!",
            "Continue regular watering and feeding schedule",
            "Monitor for any changes in leaf color or texture"
        ],
        "treatment_ru": [
            "Лечение не требуется — ваше растение в порядке!",
            "Продолжайте регулярный полив и подкормку",
            "Наблюдайте за изменениями цвета или текстуры листьев"
        ],
        "prevention_en": [
            "Maintain consistent watering schedule",
            "Ensure proper light conditions",
            "Fertilize during growing season",
            "Inspect plants regularly for early signs of problems"
        ],
        "prevention_ru": [
            "Соблюдайте регулярный график полива",
            "Обеспечьте правильное освещение",
            "Удобряйте в период вегетации",
            "Регулярно осматривайте растения на ранние признаки проблем"
        ],
        "is_healthy": True
    },

    "bacterial_spot": {
        "name_en": "Bacterial Spot",
        "name_ru": "Бактериальная пятнистость",
        "description_en": "Bacterial leaf spot causes dark, water-soaked lesions on leaves that may have yellow halos. It spreads through water splash and infected tools.",
        "description_ru": "Бактериальная пятнистость вызывает тёмные, водянистые поражения на листьях с жёлтыми ореолами. Распространяется через брызги воды и заражённые инструменты.",
        "treatment_en": [
            "Remove and destroy all affected leaves immediately",
            "Apply copper-based bactericide spray",
            "Reduce watering frequency and avoid wetting leaves",
            "Improve air circulation around the plant",
            "Disinfect all tools with 70% rubbing alcohol"
        ],
        "treatment_ru": [
            "Немедленно удалите и уничтожьте все поражённые листья",
            "Нанесите бактерицидный спрей на основе меди",
            "Сократите частоту полива, избегайте попадания воды на листья",
            "Улучшите циркуляцию воздуха вокруг растения",
            "Дезинфицируйте все инструменты 70% спиртом"
        ],
        "prevention_en": [
            "Water at soil level, never on foliage",
            "Space plants for good air circulation",
            "Sterilize pots and tools before use",
            "Quarantine new plants for 2 weeks"
        ],
        "prevention_ru": [
            "Поливайте на уровне почвы, никогда на листья",
            "Размещайте растения на расстоянии для хорошей вентиляции",
            "Стерилизуйте горшки и инструменты перед использованием",
            "Новые растения держите на карантине 2 недели"
        ],
        "is_healthy": False
    },

    "early_blight": {
        "name_en": "Early Blight",
        "name_ru": "Ранний фитофтороз",
        "description_en": "Early blight is a fungal disease that creates concentric ring patterns (target spots) on older leaves first. Common in warm, humid conditions.",
        "description_ru": "Ранний фитофтороз — грибковое заболевание, создающее концентрические кольцевые узоры (мишеневидные пятна) сначала на старых листьях. Характерен для тёплых, влажных условий.",
        "treatment_en": [
            "Remove infected leaves and dispose of them (do not compost)",
            "Apply fungicide containing chlorothalonil or copper",
            "Ensure proper spacing between plants",
            "Water at the base of the plant in the morning",
            "Apply mulch to prevent soil splash onto leaves"
        ],
        "treatment_ru": [
            "Удалите заражённые листья и утилизируйте их (не компостируйте)",
            "Примените фунгицид, содержащий хлороталонил или медь",
            "Обеспечьте достаточное расстояние между растениями",
            "Поливайте у основания растения утром",
            "Мульчируйте почву для предотвращения брызг на листья"
        ],
        "prevention_en": [
            "Rotate plant locations annually",
            "Ensure good air circulation",
            "Avoid overhead watering",
            "Remove plant debris regularly"
        ],
        "prevention_ru": [
            "Ежегодно меняйте расположение растений",
            "Обеспечьте хорошую циркуляцию воздуха",
            "Избегайте верхнего полива",
            "Регулярно убирайте растительные остатки"
        ],
        "is_healthy": False
    },

    "late_blight": {
        "name_en": "Late Blight",
        "name_ru": "Фитофтороз",
        "description_en": "Late blight causes large, dark, water-soaked lesions that spread rapidly. Leaves may show white fuzzy growth on undersides in humid conditions.",
        "description_ru": "Фитофтороз вызывает крупные, тёмные, водянистые поражения, которые быстро распространяются. На нижней стороне листьев может появляться белый пушистый налёт.",
        "treatment_en": [
            "Remove and destroy all affected parts immediately",
            "Apply copper-based or systemic fungicide",
            "Increase spacing and air circulation",
            "Reduce humidity around the plant",
            "In severe cases, the plant may need to be discarded"
        ],
        "treatment_ru": [
            "Немедленно удалите и уничтожьте все поражённые части",
            "Примените фунгицид на основе меди или системный фунгицид",
            "Увеличьте расстояние между растениями и вентиляцию",
            "Снизьте влажность вокруг растения",
            "В тяжёлых случаях растение может потребовать утилизации"
        ],
        "prevention_en": [
            "Avoid excess moisture on foliage",
            "Provide good ventilation",
            "Inspect plants regularly during humid weather",
            "Use disease-resistant varieties when possible"
        ],
        "prevention_ru": [
            "Избегайте избыточной влаги на листве",
            "Обеспечьте хорошую вентиляцию",
            "Осматривайте растения регулярно во влажную погоду",
            "Используйте устойчивые к болезням сорта"
        ],
        "is_healthy": False
    },

    "leaf_mold": {
        "name_en": "Leaf Mold",
        "name_ru": "Листовая плесень",
        "description_en": "Leaf mold causes pale green or yellowish spots on upper leaf surfaces with olive-green to brown fuzzy mold on the undersides. Thrives in high humidity.",
        "description_ru": "Листовая плесень вызывает бледно-зелёные или желтоватые пятна на верхней стороне листьев с оливково-зелёной или коричневой пушистой плесенью снизу. Развивается при высокой влажности.",
        "treatment_en": [
            "Remove affected leaves carefully",
            "Improve ventilation dramatically",
            "Reduce humidity below 70%",
            "Apply fungicide spray (sulfur or copper-based)",
            "Space out plants to improve airflow"
        ],
        "treatment_ru": [
            "Аккуратно удалите поражённые листья",
            "Существенно улучшите вентиляцию",
            "Снизьте влажность ниже 70%",
            "Примените фунгицидный спрей (на основе серы или меди)",
            "Расставьте растения для улучшения воздушного потока"
        ],
        "prevention_en": [
            "Maintain humidity between 40-65%",
            "Ensure excellent air circulation",
            "Avoid crowding plants",
            "Water in the morning so leaves dry quickly"
        ],
        "prevention_ru": [
            "Поддерживайте влажность 40-65%",
            "Обеспечьте отличную циркуляцию воздуха",
            "Не ставьте растения слишком близко",
            "Поливайте утром, чтобы листья быстро высохли"
        ],
        "is_healthy": False
    },

    "septoria_leaf_spot": {
        "name_en": "Septoria Leaf Spot",
        "name_ru": "Септориоз",
        "description_en": "Septoria creates small, circular spots with dark borders and light gray centers. Tiny black dots (pycnidia) may be visible in the spots.",
        "description_ru": "Септориоз создаёт маленькие круглые пятна с тёмными краями и светло-серыми центрами. В пятнах могут быть видны крошечные чёрные точки (пикнидии).",
        "treatment_en": [
            "Remove all leaves showing symptoms",
            "Apply fungicide (chlorothalonil or copper-based)",
            "Water only at soil level",
            "Clean up fallen leaves and debris",
            "Treat weekly until symptoms stop"
        ],
        "treatment_ru": [
            "Удалите все листья с симптомами",
            "Примените фунгицид (хлороталонил или на основе меди)",
            "Поливайте только на уровне почвы",
            "Уберите опавшие листья и мусор",
            "Обрабатывайте еженедельно до исчезновения симптомов"
        ],
        "prevention_en": [
            "Ensure good air circulation",
            "Mulch around plants to prevent splash",
            "Avoid working with wet plants",
            "Remove debris from plant area"
        ],
        "prevention_ru": [
            "Обеспечьте хорошую циркуляцию воздуха",
            "Мульчируйте для предотвращения брызг",
            "Не работайте с мокрыми растениями",
            "Убирайте мусор из зоны растений"
        ],
        "is_healthy": False
    },

    "spider_mites": {
        "name_en": "Spider Mites",
        "name_ru": "Паутинный клещ",
        "description_en": "Spider mites are tiny pests that suck cell contents from leaves, causing stippling, discoloration, and fine webbing. They thrive in dry, warm conditions.",
        "description_ru": "Паутинный клещ — мелкие вредители, высасывающие содержимое клеток листьев, вызывая крапчатость, обесцвечивание и тонкую паутину. Процветают в сухих, тёплых условиях.",
        "treatment_en": [
            "Spray the plant thoroughly with water to wash off mites",
            "Apply insecticidal soap or neem oil solution",
            "Repeat treatment every 5-7 days for at least 3 weeks",
            "Increase humidity around the plant (misting, pebble tray)",
            "Isolate infested plants immediately"
        ],
        "treatment_ru": [
            "Тщательно опрыскайте растение водой для смывания клещей",
            "Нанесите инсектицидное мыло или раствор масла нима",
            "Повторяйте обработку каждые 5-7 дней не менее 3 недель",
            "Увеличьте влажность (опрыскивание, поддон с галькой)",
            "Немедленно изолируйте заражённые растения"
        ],
        "prevention_en": [
            "Mist plants regularly to keep humidity up",
            "Wipe leaves with a damp cloth weekly",
            "Inspect new plants thoroughly before bringing home",
            "Keep plants healthy — stressed plants attract mites"
        ],
        "prevention_ru": [
            "Регулярно опрыскивайте растения для повышения влажности",
            "Еженедельно протирайте листья влажной тканью",
            "Тщательно осматривайте новые растения перед покупкой",
            "Поддерживайте здоровье растений — ослабленные привлекают клещей"
        ],
        "is_healthy": False
    },

    "target_spot": {
        "name_en": "Target Spot",
        "name_ru": "Мишеневидная пятнистость",
        "description_en": "Target spot produces circular brown lesions with concentric rings resembling a target. It can affect leaves, stems, and fruits.",
        "description_ru": "Мишеневидная пятнистость образует круглые коричневые поражения с концентрическими кольцами, напоминающими мишень. Может поражать листья, стебли и плоды.",
        "treatment_en": [
            "Prune and remove all affected plant parts",
            "Apply broad-spectrum fungicide",
            "Improve air flow around plants",
            "Avoid watering foliage directly",
            "Keep the growing area clean"
        ],
        "treatment_ru": [
            "Обрежьте и удалите все поражённые части",
            "Примените фунгицид широкого спектра",
            "Улучшите воздушный поток вокруг растений",
            "Избегайте прямого полива листвы",
            "Содержите зону выращивания в чистоте"
        ],
        "prevention_en": [
            "Rotate plant positions",
            "Water at ground level",
            "Maintain good air circulation",
            "Clean tools between plants"
        ],
        "prevention_ru": [
            "Чередуйте расположение растений",
            "Поливайте на уровне почвы",
            "Поддерживайте хорошую циркуляцию воздуха",
            "Очищайте инструменты между растениями"
        ],
        "is_healthy": False
    },

    "mosaic_virus": {
        "name_en": "Mosaic Virus",
        "name_ru": "Мозаичный вирус",
        "description_en": "Mosaic virus causes a distinctive mottled pattern of light and dark green on leaves. Leaves may be distorted or curled. There is no cure — management is key.",
        "description_ru": "Мозаичный вирус вызывает характерный пёстрый узор из светло- и тёмно-зелёных участков на листьях. Листья могут быть деформированы или скручены. Лечения нет — ключ в управлении.",
        "treatment_en": [
            "There is no cure for viral infections in plants",
            "Remove and destroy severely infected plants",
            "Mildly affected plants can be kept but will not recover",
            "Control aphids and other vectors that spread the virus",
            "Sterilize all tools that contact infected plants"
        ],
        "treatment_ru": [
            "Вирусные инфекции растений не лечатся",
            "Удалите и уничтожьте сильно поражённые растения",
            "Слабо поражённые растения можно оставить, но они не выздоровеют",
            "Контролируйте тлю и других переносчиков вируса",
            "Стерилизуйте все инструменты, контактировавшие с больными растениями"
        ],
        "prevention_en": [
            "Buy certified disease-free plants",
            "Control insect vectors (especially aphids)",
            "Wash hands before handling plants",
            "Don't use tobacco products near plants"
        ],
        "prevention_ru": [
            "Покупайте сертифицированные здоровые растения",
            "Контролируйте насекомых-переносчиков (особенно тлю)",
            "Мойте руки перед работой с растениями",
            "Не используйте табачные изделия рядом с растениями"
        ],
        "is_healthy": False
    },

    "yellow_leaf_curl": {
        "name_en": "Yellow Leaf Curl",
        "name_ru": "Жёлтое скручивание листьев",
        "description_en": "Yellow leaf curl virus causes leaves to curl upward and turn yellow. Growth becomes stunted and the plant produces fewer flowers.",
        "description_ru": "Вирус жёлтого скручивания листьев вызывает скручивание листьев вверх и пожелтение. Рост замедляется, растение даёт меньше цветов.",
        "treatment_en": [
            "No cure exists — remove severely affected plants",
            "Control whitefly populations (main vector)",
            "Use yellow sticky traps near plants",
            "Apply neem oil to repel whiteflies",
            "Keep plants strong with proper nutrition"
        ],
        "treatment_ru": [
            "Лечения не существует — удалите сильно поражённые растения",
            "Контролируйте популяцию белокрылки (основной переносчик)",
            "Используйте жёлтые клейкие ловушки рядом с растениями",
            "Нанесите масло нима для отпугивания белокрылки",
            "Поддерживайте здоровье растений правильным питанием"
        ],
        "prevention_en": [
            "Use reflective mulches to deter whiteflies",
            "Screen windows if growing indoors near open windows",
            "Quarantine new plants for 2-3 weeks",
            "Inspect plants regularly for whiteflies"
        ],
        "prevention_ru": [
            "Используйте светоотражающую мульчу для отпугивания белокрылки",
            "Защитите окна сетками при выращивании у открытых окон",
            "Карантинируйте новые растения 2-3 недели",
            "Регулярно осматривайте растения на наличие белокрылки"
        ],
        "is_healthy": False
    },

    "powdery_mildew": {
        "name_en": "Powdery Mildew",
        "name_ru": "Мучнистая роса",
        "description_en": "Powdery mildew appears as white or gray powdery patches on leaf surfaces. It thrives in warm, dry air with high humidity at the leaf surface.",
        "description_ru": "Мучнистая роса проявляется как белый или серый порошкообразный налёт на поверхности листьев. Развивается в тёплом сухом воздухе при высокой влажности у поверхности листа.",
        "treatment_en": [
            "Remove severely affected leaves",
            "Spray with baking soda solution (1 tsp/liter water + drop of soap)",
            "Apply neem oil solution weekly",
            "Use milk spray (40% milk, 60% water) as organic alternative",
            "Improve air circulation immediately"
        ],
        "treatment_ru": [
            "Удалите сильно поражённые листья",
            "Опрыскайте раствором соды (1 ч.л./литр воды + капля мыла)",
            "Еженедельно применяйте раствор масла нима",
            "Используйте молочный раствор (40% молоко, 60% вода) как органическую альтернативу",
            "Немедленно улучшите циркуляцию воздуха"
        ],
        "prevention_en": [
            "Ensure good air circulation around plants",
            "Avoid overhead watering",
            "Don't crowd plants together",
            "Provide adequate light"
        ],
        "prevention_ru": [
            "Обеспечьте хорошую циркуляцию воздуха",
            "Избегайте верхнего полива",
            "Не ставьте растения слишком близко",
            "Обеспечьте достаточное освещение"
        ],
        "is_healthy": False
    },

    "rust": {
        "name_en": "Rust Disease",
        "name_ru": "Ржавчина",
        "description_en": "Rust fungus produces orange, yellow, or brown pustules on the undersides of leaves. Upper surfaces show yellow spots corresponding to pustule locations.",
        "description_ru": "Грибок ржавчины образует оранжевые, жёлтые или коричневые пустулы на нижней стороне листьев. На верхней поверхности видны жёлтые пятна в соответствующих местах.",
        "treatment_en": [
            "Remove and destroy all infected leaves",
            "Apply sulfur-based or copper fungicide",
            "Do not compost infected material",
            "Improve air circulation",
            "Treat every 7-14 days until symptoms stop"
        ],
        "treatment_ru": [
            "Удалите и уничтожьте все заражённые листья",
            "Примените фунгицид на основе серы или меди",
            "Не компостируйте заражённый материал",
            "Улучшите циркуляцию воздуха",
            "Обрабатывайте каждые 7-14 дней до исчезновения симптомов"
        ],
        "prevention_en": [
            "Water in the morning at soil level",
            "Keep foliage dry",
            "Remove fallen leaves promptly",
            "Provide adequate spacing between plants"
        ],
        "prevention_ru": [
            "Поливайте утром на уровне почвы",
            "Содержите листву сухой",
            "Своевременно убирайте опавшие листья",
            "Обеспечьте достаточное расстояние между растениями"
        ],
        "is_healthy": False
    },

    "root_rot": {
        "name_en": "Root Rot",
        "name_ru": "Корневая гниль",
        "description_en": "Root rot is caused by overwatering and poor drainage, leading to fungal infection of the roots. Roots turn brown and mushy, and the plant wilts despite wet soil.",
        "description_ru": "Корневая гниль вызвана переувлажнением и плохим дренажом, что приводит к грибковой инфекции корней. Корни становятся коричневыми и мягкими, растение вянет несмотря на влажную почву.",
        "treatment_en": [
            "Remove plant from pot and wash away all soil",
            "Cut away all brown, mushy, or smelly roots with sterile scissors",
            "Let roots air-dry for several hours",
            "Repot in fresh, well-draining potting mix",
            "Water sparingly for the first 2-3 weeks after repotting"
        ],
        "treatment_ru": [
            "Извлеките растение из горшка и смойте всю почву",
            "Обрежьте все коричневые, мягкие или пахнущие корни стерильными ножницами",
            "Дайте корням подсохнуть несколько часов",
            "Пересадите в свежую, хорошо дренированную почву",
            "Поливайте умеренно первые 2-3 недели после пересадки"
        ],
        "prevention_en": [
            "Always use pots with drainage holes",
            "Water only when the top 2-3cm of soil is dry",
            "Use well-draining potting mix with perlite",
            "Never let pots sit in standing water"
        ],
        "prevention_ru": [
            "Всегда используйте горшки с дренажными отверстиями",
            "Поливайте только когда верхние 2-3 см почвы сухие",
            "Используйте хорошо дренированную почву с перлитом",
            "Никогда не оставляйте горшки в стоячей воде"
        ],
        "is_healthy": False
    },

    "anthracnose": {
        "name_en": "Anthracnose",
        "name_ru": "Антракноз",
        "description_en": "Anthracnose causes dark, sunken lesions on leaves, stems, and fruits. The spots may have pink or orange spore masses in humid conditions.",
        "description_ru": "Антракноз вызывает тёмные, вдавленные поражения на листьях, стеблях и плодах. Пятна могут содержать розовые или оранжевые массы спор во влажных условиях.",
        "treatment_en": [
            "Remove and destroy affected plant parts",
            "Apply copper-based fungicide",
            "Reduce watering and improve drainage",
            "Increase air circulation",
            "Disinfect pruning tools between cuts"
        ],
        "treatment_ru": [
            "Удалите и уничтожьте поражённые части",
            "Примените фунгицид на основе меди",
            "Сократите полив и улучшите дренаж",
            "Увеличьте циркуляцию воздуха",
            "Дезинфицируйте секатор между срезами"
        ],
        "prevention_en": [
            "Avoid overhead watering",
            "Provide good air circulation",
            "Clean up plant debris regularly",
            "Use sterile potting mix"
        ],
        "prevention_ru": [
            "Избегайте верхнего полива",
            "Обеспечьте хорошую вентиляцию",
            "Регулярно убирайте растительные остатки",
            "Используйте стерильную почвенную смесь"
        ],
        "is_healthy": False
    },

    "botrytis": {
        "name_en": "Botrytis (Gray Mold)",
        "name_ru": "Ботритис (Серая гниль)",
        "description_en": "Botrytis, or gray mold, produces fuzzy gray-brown mold on flowers, buds, leaves, and stems. It thrives in cool, damp conditions.",
        "description_ru": "Ботритис, или серая гниль, образует пушистую серо-коричневую плесень на цветах, бутонах, листьях и стеблях. Развивается в прохладных, влажных условиях.",
        "treatment_en": [
            "Remove all infected parts immediately",
            "Improve ventilation and reduce humidity",
            "Avoid misting or overhead watering",
            "Apply fungicide if infection is widespread",
            "Remove dead or dying flowers promptly"
        ],
        "treatment_ru": [
            "Немедленно удалите все заражённые части",
            "Улучшите вентиляцию и снизьте влажность",
            "Избегайте опрыскивания и верхнего полива",
            "Примените фунгицид при обширном поражении",
            "Своевременно удаляйте увядшие цветы"
        ],
        "prevention_en": [
            "Keep humidity below 70%",
            "Provide excellent air circulation",
            "Remove dead plant material promptly",
            "Water plants in the morning"
        ],
        "prevention_ru": [
            "Поддерживайте влажность ниже 70%",
            "Обеспечьте отличную циркуляцию воздуха",
            "Своевременно удаляйте мёртвые части растений",
            "Поливайте растения утром"
        ],
        "is_healthy": False
    },

    # Fallback for unknown classes
    "unknown": {
        "name_en": "Unidentified Condition",
        "name_ru": "Неопределённое состояние",
        "description_en": "The analysis could not confidently identify a specific disease. The plant may have a condition not in our database, or the image quality may be insufficient.",
        "description_ru": "Анализ не смог уверенно определить конкретное заболевание. Растение может иметь состояние, не включённое в нашу базу данных, или качество изображения может быть недостаточным.",
        "treatment_en": [
            "Try taking another photo in better lighting",
            "Focus on the affected area of the plant",
            "Ensure the image is sharp and well-lit",
            "If symptoms persist, consult a local plant expert"
        ],
        "treatment_ru": [
            "Попробуйте сделать другое фото при лучшем освещении",
            "Сфокусируйтесь на поражённом участке растения",
            "Убедитесь, что изображение чёткое и хорошо освещённое",
            "Если симптомы сохраняются, обратитесь к местному специалисту"
        ],
        "prevention_en": [
            "Monitor your plant regularly for changes",
            "Maintain good watering and light practices",
            "Keep the growing area clean"
        ],
        "prevention_ru": [
            "Регулярно наблюдайте за растением",
            "Соблюдайте правильный полив и освещение",
            "Содержите зону выращивания в чистоте"
        ],
        "is_healthy": False
    }
}
