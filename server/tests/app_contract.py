"""What the Android app requires of each JSON response it decodes.

The app decodes app-API responses with kotlinx.serialization
(``ignoreUnknownKeys = true``, ``coerceInputValues = true``, explicit nulls
on). For each ``@Serializable`` data class in the app's model files that means:

* a non-null field with no default must be present and non-null;
* a nullable field with no default must be present (null is fine);
* a field with a default may be absent, and an explicit ``null`` falls back to
  the default;
* a present, non-null value must have a type the field can decode.

Extra keys are ignored. The rules are read from the Kotlin sources at test
time, so a field the app starts to require is checked by the next server run.
``conftest`` applies :func:`violations` to every 2xx JSON response a test gets
from a route in :data:`ROUTES`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

MODEL_FILES = (
    Path(__file__).resolve().parents[2]
    / "android/app/src/main/java/com/plantdiseases/app/data/model"
).glob("*.kt")

# (method, route path template) -> the Kotlin class the app decodes it into.
ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/capabilities"): "CapabilitiesResponse",
    ("GET", "/api/aqua/manifest"): "AquaManifestResponse",
    ("POST", "/api/analyze"): "AnalysisResponse",
    ("POST", "/api/identify"): "IdentifyResponse",
    ("POST", "/api/care-plan"): "CarePlanResponse",
    ("POST", "/api/symptom-diagnose"): "SymptomDiagnoseResponse",
    ("POST", "/api/chat"): "ChatResponse",
    ("POST", "/api/progression"): "ProgressionResponse",
    ("POST", "/api/feedback"): "FeedbackResponse",
    ("GET", "/api/species-info"): "SpeciesInfoResponse",
    ("GET", "/api/species-diseases"): "SpeciesDiseasesResponse",
    ("GET", "/api/species-profile"): "SpeciesProfileResponse",
    ("GET", "/api/popular"): "PopularResponse",
    ("GET", "/api/community/nearby"): "CommunityScansResponse",
    ("GET", "/api/community/map"): "CommunityScansResponse",
    ("GET", "/api/community/by-species"): "CommunityScansResponse",
    ("GET", "/api/community/heatmap"): "CommunityHeatmapResponse",
    ("GET", "/api/community/safety/blocks"): "CommunityBlocksResponse",
    ("POST", "/api/community/scan"): "CommunityScanUploadResponse",
    ("GET", "/api/community/spaces"): "ForumSpacesResponse",
    ("GET", "/api/community/feed"): "ForumPage",
    ("GET", "/api/community/search"): "ForumPage",
    ("GET", "/api/community/posts/{post_id}"): "ForumPost",
    ("POST", "/api/community/posts"): "ForumPost",
    ("PATCH", "/api/community/posts/{post_id}"): "ForumPost",
    ("POST", "/api/community/posts/{post_id}/like"): "ForumLikeResponse",
    ("POST", "/api/community/posts/{post_id}/answers"): "ForumAnswer",
    ("POST", "/api/community/answers/{answer_id}/reaction"): "ForumReactionResponse",
    ("GET", "/api/community/helpers/{author_token}"): "HelperProfile",
    ("GET", "/api/community/me/achievements"): "HelperProfile",
    ("GET", "/api/community/activity"): "ForumActivityResponse",
    ("POST", "/api/community/media"): "ForumMediaUpload",
    ("PUT", "/api/community/me/avatar"): "ForumAvatarUpload",
    ("GET", "/api/community/admin/me"): "StaffMe",
    ("GET", "/api/community/admin/queue"): "ModQueue",
    ("POST", "/api/community/admin/posts/{post_id}/action"): "ModActionResult",
    ("POST", "/api/community/admin/answers/{answer_id}/action"): "ModActionResult",
    ("POST", "/api/community/admin/actors/{author_token}/sanction"): "ModActionResult",
    ("GET", "/api/community/moderation/notices"): "ModNotices",
}

_ROUTE_PATTERNS = [
    (method, re.compile("^" + re.sub(r"\\{[^}]+\\}", "[^/]+", re.escape(path)) + "$"), class_name)
    for (method, path), class_name in ROUTES.items()
]


def class_for(method: str, path: str) -> str | None:
    """The app model a request to ``path`` decodes into, or None."""
    for route_method, pattern, class_name in _ROUTE_PATTERNS:
        if route_method == method.upper() and pattern.match(path):
            return class_name
    return None


ANY = "*"  # a field with a custom serializer: no type check


@dataclass(frozen=True)
class Field:
    json_name: str
    kotlin_type: str  # without the trailing '?'
    nullable: bool
    has_default: bool


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    parts, depth, current, quote = [], 0, [], False
    for ch in text:
        if ch == '"':
            quote = not quote
        elif not quote and ch in "(<[{":
            depth += 1
        elif not quote and ch in ")>]}":
            depth -= 1
        if ch == sep and depth == 0 and not quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//[^\n\"]*$", "", source)


_CLASS = re.compile(r"@Serializable\s+(?:@\w+(?:\([^)]*\))?\s+)*data class (\w+)\s*\(")
_PARAM = re.compile(r"(?:val|var)\s+(\w+)\s*:\s*(.+)$", re.S)


def _parse_params(body: str) -> list[Field]:
    fields = []
    for raw in _split_top_level(body):
        param = raw.strip()
        if not param:
            continue
        serial = re.search(r'@SerialName\("([^"]+)"\)', param)
        custom = "@Serializable(with" in param
        decl = re.sub(r"@\w+(?:\.\w+)*(?:\((?:[^()]|\([^)]*\))*\))?", "", param).strip()
        match = _PARAM.search(decl)
        if not match:
            continue
        name, rest = match.group(1), match.group(2)
        type_and_default = _split_top_level(rest, "=")
        kotlin_type = type_and_default[0].strip()
        nullable = kotlin_type.endswith("?")
        fields.append(
            Field(
                json_name=serial.group(1) if serial else name,
                kotlin_type=ANY if custom else kotlin_type.rstrip("?").strip(),
                nullable=nullable,
                has_default=len(type_and_default) > 1,
            )
        )
    return fields


@cache
def classes() -> dict[str, list[Field]]:
    """Every ``@Serializable`` data class in the app's model files."""
    found: dict[str, list[Field]] = {}
    for path in MODEL_FILES:
        source = _strip_comments(path.read_text(encoding="utf-8"))
        for match in _CLASS.finditer(source):
            start = match.end()
            depth, i = 1, start
            while depth:
                depth += {"(": 1, ")": -1}.get(source[i], 0)
                i += 1
            found[match.group(1)] = _parse_params(source[start : i - 1])
    return found


_PRIMITIVES = {
    "String": (str,),
    "Int": (int,),
    "Long": (int,),
    "Float": (int, float),
    "Double": (int, float),
    "Boolean": (bool,),
}


def _type_violations(value: object, kotlin_type: str, where: str) -> list[str]:
    if kotlin_type == ANY or value is None:
        return []
    kotlin_type = kotlin_type.rstrip("?").strip()
    if kotlin_type in _PRIMITIVES:
        ok = isinstance(value, _PRIMITIVES[kotlin_type])
        if kotlin_type != "Boolean" and isinstance(value, bool):
            ok = False
        return [] if ok else [f"{where}: {value!r} is not a {kotlin_type}"]
    generic = re.fullmatch(r"(\w+)<(.+)>", kotlin_type)
    if generic and generic.group(1) in {"List", "Set"}:
        if not isinstance(value, list):
            return [f"{where}: expected a list"]
        problems = []
        for index, item in enumerate(value):
            problems += _type_violations(item, generic.group(2), f"{where}[{index}]")
        return problems
    if generic and generic.group(1) == "Map":
        if not isinstance(value, dict):
            return [f"{where}: expected an object"]
        value_type = _split_top_level(generic.group(2))[1]
        problems = []
        for key, item in value.items():
            problems += _type_violations(item, value_type, f"{where}.{key}")
        return problems
    if kotlin_type in classes():
        return violations(value, kotlin_type, where)
    return []  # an enum or a type this checker does not model


def violations(payload: object, class_name: str, where: str = "$") -> list[str]:
    """Why ``payload`` would fail to decode into ``class_name``; empty if it decodes."""
    if not isinstance(payload, dict):
        return [f"{where}: expected an object for {class_name}"]
    problems = []
    for field in classes()[class_name]:
        path = f"{where}.{field.json_name}"
        if field.json_name not in payload:
            if not field.has_default:
                problems.append(f"{path}: missing (required by {class_name})")
            continue
        value = payload[field.json_name]
        if value is None:
            if not field.nullable and not field.has_default:
                problems.append(f"{path}: null (required by {class_name})")
            continue
        problems += _type_violations(value, field.kotlin_type, path)
    return problems
