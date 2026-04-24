"""
Bilingual disease information database for PlantScope v3.x.

Maps Stage 2 classifier class names to houseplant-specific descriptions,
treatments, and prevention tips. The database is intentionally focused on
diseases and pests most common on indoor/houseplants — even though the
visual patterns generalise from PlantDoc's real-world crop photos.

Authoritative class list: server/models/classes.json.
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

    "blight": {
        "name_en": "Blight",
        "name_ru": "Фитофтороз",
        "description_en": "A rapidly spreading fungal disease that causes large, dark, water-soaked lesions on leaves and stems. On houseplants it is most often triggered by overwatering combined with cool temperatures.",
        "description_ru": "Быстро прогрессирующее грибковое заболевание: крупные тёмные водянистые поражения на листьях и стеблях. На комнатных растениях чаще всего возникает при переувлажнении и прохладе.",
        "treatment_en": [
            "Remove and destroy every affected part immediately — do not compost",
            "Apply a systemic or copper-based fungicide",
            "Reduce watering and let the substrate dry deeply",
            "Raise the temperature above 20 °C and reduce humidity",
            "If more than 50 % of the plant is affected, consider discarding it to protect the rest"
        ],
        "treatment_ru": [
            "Немедленно удалите и уничтожьте все поражённые части — не в компост",
            "Обработайте системным фунгицидом или препаратом на основе меди",
            "Сократите полив, дайте субстрату глубоко просохнуть",
            "Поднимите температуру выше 20 °C и снизьте влажность",
            "Если поражено больше 50 % растения — лучше утилизировать его, чтобы не заразить остальные"
        ],
        "prevention_en": [
            "Never let pots stand in water — empty saucers after watering",
            "Space plants for good airflow",
            "Keep the room above 18 °C in winter",
            "Inspect the collection weekly during cool, damp weather"
        ],
        "prevention_ru": [
            "Никогда не оставляйте горшки в воде — сливайте из поддона лишнюю",
            "Расставляйте растения свободно для хорошей вентиляции",
            "Зимой поддерживайте температуру выше 18 °C",
            "В сырую прохладную погоду осматривайте коллекцию еженедельно"
        ],
        "is_healthy": False
    },

    "rust": {
        "name_en": "Rust",
        "name_ru": "Ржавчина",
        "description_en": "A fungal disease that produces orange, rust-coloured pustules on the undersides of leaves, with matching yellow spots on top. Common on pelargoniums, fuchsias, and chrysanthemums.",
        "description_ru": "Грибковое заболевание: оранжевые «ржавые» пустулы на нижней стороне листьев и соответствующие жёлтые пятна сверху. Часто на пеларгониях, фуксиях, хризантемах.",
        "treatment_en": [
            "Remove and destroy all leaves showing pustules",
            "Apply a sulfur-based or copper fungicide every 7–14 days",
            "Do not mist and do not compost affected material",
            "Improve air circulation around the plant",
            "Isolate infected plants from the rest of the collection"
        ],
        "treatment_ru": [
            "Удалите и уничтожьте все листья с пустулами",
            "Раз в 7–14 дней обрабатывайте фунгицидом на основе серы или меди",
            "Прекратите опрыскивание; поражённые части не компостируйте",
            "Улучшите вентиляцию вокруг растения",
            "Изолируйте заражённое растение от остальных"
        ],
        "prevention_en": [
            "Water in the morning so leaves dry quickly",
            "Keep foliage dry — skip overhead misting for susceptible species",
            "Remove fallen leaves promptly",
            "Give plants enough space to allow airflow"
        ],
        "prevention_ru": [
            "Поливайте утром, чтобы листья быстро высыхали",
            "Не опрыскивайте восприимчивые виды сверху",
            "Своевременно убирайте опавшие листья",
            "Расставляйте растения так, чтобы был воздух между ними"
        ],
        "is_healthy": False
    },

    "mosaic_virus": {
        "name_en": "Mosaic Virus",
        "name_ru": "Мозаичный вирус",
        "description_en": "A viral infection that creates a mottled, patchwork pattern of light and dark green on the leaves. Leaves may distort or curl. There is no cure — infected plants are usually discarded to protect the collection.",
        "description_ru": "Вирусная инфекция: характерный пёстрый узор из светло- и тёмно-зелёных участков на листьях. Листья могут деформироваться и скручиваться. Лечения нет — заражённые растения обычно утилизируют, чтобы защитить коллекцию.",
        "treatment_en": [
            "There is no cure for plant viruses",
            "Discard severely affected plants and the substrate they grew in",
            "Mild cases may be kept in isolation but will not recover",
            "Control aphids, thrips and whiteflies — they are the main vectors",
            "Wash hands and sterilise tools after touching an infected plant"
        ],
        "treatment_ru": [
            "Вирусные инфекции растений не лечатся",
            "Сильно поражённые растения утилизируйте вместе с субстратом",
            "Слабо поражённые можно оставить в изоляции, но они не выздоровеют",
            "Боритесь с тлёй, трипсами и белокрылкой — это главные переносчики",
            "После контакта с больным растением мойте руки и дезинфицируйте инструмент"
        ],
        "prevention_en": [
            "Buy plants from trusted sources — check leaves before buying",
            "Quarantine new arrivals for 2–3 weeks",
            "Use sterile tools and clean pots",
            "Don't handle plants after smoking (tobacco can carry mosaic virus)"
        ],
        "prevention_ru": [
            "Покупайте растения в проверенных местах, осматривайте листья",
            "Новые растения держите 2–3 недели на карантине",
            "Используйте стерильные инструменты и чистые горшки",
            "Не работайте с растениями сразу после курения (табак переносит вирус)"
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

    "leaf_mold": {
        "name_en": "Leaf Mold",
        "name_ru": "Листовая плесень",
        "description_en": "A fungal disease that shows up as pale yellow patches on the upper leaf surface and olive-brown fuzzy mold on the underside. Develops quickly in humid, poorly ventilated rooms.",
        "description_ru": "Грибковое заболевание: светло-жёлтые пятна сверху листа и оливково-коричневая пушистая плесень снизу. Быстро развивается во влажных помещениях без вентиляции.",
        "treatment_en": [
            "Carefully remove infected leaves",
            "Drop humidity below 70 % and boost ventilation immediately",
            "Apply a copper- or sulfur-based fungicide once a week",
            "Space plants out so air can flow around each one",
            "Water only at the soil level, in the morning"
        ],
        "treatment_ru": [
            "Аккуратно удалите заражённые листья",
            "Снизьте влажность ниже 70 % и резко улучшите проветривание",
            "Раз в неделю обрабатывайте фунгицидом на основе меди или серы",
            "Расставьте растения так, чтобы воздух свободно циркулировал",
            "Поливайте только под корень и утром"
        ],
        "prevention_en": [
            "Keep room humidity in the 40–65 % range",
            "Avoid night-time misting",
            "Ensure the room has good passive or active air exchange",
            "Clean the soil surface from fallen debris regularly"
        ],
        "prevention_ru": [
            "Поддерживайте влажность помещения 40–65 %",
            "Не опрыскивайте растения на ночь",
            "Обеспечьте воздухообмен (естественный или приточный)",
            "Регулярно очищайте поверхность субстрата от опавших листьев"
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
