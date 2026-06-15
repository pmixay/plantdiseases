# User-feedback & scan labeling — the admin review loop

**Scope.** This documents how the server turns **production scans** and **user
feedback** into a human-reviewable queue that an admin processes from the app.
The previous revision also described an offline-collection → Kaggle dataset →
server-ingestion → export loop; **that dataset side has been removed.** The
server no longer ingests a Kaggle supplement and no longer builds export
bundles — it only records user-sourced candidates and serves them to the admin
labeling tool. Model/dataset context lives in `docs/ROADMAP.md`.

## The loop, end to end

```
   (1) PRODUCTION SCANS  /api/analyze → anonymized recording (BackgroundTasks)
            route_scan(): confident → auto_yes, suspicious → pending
                         ▼
   (2) USER FEEDBACK  thumbs-down re-opens the candidate as top-priority pending
                         ▼
   (3) HUMAN LABELING  /api/label/* — 2-source queue (feedback → scans),
            admin verdicts yes / no / not_sure from the Android app
                         ▼
   (4) VERIFIED ROWS stay in labeling.db (auditable; GDPR-swept by scan_id)
```

## (1) Production scan recording — anonymized

Every `/api/analyze` result except `not_a_plant` (which also covers the blur
gate's synthetic NAP) is recorded AFTER the response is sent (BackgroundTasks;
zero hot-path latency), subject to consent + caps:

- **Anonymization by construction:** the decoded image is re-encoded
  (JPEG q88, no EXIF — GPS/metadata dropped), downscaled to ≤1024 px. No
  user id, IP, or filename is ever passed in. The ONLY linkage is the
  response `scan_id`; the user↔scan_id join lives solely in `feedback.db`
  and is swept by the GDPR delete cascade.
- **Routing:** `route_scan()` over the already-computed result — singleton
  conformal set == top-1 ⇒ `auto_yes`; otherwise `pending` (suspicious →
  human queue). `auto_no` is impossible for scans (the model's own
  prediction is the proposed class). Trust knobs: `labeling_trust_conformal`,
  `labeling_conf_high`.
- **Class-balanced caps:** suspicious scans and weak-class (`labeling_targets`
  target>0) scans are always kept; confident common-class scans are sampled at
  `scan_common_keep_rate` (0.1) decided deterministically by SHA; a per-class
  daily cap (`scan_daily_cap_per_class`) + global `scan_max_images` bound disk.
  Here `labeling_targets` only marks which classes are weak — it no longer
  gates a dataset queue (there isn't one).
- **Retention / prune order (protects hard cases):** unreviewed `pending`
  scans die after `scan_retention_days` (90); over the global cap, redundant
  `auto_yes` common-class scans are pruned first, then weak-class `auto_yes`;
  `user_feedback` rows and human-verdict rows are NEVER pruned.
- **Consent:** disclosed via the blocking first-launch consent page
  (Android onboarding; upgrade installs get a one-time blocking dialog) and
  `website/privacy.html`.

## (2) Feedback → the labeling queue

- Negative feedback re-opens the recorded candidate (even an `auto_yes` one)
  as `pending` under `source='user_feedback'`, retargeted at the user's
  corrected class, carrying their note — the canonical false-positive
  signal, served at top queue priority. It never overrides a human verdict.
- Positive feedback stamps `user_correct=1` and promotes a still-`pending`
  scan to `auto_yes` when the user confirms the model's own prediction.
- If the scan was never recorded (old install / recording off / disputed
  NAP), the negative-feedback image upload builds the candidate from the
  uploaded bytes via the same anonymize helper. **Known gap:** negative
  feedback with no image and no recorded scan has no pixels — nothing can be
  enqueued.
- **GDPR:** `/api/community/user/{id}` delete cascades community rows,
  feedback rows AND labeling candidates keyed by that user's scan_ids.
  Plain `user_scan` rows are unreachable by design (no user linkage exists).

## (3) Human labeling — 2 prioritised sources

`/api/label/next` serves `pending` cards in priority order **feedback →
scans** (filterable via `?source=feedback|scans`). Feedback cards show the
model's verdict + the user's note so the admin sees "user proposed X / model
said Y". `/api/label/stats` adds a per-source block (`feedback` / `scans`).
Verdicts (`yes` / `no` / `not_sure`) are recorded via `/api/label/verdict`
under an atomic claim/lease so concurrent admins never collide; abandoned
claims requeue after the lease TTL.

Auth is a single shared admin password (set `labeling_admin_password` in
`config.json`; **blank refuses every login**, so the feature is safe to leave
enabled) exchanged for a stateless HMAC-signed session token.

## (4) Verified rows

Human verdicts (`yes` / `no` / `not_sure`) and `auto_yes` rows persist in
`labeling.db` and are never auto-rerouted or pruned. They remain available for
ad-hoc inspection / a future export path; the server itself no longer produces
Kaggle bundles.

## Configuration (`server/config.json`)

| Key | Default | Purpose |
|---|---|---|
| `labeling_enabled` | `true` | Master switch for the admin tool + scan recording wiring. Blank `labeling_admin_password` already refuses all logins. |
| `labeling_admin_password` | *(empty)* | Shared admin password. Blank ⇒ no access. |
| `labeling_session_secret` | *(empty)* | HMAC key for session tokens (random per-process if blank). |
| `labeling_trust_conformal` / `labeling_conf_high` | `true` / `0.85` | `route_scan` trust knobs (conformal-set primary, calibrated-confidence fallback). |
| `labeling_targets` | weak-class map | `target>0` marks weak classes whose scans are always retained + prioritised. Not a collection goal. |
| `scan_recording_enabled` | `true` | Record anonymized scans for review. |
| `scan_retention_days` / `scan_max_images` | `90` / `20000` | Retention + global cap. |
| `scan_common_keep_rate` / `scan_daily_cap_per_class` | `0.1` / `50` | Class-balanced sampling + daily cap. |

## Verification

- Server: `ruff check .` + `pytest tests -q` (routing, recording, feedback
  wiring, the 2-source queue, claim/verdict/stats — see
  `tests/test_scan_recording.py`, `tests/test_feedback_labeling_wiring.py`,
  `tests/test_label_sources.py`, `tests/test_labeling.py`).
- Android: `./gradlew lintDebug testDebugUnitTest assembleRelease`; manual —
  fresh install ends onboarding on the consent page (Skip jumps to it, Agree
  required); the labeling screen's source chips show **Feedback / Scans** only.
- Live: scan → row in `candidates` with `source='user_scan'`; negative
  feedback → card at top of `/api/label/next`.

## Risks

- **Calibration drift** — `route_scan` trust rides on temperature/conformal
  staying calibrated; both are re-fit on every model ship (notebook §6).
- **Model blind spots** — mitigated by negative feedback always reaching the
  queue at top priority, and suspicious scans always being kept.
- **Consent/regulatory** — recording is consent-gated and anonymized;
  `website/privacy.html` and the in-app consent screen are the canonical
  disclosure.
