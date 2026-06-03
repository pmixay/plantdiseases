"""Weather-aware care plan tests — pure-function only, no network.

``fetch_weather`` guard paths and the ``_weather_care_note`` condition
buckets are deterministic, so we can assert them without hitting Open-Meteo.
"""
from __future__ import annotations

import ai_services as ai
import weather


def test_fetch_weather_requires_both_coordinates():
    assert weather.fetch_weather(None, 10.0) is None
    assert weather.fetch_weather(10.0, None) is None
    assert weather.fetch_weather(None, None) is None


def test_fetch_weather_rejects_out_of_range():
    assert weather.fetch_weather(91.0, 0.0) is None
    assert weather.fetch_weather(0.0, 181.0) is None
    assert weather.fetch_weather("not-a-number", 0.0) is None


def test_weather_note_buckets_en():
    # Rain is checked first — a wet forecast wins even on a warm day.
    rain = ai._weather_care_note(
        {"temp_c": 18, "humidity": 70, "precip_next": 6.0}, "en"
    )
    assert "Rain" in rain and "18" in rain

    hot_dry = ai._weather_care_note(
        {"temp_c": 30, "humidity": 30, "precip_next": 0.0}, "en"
    )
    assert "Hot" in hot_dry and "30" in hot_dry

    humid = ai._weather_care_note(
        {"temp_c": 22, "humidity": 85, "precip_next": 0.0}, "en"
    )
    assert "Humid" in humid

    cold = ai._weather_care_note(
        {"temp_c": 2, "humidity": 50, "precip_next": 0.0}, "en"
    )
    assert "Cold" in cold

    mild = ai._weather_care_note(
        {"temp_c": 21, "humidity": 55, "precip_next": 0.0}, "en"
    )
    assert "Mild" in mild


def test_weather_note_localised_ru():
    note = ai._weather_care_note(
        {"temp_c": 21, "humidity": 55, "precip_next": 0.0}, "ru"
    )
    assert note is not None
    assert "погода" in note.lower()


def test_weather_note_none_on_missing_fields():
    assert ai._weather_care_note({}, "en") is None
    assert ai._weather_care_note({"temp_c": 20}, "en") is None
