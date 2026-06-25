"""Curated region -> popular-houseplant lists.

Plain data module, no I/O. The ``/api/popular`` endpoint resolves a lead
image per plant (via ``species_info``) and caches the bundle; this file only
decides *which* plants to show for a given country.

``GLOBAL_DEFAULT`` mirrors the app's bundled ``SpeciesCatalog.popular`` so the
two never drift. ``BY_REGION`` is keyed by ISO-3166 alpha-2 country code
(uppercase); unmapped countries fall back to the global list.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PopularPlant:
    scientific: str
    common_en: str
    common_ru: str


# Mirrors android SpeciesCatalog.popular (same scientific names so Wikipedia
# resolves to the same lead photos).
GLOBAL_DEFAULT: list[PopularPlant] = [
    PopularPlant("Monstera deliciosa", "Swiss cheese plant", "Монстера"),
    PopularPlant("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
    PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
    PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
    PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
    PopularPlant("Ficus lyrata", "Fiddle-leaf fig", "Фикус лировидный"),
    PopularPlant("Ficus elastica", "Rubber plant", "Фикус каучуконосный"),
    PopularPlant("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
    PopularPlant("Aloe vera", "Aloe vera", "Алоэ вера"),
    PopularPlant("Dracaena marginata", "Dragon tree", "Драцена окаймлённая"),
]

# A handful of countries with a locally-skewed ordering / selection. Curated,
# not exhaustive — anything unmapped falls back to GLOBAL_DEFAULT.
BY_REGION: dict[str, list[PopularPlant]] = {
    "RU": [
        PopularPlant("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
        PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
        PopularPlant("Ficus benjamina", "Weeping fig", "Фикус Бенджамина"),
        PopularPlant("Saintpaulia ionantha", "African violet", "Сенполия (фиалка)"),
        PopularPlant("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
        PopularPlant("Aloe vera", "Aloe vera", "Алоэ вера"),
    ],
    "US": [
        PopularPlant("Monstera deliciosa", "Swiss cheese plant", "Монстера"),
        PopularPlant("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
        PopularPlant("Ficus lyrata", "Fiddle-leaf fig", "Фикус лировидный"),
        PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
        PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularPlant("Pilea peperomioides", "Chinese money plant", "Пилея пеперомиевидная"),
        PopularPlant("Sansevieria cylindrica", "Cylindrical snake plant", "Сансевиерия цилиндрическая"),
    ],
    "IN": [
        PopularPlant("Ocimum tenuiflorum", "Holy basil (Tulsi)", "Базилик священный (туласи)"),
        PopularPlant("Epipremnum aureum", "Money plant", "Эпипремнум (потос)"),
        PopularPlant("Aloe vera", "Aloe vera", "Алоэ вера"),
        PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularPlant("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
        PopularPlant("Ficus elastica", "Rubber plant", "Фикус каучуконосный"),
        PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
    ],
    "BR": [
        PopularPlant("Monstera deliciosa", "Swiss cheese plant", "Монстера"),
        PopularPlant("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
        PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularPlant("Philodendron hederaceum", "Heartleaf philodendron", "Филодендрон лазящий"),
        PopularPlant("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
        PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
        PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularPlant("Aloe vera", "Aloe vera", "Алоэ вера"),
    ],
    "GB": [
        PopularPlant("Monstera deliciosa", "Swiss cheese plant", "Монстера"),
        PopularPlant("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
        PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularPlant("Ficus lyrata", "Fiddle-leaf fig", "Фикус лировидный"),
        PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
        PopularPlant("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
        PopularPlant("Aloe vera", "Aloe vera", "Алоэ вера"),
    ],
    "CN": [
        PopularPlant("Epipremnum aureum", "Golden pothos", "Эпипремнум (потос)"),
        PopularPlant("Pilea peperomioides", "Chinese money plant", "Пилея пеперомиевидная"),
        PopularPlant("Zamioculcas zamiifolia", "ZZ plant", "Замиокулькас"),
        PopularPlant("Dracaena trifasciata", "Snake plant", "Сансевиерия"),
        PopularPlant("Spathiphyllum wallisii", "Peace lily", "Спатифиллум"),
        PopularPlant("Chlorophytum comosum", "Spider plant", "Хлорофитум"),
        PopularPlant("Aloe vera", "Aloe vera", "Алоэ вера"),
        PopularPlant("Ficus microcarpa", "Ginseng ficus", "Фикус микрокарпа"),
    ],
}


def popular_for(country: str | None, region: str | None = None) -> list[PopularPlant]:
    """Curated popular plants for a country, else the global default.

    ``country`` may be an ISO alpha-2 code or a country name / "City, Country"
    composite — only the leading 2 letters of an ISO code are matched, so a
    full name simply misses the map and falls through to global. ``region`` is
    accepted for signature stability but unused (country granularity suffices).
    """
    code = (country or "").strip().upper()
    if len(code) == 2 and code in BY_REGION:
        return BY_REGION[code]
    return GLOBAL_DEFAULT
