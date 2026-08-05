package com.plantdiseases.app.util

/** Lunar-gardening advice category. Mirrors traditional almanac groupings so
 *  the lunar calendar can differ tomato advice from cactus advice. */
enum class PlantCategory { FRUITING, ROOT, LEAFY, FLOWERING, SUCCULENT, FOLIAGE, HERB }

/**
 * Maps free-text species (scientific + common name) to a [PlantCategory].
 *
 * The app's plant set is open-ended — species arrive as free text from
 * PlantNet/Wikipedia/community scans, never from a fixed catalog — so this is
 * a pure best-effort classifier with a safe default, never a lookup that can
 * fail. Default is [PlantCategory.FOLIAGE]: this is a houseplant app, foliage
 * houseplants are the modal case.
 */
object PlantCategoryClassifier {

    fun classify(scientificName: String?, commonName: String?): PlantCategory {
        binomial(scientificName)?.let { b -> BINOMIAL_OVERRIDES[b]?.let { return it } }
        genus(scientificName)?.let { g -> GENUS_MAP[g]?.let { return it } }
        val tokens = normalize(commonName) + normalize(scientificName)
        for (category in PRIORITY) {
            val stems = KEYWORDS.getValue(category)
            if (tokens.any { token -> stems.any { stem -> token.startsWith(stem) } }) {
                return category
            }
        }
        return PlantCategory.FOLIAGE
    }

    /** lowercase, ё→е, every non-letter → space, whitespace collapsed, tokens <3 chars dropped. */
    internal fun normalize(raw: String?): List<String> {
        if (raw.isNullOrBlank()) return emptyList()
        val cleaned = raw.lowercase().replace('ё', 'е').replace(Regex("[^a-zа-я]+"), " ")
        return cleaned.split(' ').filter { it.length >= 3 }
    }

    /** "genus species", lowercase, or null when fewer than two tokens. */
    internal fun binomial(scientificName: String?): String? {
        val tokens = normalize(scientificName)
        if (tokens.size < 2) return null
        return "${tokens[0]} ${tokens[1]}"
    }

    private fun genus(scientificName: String?): String? = normalize(scientificName).firstOrNull()

    private val PRIORITY = listOf(
        PlantCategory.SUCCULENT,
        PlantCategory.HERB,
        PlantCategory.ROOT,
        PlantCategory.LEAFY,
        PlantCategory.FRUITING,
        PlantCategory.FLOWERING,
    )

    private val BINOMIAL_OVERRIDES: Map<String, PlantCategory> = mapOf(
        "solanum tuberosum" to PlantCategory.ROOT,
        "allium schoenoprasum" to PlantCategory.HERB,
        "allium sativum" to PlantCategory.ROOT,
        "brassica oleracea" to PlantCategory.LEAFY,
        "beta vulgaris" to PlantCategory.ROOT,
        "ficus carica" to PlantCategory.FRUITING,
        "dracaena trifasciata" to PlantCategory.SUCCULENT,
        "kalanchoe blossfeldiana" to PlantCategory.SUCCULENT,
    )

    private val GENUS_MAP: Map<String, PlantCategory> = buildMap {
        putAll(
            listOf(
                "solanum", "lycopersicon", "capsicum", "cucumis", "cucurbita", "citrullus",
                "fragaria", "citrus", "physalis", "passiflora", "vitis", "musa", "coffea",
                "punica", "olea", "persea", "pisum", "phaseolus", "zea",
            ).associateWith { PlantCategory.FRUITING },
        )
        putAll(
            listOf(
                "daucus", "raphanus", "pastinaca", "allium", "zingiber", "curcuma", "manihot",
                "beta", "armoracia", "helianthus",
            ).associateWith { PlantCategory.ROOT },
        )
        putAll(
            listOf(
                "lactuca", "spinacia", "brassica", "eruca", "cichorium", "valerianella",
                "nasturtium", "rumex",
            ).associateWith { PlantCategory.LEAFY },
        )
        putAll(
            listOf(
                "ocimum", "mentha", "thymus", "rosmarinus", "salvia", "petroselinum",
                "anethum", "coriandrum", "origanum", "melissa", "lavandula", "artemisia",
                "satureja", "levisticum", "stevia",
            ).associateWith { PlantCategory.HERB },
        )
        putAll(
            listOf(
                "echeveria", "aloe", "haworthia", "haworthiopsis", "crassula", "sedum",
                "sempervivum", "kalanchoe", "agave", "gasteria", "euphorbia", "opuntia",
                "mammillaria", "gymnocalycium", "echinocactus", "echinopsis", "ferocactus",
                "cereus", "schlumbergera", "rhipsalis", "lithops", "portulacaria",
                "pachyphytum", "graptopetalum", "adenium", "pachypodium", "sansevieria",
                "aeonium", "senecio",
            ).associateWith { PlantCategory.SUCCULENT },
        )
        putAll(
            listOf(
                "phalaenopsis", "dendrobium", "cattleya", "cymbidium", "oncidium",
                "paphiopedilum", "saintpaulia", "streptocarpus", "anthurium", "spathiphyllum",
                "begonia", "pelargonium", "geranium", "rosa", "hibiscus", "gardenia",
                "jasminum", "camellia", "clivia", "hippeastrum", "cyclamen", "primula",
                "chrysanthemum", "gerbera", "rhododendron", "bougainvillea", "fuchsia",
                "impatiens", "hoya", "tulipa", "narcissus", "crocus", "lilium", "sinningia",
                "plumeria", "stephanotis",
            ).associateWith { PlantCategory.FLOWERING },
        )
        putAll(
            listOf(
                "monstera", "philodendron", "epipremnum", "scindapsus", "ficus", "dracaena",
                "chlorophytum", "calathea", "goeppertia", "maranta", "aglaonema",
                "dieffenbachia", "syngonium", "zamioculcas", "alocasia", "colocasia",
                "caladium", "peperomia", "pilea", "tradescantia", "nephrolepis", "asplenium",
                "adiantum", "pteris", "platycerium", "howea", "chamaedorea", "dypsis", "yucca",
                "schefflera", "fatsia", "hedera", "cissus", "aspidistra", "fittonia",
                "hypoestes", "ctenanthe", "stromanthe",
            ).associateWith { PlantCategory.FOLIAGE },
        )
    }

    private val KEYWORDS: Map<PlantCategory, List<String>> = mapOf(
        PlantCategory.SUCCULENT to listOf(
            "succulent", "cact", "cacti", "aloe", "jade", "eche", "hawor", "agave", "sedum",
            "stonecrop", "houseleek", "kalanchoe", "lithop",
            "суккулент", "кактус", "алоэ", "толстянк", "денежн", "эхевери", "хавор", "агав",
            "очиток", "молодил", "каланхоэ", "щучий", "тёщин", "тещин", "декабрист", "молочай",
            "литопс",
        ),
        PlantCategory.HERB to listOf(
            "basil", "mint", "thyme", "rosemary", "sage", "parsley", "dill", "cilantro",
            "coriander", "oregano", "marjoram", "balm", "lavender", "tarragon", "chive",
            "savory", "lovage", "stevia", "herb",
            "базилик", "мята", "мяты", "тимьян", "чабрец", "розмарин", "шалфей", "петрушк",
            "укроп", "кинза", "кориандр", "орегано", "майоран", "мелисс", "лаванд", "эстрагон",
            "тархун", "шнитт", "чабер", "зелень", "прян",
        ),
        PlantCategory.ROOT to listOf(
            "carrot", "beet", "radish", "potato", "turnip", "parsnip", "onion", "garlic",
            "shallot", "leek", "ginger", "turmeric", "celeriac", "rutabaga", "swede",
            "horseradish", "cassava", "tuber", "taro",
            "морков", "свёкл", "свекл", "редис", "редьк", "картоф", "репа", "пастернак",
            "чеснок", "порей", "имбир", "куркум", "брюкв", "хрен", "батат", "топинамбур",
            "клубнелук",
        ),
        PlantCategory.LEAFY to listOf(
            "lettuce", "spinach", "cabbage", "kale", "chard", "arugula", "rocket", "chicory",
            "endive", "choy", "choi", "mizuna", "watercress", "sorrel", "microgreen", "salad",
            "collard", "broccoli", "cauliflower",
            "салат", "шпинат", "капуст", "кейл", "мангольд", "руккол", "цикори", "эндиви",
            "кресс", "щавел", "микрозелен", "брокколи", "цветна",
        ),
        PlantCategory.FRUITING to listOf(
            "tomato", "pepper", "chili", "chilli", "cucumber", "eggplant", "aubergine",
            "zucchini", "courgette", "squash", "pumpkin", "melon", "watermelon", "strawberry",
            "bean", "peas", "corn", "maize", "citrus", "lemon", "lime", "orange", "mandarin",
            "tangerine", "grapefruit", "banana", "pomegranate", "avocado", "coffee", "grape",
            "passion",
            "томат", "помидор", "перец", "чили", "огур", "баклажан", "кабач", "цуккини", "тыкв",
            "дын", "арбуз", "клубник", "земляник", "фасол", "горох", "кукуруз", "цитрус",
            "лимон", "лайм", "апельсин", "мандарин", "грейпфрут", "банан", "инжир", "гранат",
            "авокадо", "кофе", "виноград", "маракуй",
        ),
        PlantCategory.FLOWERING to listOf(
            "orchid", "violet", "anthurium", "peace", "begonia", "geranium", "pelargonium",
            "rose", "hibiscus", "gardenia", "jasmine", "camellia", "clivia", "amaryllis",
            "cyclamen", "primrose", "chrysanthemum", "gerbera", "azalea", "bougainvillea",
            "fuchsia", "impatiens", "tulip", "daffodil", "crocus", "lily", "bloom", "flower",
            "орхиде", "фаленопсис", "фиалк", "сенполи", "антуриум", "спатифиллум", "бегони",
            "геран", "пеларгони", "роза", "розы", "гибискус", "гардени", "жасмин", "камели",
            "кливи", "амариллис", "гиппеаструм", "цикламен", "примул", "хризантем", "гербер",
            "азали", "бугенвилле", "фукси", "бальзамин", "хойя", "тюльпан", "нарцисс",
            "крокус", "лили", "цвету",
        ),
        // No FOLIAGE entry: it is the fallback [classify] returns when nothing
        // else matches, so a keyword list for it could never change an answer.
    )
}
