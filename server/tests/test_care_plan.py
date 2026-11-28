"""Care plan: the deterministic rules engine, the dosage guard and the LLM polish."""
import os

import pytest

from tests import module_state
from tests.llm_stub import mock_llm_post

# Make sure env-driven config doesn't accidentally enable the live API
# during unit tests (CI machines may have leaked secrets).
os.environ.pop("LLM_BASE_URL", None)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Give each test care_plan as it is straight after import."""
    import care_plan

    module_state.restore(care_plan)


def test_care_plan_polish_prompt_keeps_this_plans_numbers_last(tmp_path, monkeypatch):
    """The canonical schedule differs per plan. It used to be spliced into the
    MIDDLE of the instructions, which put a per-plan value ahead of every
    stable rule behind it — a guaranteed miss on a prompt that is otherwise
    identical for every care plan."""
    import ai_services as ai
    import care_plan

    captured = {}
    mock_llm_post(
        monkeypatch, finish_reason="stop", content="- bullet", captured=captured,
    )
    monkeypatch.setattr(ai, "LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setattr(care_plan, "_enforce_canonical_dosages", lambda text, plan: True)
    # A polished summary from an earlier test would be served from the disk
    # cache and this call would never reach the stub.
    monkeypatch.setattr(care_plan._CARE_PLAN_DISK, "dir", tmp_path / "care_plan_polish")
    care_plan._CARE_PLAN_CACHE._d.clear()
    care_plan._polish_plan_with_llm(
        {"watering_days": 7, "fertilise_weeks": 4, "fertiliser_npk": "10-15-10"}, "en",
    )
    system = captured["payload"]["messages"][0]["content"]
    assert system.startswith(ai._HOUSE_PREAMBLE)
    assert system.index("water every 7 days") > system.index(care_plan._CARE_PLAN_POLISH_TASK)
    assert system.rstrip().endswith("Write the summary in English.")


def test_care_plan_offline_has_all_fields():
    import care_plan

    plan = care_plan.generate_care_plan(
        disease="powdery_mildew", species="Begonia", light="bright_indirect",
    )
    # v1 contract — preserved.
    for key in (
        "species", "disease", "light_tier",
        "watering_days", "fertilise_weeks", "humidity_target",
        "key_action", "follow_up_days",
    ):
        assert key in plan, f"missing v1 care-plan key: {key}"
    # v2 additive fields — present even without an LLM key.
    for key in (
        "light_lux_target", "placement", "acclimation",
        "watering_method", "soil_mix", "fertiliser_npk",
        "humidity_band", "temperature_c",
        "treatment", "sanitation", "isolation", "red_flags",
        "checkpoints", "season", "summary_md",
    ):
        assert key in plan, f"missing v2 care-plan key: {key}"
    # The deterministic Markdown summary must be non-empty so clients
    # never render a blank card. The LLM polish, when configured,
    # replaces the string in place.
    assert isinstance(plan["summary_md"], str) and plan["summary_md"].strip()
    assert plan["treatment"], "treatment checklist must be non-empty for a disease"
    assert plan["isolation"] is True  # powdery mildew quarantines


def test_care_plan_polish_is_cached(tmp_path, monkeypatch):
    """An identical care-plan request must hit the LLM polish only
    once — the second call is served from the cache."""
    import ai_services as ai
    import care_plan

    monkeypatch.setattr(ai, "LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setattr(care_plan._CARE_PLAN_DISK, "dir", tmp_path / "care_plan_polish")
    care_plan._CARE_PLAN_CACHE._d.clear()

    calls = {"n": 0}

    def fake_call(system, blocks, max_tokens=None, require_complete=False,
                  reasoning_effort=None, session_id=None, cache_prefix=False):
        calls["n"] += 1
        return ("- Water every 5 days\n- Bright indirect light", 5, 10,
                {"cached_tokens": 0, "cache_write_tokens": 0})

    monkeypatch.setattr(ai, "_call_llm", fake_call)

    kwargs = dict(disease="powdery_mildew", species="Begonia",
                  light="bright_indirect", season="growing")
    first = care_plan.generate_care_plan(**kwargs)
    second = care_plan.generate_care_plan(**kwargs)

    assert calls["n"] == 1, "polish should be cached after the first call"
    assert first["summary_md"] == second["summary_md"]
    assert second.get("cached") is True


def test_care_plan_polish_prompt_echoes_canonical_numbers(tmp_path, monkeypatch):
    """The polish system prompt must restate the canonical schedule explicitly
    ("water every N days; fertilise every M weeks; NPK …"). Relying on the
    model to re-read the numbers out of the JSON blob made it drift ("every 10
    days" when the plan said 5), and every drift is rejected by the dosage
    guard — silently losing the polish."""
    import ai_services as ai
    import care_plan

    monkeypatch.setattr(ai, "LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setattr(care_plan._CARE_PLAN_DISK, "dir", tmp_path / "care_plan_polish")
    care_plan._CARE_PLAN_CACHE._d.clear()

    captured = {}

    def fake_call(system, blocks, max_tokens=None, require_complete=False,
                  reasoning_effort=None, session_id=None, cache_prefix=False):
        captured["system"] = system
        captured["reasoning_effort"] = reasoning_effort
        return ("- polished", 5, 10, {"cached_tokens": 0, "cache_write_tokens": 0})

    monkeypatch.setattr(ai, "_call_llm", fake_call)

    plan = care_plan.generate_care_plan(disease="leaf_spot", species="Monstera deliciosa",
                                 light="medium", season="growing")
    assert plan["summary_md"] == "- polished"
    system = captured["system"]
    assert f"water every {plan['watering_days']} days" in system
    assert f"fertilise every {plan['fertilise_weeks']} weeks" in system
    assert "verbatim" in system
    # Constraint adherence is what this call is judged on (the dosage guard
    # throws away any polish that drifts), so it runs at the medium floor.
    assert captured["reasoning_effort"] == "medium"
    # The rewrite must also carry the two fields the owner acts on. Asking only
    # for "watering, light, humidity, feeding, disease actions" made the polish
    # drop the follow-up and the escalation signs the deterministic summary and
    # the plan itself both carry.
    assert "checkpoints" in system
    assert "red_flags" in system


def test_care_plan_polish_rejection_is_counted(tmp_path, monkeypatch):
    """A polish that contradicts the canonical dosages is dropped silently —
    the user still gets the deterministic summary. Without a counter, a prompt
    or model regression that fails the guard on every call is indistinguishable
    from everything working, so /api/metrics tracks the reject ratio."""
    import ai_services as ai
    import care_plan

    monkeypatch.setattr(ai, "LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setattr(care_plan._CARE_PLAN_DISK, "dir", tmp_path / "care_plan_polish")
    care_plan._CARE_PLAN_CACHE._d.clear()
    for key in care_plan._care_plan_totals:
        care_plan._care_plan_totals[key] = 0

    def fake_call(system, blocks, max_tokens=None, require_complete=False,
                  reasoning_effort=None, session_id=None, cache_prefix=False):
        # Contradicts the canonical watering cadence for any plan.
        return ("- Water every 99 days", 5, 10,
                {"cached_tokens": 0, "cache_write_tokens": 0})

    monkeypatch.setattr(ai, "_call_llm", fake_call)

    plan = care_plan.generate_care_plan(disease="leaf_spot", species="Ficus",
                                 light="medium", season="growing")
    assert "99" not in plan["summary_md"], "rejected polish must not reach the user"
    snap = care_plan.care_plan_metrics_snapshot()
    assert snap["attempts"] == 1
    assert snap["rejected"] == 1
    assert snap["accepted"] == 0
    assert snap["reject_ratio"] == 1.0


def test_care_plan_unknown_disease_falls_through():
    import care_plan

    # An unknown disease string should not raise — care-plan returns a
    # neutral monitoring plan instead.
    plan = care_plan.generate_care_plan(disease="unknown_disease_x", light="low_light")
    assert "watering_days" in plan
    assert plan["disease"] == "unknown_disease_x"
    assert plan["isolation"] is False  # default protocol = "healthy"-ish


def test_care_plan_season_modifier_stretches_dormant_watering():
    """Dormant months stretch the watering interval and pause feeding."""
    import datetime as dt

    import care_plan

    growing = care_plan.generate_care_plan(
        disease="healthy", light="bright_indirect", today=dt.date(2025, 6, 15),
    )
    dormant = care_plan.generate_care_plan(
        disease="healthy", light="bright_indirect", today=dt.date(2025, 1, 15),
    )
    assert growing["season"] == "growing"
    assert dormant["season"] == "dormant"
    # Dormant interval is at least 7 days and longer than the growing
    # interval.
    assert isinstance(dormant["watering_days"], int)
    assert dormant["watering_days"] >= growing["watering_days"]
    # Dormant fertilising is half-cadence or more (always longer interval).
    assert dormant["fertilise_weeks"] >= growing["fertilise_weeks"]


def test_care_plan_disease_protocol_carries_treatment_and_red_flags():
    """Disease classes must surface a non-empty treatment + red-flags list."""
    import care_plan

    for d in (
        "powdery_mildew",
        "gray_mold_botrytis",
        "leaf_spot",
        "viral_disease",
    ):
        plan = care_plan.generate_care_plan(disease=d, light="bright_indirect")
        assert plan["treatment"], f"empty treatment list for {d}"
        assert plan["red_flags"], f"empty red_flags list for {d}"
        assert plan["isolation"] is True, f"{d} should isolate"


def test_care_plan_unknown_light_falls_back():
    """An invalid light tier must be normalised to ``bright_indirect``."""
    import care_plan

    plan = care_plan.generate_care_plan(disease="healthy", light="ultraviolet")
    assert plan["light_tier"] == "bright_indirect"


def test_care_plan_curated_disease_carries_products_with_active_ingredient():
    """A class with a curated catalogue entry gets a products list, and
    every product names its active ingredient — the load-bearing field,
    since brand names differ by market."""
    import care_plan

    plan = care_plan.generate_care_plan(disease="gray_mold_botrytis", light="bright_indirect")
    assert plan["products"], "gray_mold_botrytis should carry curated products"
    for product in plan["products"]:
        assert product["active_ingredient"], f"product {product['key']} has no active ingredient"
        assert product["name"]
        assert product["kind"]
    assert plan["treatment_conditions"]["temp_c"]
    assert plan["treatment_conditions"]["best_time"]


def test_care_plan_uncurated_disease_omits_products_key_entirely():
    """A class outside the curated catalogue (no chemical intervention,
    or a cure-less class) must NOT carry a "products" key at all — not
    an empty list, which would read as "checked, found nothing"."""
    import care_plan

    for disease in ("healthy", "viral_disease", "leaf_scorch_underwatering", "not_a_plant"):
        plan = care_plan.generate_care_plan(disease=disease, light="bright_indirect")
        assert "products" not in plan, f"{disease} should have no products key"
        assert "treatment_conditions" not in plan, f"{disease} should have no treatment_conditions key"


def test_care_plan_products_localise_to_russian():
    """RU locale must return Russian product names/ingredients/notes and
    a Russian best-time string, not leak the English catalogue text."""
    import care_plan

    plan = care_plan.generate_care_plan(disease="powdery_mildew", light="bright_indirect", locale="ru")
    assert plan["products"], "powdery_mildew should carry curated products"
    for product in plan["products"]:
        assert any("а" <= ch <= "я" or ch in "ёЁ" for ch in product["active_ingredient"].lower()), (
            f"active_ingredient for {product['key']} doesn't look Russian: {product['active_ingredient']!r}"
        )
    assert any("а" <= ch <= "я" for ch in plan["treatment_conditions"]["best_time"].lower())


def test_care_plan_ru_localises_fertiliser_npk_from_light_tier():
    """For diseases that don't override fertiliser_npk (powdery_mildew,
    healthy, sunburn, ...) the English string falls through to the
    light-tier base. The RU plan must follow the same fall-through —
    a Russian call must NOT leak the English "balanced 3-1-2 at full
    strength" sentinel.
    """
    import care_plan

    for d in ("powdery_mildew", "healthy", "sunburn_or_scorch"):
        plan = care_plan.generate_care_plan(
            disease=d, light="bright_indirect", locale="ru",
        )
        fert = plan["fertiliser_npk"]
        assert isinstance(fert, str) and fert.strip()
        assert "balanced" not in fert.lower(), (
            f"{d}: fertiliser_npk leaked English fallback: {fert!r}"
        )


def test_care_plan_ru_localises_fertiliser_npk_from_disease_override():
    """nutrient_deficiency overrides fertiliser_npk on the disease
    side. The Russian plan must surface that override's RU copy, not
    the base-tier RU string nor the English original.
    """
    import care_plan

    plan = care_plan.generate_care_plan(
        disease="nutrient_deficiency", light="bright_indirect", locale="ru",
    )
    assert "хелат" in plan["fertiliser_npk"].lower()


def test_care_plan_ru_unknown_disease_falls_back_to_healthy_strings():
    """Unknown disease keys (e.g. the ``"unknown"`` value the endpoint
    normalises to) must surface RUSSIAN text in RU mode. The English
    code path falls back to the "healthy" protocol; the RU lookup
    must follow the same fallback rather than re-emitting the
    English defaults.
    """
    import care_plan

    plan = care_plan.generate_care_plan(
        disease="unknown", light="bright_indirect", locale="ru",
    )
    for field in ("key_action",):
        value = plan[field]
        assert isinstance(value, str) and value.strip()
        # The English healthy strings contain "current routine" /
        # "monitor weekly" — easy markers that the RU fallback failed.
        assert "current routine" not in value.lower(), (
            f"{field}: RU plan leaked English fallback: {value!r}"
        )
        assert "monitor weekly" not in value.lower(), (
            f"{field}: RU plan leaked English fallback: {value!r}"
        )
    for step in plan["treatment"]:
        assert "continue the current" not in step.lower(), (
            f"treatment step leaked English fallback: {step!r}"
        )
    # And conversely the English path is unaffected.
    plan_en = care_plan.generate_care_plan(
        disease="unknown", light="bright_indirect", locale="en",
    )
    assert "monitor" in plan_en["key_action"].lower()


def test_humidity_habit_resists_common_substring_traps():
    """Whole-word matching only. "Asparagus fern" is Liliaceae —
    must NOT trip the fern bucket. "Aloeswood Saraca" is agarwood —
    must NOT trip the succulent bucket. Real Aloe vera / Boston
    fern must still classify correctly.
    """
    import care_plan

    # False-positive guards (the reported bugs).
    assert care_plan._humidity_habit("Asparagus fern") is None
    assert care_plan._humidity_habit("Asparagus aethiopicus") is None
    assert care_plan._humidity_habit("Aloeswood Saraca") is None
    assert care_plan._humidity_habit("Saraca asoca") is None
    # True positives still classify.
    assert care_plan._humidity_habit("Aloe vera") == "succulent"
    assert care_plan._humidity_habit("Cactus") == "succulent"
    assert care_plan._humidity_habit("Nephrolepis exaltata") == "fern_tropical"
    assert care_plan._humidity_habit("Boston Fern") is None  # common name only
    assert care_plan._humidity_habit("Calathea orbifolia") == "fern_tropical"
    # Unknown / empty inputs.
    assert care_plan._humidity_habit("") is None
    assert care_plan._humidity_habit(None) is None
    assert care_plan._humidity_habit("Ficus elastica") is None


def test_care_plan_humidity_band_not_perturbed_by_false_positives():
    """Verify the bug fix end-to-end: the humidity band on a plan
    for "Asparagus fern" must match the base light-tier band — not
    the fern bucket's 60-80 % range that the old substring match
    used to push it into.
    """
    import care_plan

    base_plan = care_plan.generate_care_plan(
        disease="healthy", light="bright_indirect",
    )
    asparagus_plan = care_plan.generate_care_plan(
        disease="healthy", species="Asparagus fern", light="bright_indirect",
    )
    aloeswood_plan = care_plan.generate_care_plan(
        disease="healthy", species="Aloeswood Saraca", light="bright_indirect",
    )
    assert asparagus_plan["humidity_band"] == base_plan["humidity_band"]
    assert aloeswood_plan["humidity_band"] == base_plan["humidity_band"]


def test_enforce_canonical_dosages_accepts_template_incidental_numbers():
    """A faithful summary must not be rejected by incidental, non-interval
    numbers that merely sit near a watering keyword (e.g. the "2 cm" in the
    deterministic template "Water every 7 days; let the top 2 cm dry…")."""
    import care_plan

    plan = {"watering_days": 7, "fertilise_weeks": 2, "fertiliser_npk": ""}
    text = "Water every 7 days; let the top 2 cm dry between waterings."
    assert care_plan._enforce_canonical_dosages(text, plan) is True


def test_enforce_canonical_dosages_rejects_wrong_watering_interval():
    import care_plan

    plan = {"watering_days": 7, "fertilise_weeks": 2, "fertiliser_npk": ""}
    # Claims a 5-day cadence bound to "days"; canonical is 7.
    text = "Water every 5 days; keep the soil lightly moist."
    assert care_plan._enforce_canonical_dosages(text, plan) is False


def test_enforce_canonical_dosages_rejects_wrong_fertilise_interval():
    import care_plan

    plan = {"watering_days": 7, "fertilise_weeks": 2, "fertiliser_npk": ""}
    text = "Fertilise every 4 weeks with a balanced blend."
    assert care_plan._enforce_canonical_dosages(text, plan) is False


def test_enforce_canonical_dosages_rejects_npk_swap():
    import care_plan

    plan = {"watering_days": 7, "fertilise_weeks": 2, "fertiliser_npk": "10-15-10"}
    text = "Use a 20-20-20 fertiliser blend."
    assert care_plan._enforce_canonical_dosages(text, plan) is False


# ── Care-protocol coverage ────────────────────────────────────────────
# Regression guard for the bug where ``pests`` — a shipped head class —
# had no entry in either protocol table, so a pests care plan silently
# fell through to the *healthy* protocol ("keep your current routine;
# monitor weekly") instead of an isolate-and-treat one. The head is the
# contract: every class the classifier can return (bar ``not_a_plant``,
# which never reaches a care plan) needs a protocol, in both languages.


def test_every_head_class_has_a_care_protocol():
    import care_plan_content
    try:
        from classifier import DEFAULT_CLASS_NAMES
    except ImportError:  # pragma: no cover — package layout
        from server.classifier import DEFAULT_CLASS_NAMES

    expected = set(DEFAULT_CLASS_NAMES) - {"not_a_plant"}
    missing = expected - set(care_plan_content._DISEASE_PROTOCOL)
    assert not missing, f"head classes with no care protocol: {sorted(missing)}"


def test_care_protocol_tables_agree_across_languages():
    import care_plan_content

    assert set(care_plan_content._DISEASE_PROTOCOL) == set(care_plan_content._DISEASE_PROTOCOL_RU), (
        "EN/RU care protocols must cover the same classes: "
        f"EN-only={sorted(set(care_plan_content._DISEASE_PROTOCOL) - set(care_plan_content._DISEASE_PROTOCOL_RU))}, "
        f"RU-only={sorted(set(care_plan_content._DISEASE_PROTOCOL_RU) - set(care_plan_content._DISEASE_PROTOCOL))}"
    )
