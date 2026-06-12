# Google Play — Data Safety form answers

Copy-paste answers for the Play Console **Data safety** section, kept in sync
with what the app/server actually do (consent screen in onboarding,
`server/main.py` scan recording, `website/privacy.html`).

Privacy policy URL: **https://pdis.arbuz.lol/privacy.html**

## Overview answers

| Question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | **Yes** |
| Is all of the user data collected by your app encrypted in transit? | **Yes** (HTTPS) |
| Do you provide a way for users to request that their data is deleted? | **Yes** (in-app Profile → delete my data; cascades community posts, feedback and stored scan photos) |

## Data types

### Photos and videos → Photos
- **Collected:** Yes. **Shared:** No.
- **Processed ephemerally:** No (anonymized copies are retained for model training; unreviewed scans auto-delete after 90 days).
- **Required or optional:** Required for the core scan feature (the consent screen is shown on first launch; there is no separate opt-out toggle).
- **Purposes:** App functionality (diagnosis), Analytics (model quality), **App functionality → improve ML models**.
- Notes for reviewers: photos are re-encoded with all EXIF/GPS metadata stripped and downscaled before storage; no account/name/device identifiers are attached.

### Location → Approximate location
- **Collected:** Yes, **optional** (only if the user opts into the community feed and grants coarse location, or types a region manually).
- **Shared:** No (the user-chosen country/city string is shown on their shared community posts — user-initiated sharing, not data sharing with third parties).
- **Purposes:** App functionality (nearby community scans).
- Precise location is **never** collected; photo GPS EXIF is stripped.

### Messages / Other user-generated content
- **Collected:** Yes (optional feedback notes, community second-opinion comments).
- **Shared:** No.
- **Purposes:** App functionality, Analytics (model improvement).

### App activity / App info / Device IDs
- **Not collected.** No advertising ID, no device identifiers, no account system. (`user_pseudo_id` is an app-generated random install ID used only so users can delete/export their own contributions; declare under App activity → "App interactions" if reviewers ask, purpose: App functionality, with deletion path.)

## Health-claims hygiene (store listing)
- Do not claim guaranteed/clinical accuracy. Use: "AI-assisted plant disease *guidance*".
- Keep the in-listing disclaimer: diagnoses are produced by an ML model, can be wrong, and the app surfaces its own uncertainty; consult a professional for valuable plants.
- The consent screen + this policy satisfy the Play "prominent disclosure & consent" requirement for photo collection.
