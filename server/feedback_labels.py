"""The /api/feedback label taxonomy, shared by the feedback, community and forum routes."""

# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import classifier
except ImportError:
    import classifier


# Classes the production head can actually predict. Tracks the
# ``class_names`` declaration in ``models/classes.json`` via
# ``classifier.DEFAULT_CLASS_NAMES`` so we only have one source of
# truth — feedback's ``out_of_head`` flag falls out the moment a
# correction lands outside this set.
HEAD_CLASS_NAMES = classifier.DEFAULT_CLASS_NAMES

FEEDBACK_IN_HEAD_CLASSES: frozenset[str] = frozenset(HEAD_CLASS_NAMES)

# Out-of-head corrections — physiological classes the
# v2.2 head doesn't carry yet, plus a structured catch-all key. We accept
# them on /api/feedback (otherwise the user has no way to tell us
# "your model can't say this") and tag each row with ``out_of_head=1``
# so retraining can prioritise expanding the label set.
# ``leaf_scorch_underwatering`` and ``nutrient_deficiency`` moved
# into the head set (train_notebook.ipynb §1) and now arrive via
# ``FEEDBACK_IN_HEAD_CLASSES`` instead.
FEEDBACK_OUT_OF_HEAD_CLASSES: frozenset[str] = frozenset({
    "edema",
    "sunburn_or_scorch",
    "other",
    # "The reject was wrong — there IS a plant here." Sent by the app's
    # single-action feedback on a not_a_plant / blur verdict; these rows
    # (plus their uploaded images) are exactly what the rejection head
    # needs for retraining.
    "is_a_plant",
})

FEEDBACK_ALLOWED_CORRECTIONS: frozenset[str] = (
    FEEDBACK_IN_HEAD_CLASSES | FEEDBACK_OUT_OF_HEAD_CLASSES
)

FEEDBACK_ALLOWED_REASONS: frozenset[str] = frozenset({
    "wrong_class",
    "low_quality_image",
    "occluded",
    "multiple_plants",
    "ambiguous_symptoms",
    "other",
})

# Opaque server capabilities use a URL-safe alphabet. Community still names
# its publication capability ``scan_id`` for API compatibility; feedback uses
# a separate receipt that is never accepted by community endpoints.
SCAN_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


