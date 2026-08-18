# Review of COMMUNITY_FORUM_V3_PLAN.md

Adversarial read of the first draft, done the way a reviewer who has to sign off on the result would do it. Every claim in the plan was checked against the code on `claude/garden-refactor` at `0be8016`. Findings are ordered by how much damage they would do if the plan shipped as written.

Verdict on the draft: **not ready to hand over.** Two findings would have stopped Sonnet dead or, worse, let it build the wrong thing confidently. Several more are the kind of vagueness that produces a plausible-looking result nobody can accept.

---

## Blocking

### B1. The plan assumes a Community bottom-nav destination that does not exist

Section 4 says "make it the Сообщество bottom-nav destination" as though the slot is sitting there empty.

It is not. `res/menu/bottom_nav_menu.xml` contains exactly five entries: `homeFragment`, `cameraFragment`, `gardenFragment`, `galleryFragment`, `profileFragment`. There is no community entry anywhere in the navigation. The target design also shows five entries, and its fourth is Сообщество where the app currently has Gallery.

So the plan quietly contains a product decision it never states: **Gallery loses its bottom-nav slot.** That is not a detail to leave to an implementer. Gallery is a real screen, and the account merge just added community badges to it (`GalleryFragment.loadCommunityBadges`), so it is actively being invested in, not abandoned.

The draft also never mentions that these are Navigation-component destinations, so adding one means editing the nav graph, not just the menu XML.

Fix: name the decision, get it decided before Phase 2 starts, and spell out the nav-graph edit. Do not let "make it the destination" stand.

### B2. Denormalised nicknames have an existing repair path the plan ignores

The draft schema puts `nickname` directly on `forum_posts` and `forum_comments`. Reasonable, that is what `community_opinions` already does.

But there is machinery that depends on knowing every table where a nickname was denormalised. `internal_account_nickname` at `server/main.py:3375` exists to "idempotently replace an actor's denormalized historical nickname", and it delegates to `community_update_actor_nickname`. When a user renames themselves, that path rewrites their historical nickname across community content.

The plan adds two new tables holding denormalised nicknames and says nothing about extending that function. Ship it as written and a rename updates scans and opinions while forum posts keep showing the old name forever. Nobody would notice until a user complains, and by then there is no record of which name was correct.

Fix: Phase 1 must extend `community_update_actor_nickname` to cover both new tables, and the test list must include a rename assertion.

---

## Serious

### S1. The pagination contract is unspecified, and may already be broken

The plan tells Sonnet to accept a `cursor` parameter. The existing `/community/nearby` accepts `country`, `region`, `lat`, `lon`, `disease`, `species`, `limit`, `scope`. No cursor. No `before`.

Worse: our pre-merge `NearbyScansActivity` does cursor paging with a `nextBefore` field and a `loadMore()` call, and the merge replaced `server/main.py` wholesale with the account branch's version. So the client may now be paging against a parameter the server no longer reads, which would mean the existing feed silently returns the same first page forever.

The plan neither specifies the new contract nor flags the suspected existing breakage.

Fix: verify whether feed paging currently works at all, state the result, and define the forum cursor concretely (what it encodes, how ties break) instead of naming a parameter.

### S2. No rate limiting, on the most abusable surface in the app

The account branch added `_consume_daily_write(operation, address, limit)` and a `COMMUNITY_*_DAILY_QUOTA` pattern, and applied it to community writes. The plan introduces free-form user-authored text with photos, which is strictly more abusable than anything currently there, and never mentions quotas once.

Fix: post creation, commenting, and voting each need a quota, following the existing helper. Say so in Phase 1.

### S3. "Your call" in a handoff plan

Section 4: "demote it to an entry inside the forum or to the map screen, your call". A plan written to be executed autonomously by another model cannot contain a shrug. Either decide, or mark it as a question to bring back before proceeding.

Same defect, softer form, in section 3.3, where anonymous read access is offered as "if you want".

### S4. Phase 1 opens with Android work while telling Sonnet not to do Android work

Step 1 of "Phase 1: the server" is a pile of Kotlin and XML deletions. The kickoff prompt then says "Do not touch the Android feed beyond the deletions listed in step 1." The phase heading and its content disagree, and the reader has to reconcile them.

Fix: make the deletion its own Phase 0, or retitle the phase.

### S5. Views on open will not produce the numbers in the design

The sample shows 232 views on a post that is one hour old, against 12 upvotes and 5 comments. That ratio implies views are counted cheaply, probably per feed impression or per open without dedupe.

The plan mandates per-account dedupe on open. That is defensible, and it is what the existing scan-view code does, but it will produce view counts roughly in line with comment counts, not twenty times higher. The screen will look wrong compared to the mockup and someone will file it as a bug.

Fix: either accept it and say so explicitly in the plan so nobody is surprised, or specify a different counting rule. Do not leave the mismatch undocumented.

---

## Gaps that will produce a half-finished screen

### G1. Relative timestamps are treated as free

Every card in the design shows "1 ч назад", "2 ч назад", "5 ч назад". The plan never mentions a formatter. Russian relative time needs plural agreement (час / часа / часов, минута / минуты / минут), which is real work and a classic place to ship something embarrassing. Android has `DateUtils.getRelativeTimeSpanString`, which handles it if the locale is right, and the app has a locale-wrapping quirk documented in `PlantReminderWorker` and `OpinionNotifier` where an application context falls back to the system locale rather than the in-app one.

Fix: name the helper, and name the locale trap.

### G2. Category icons are assumed to exist

The plan says reuse existing line icons and only draw new ones if needed. There are 84 `ic_*` drawables, but nobody checked whether any of them depict watering, fertiliser, pests, lighting, or propagation. The odds that all eight categories are already covered are low.

Fix: audit the drawables against the eight categories in Phase 2 and list which need drawing.

### G3. "Ещё" overflow behaviour is observed but not specified

The plan correctly notices that "Освещение" is tagged on a card while absent from the strip, and concludes categories overflow. It then never says what tapping "Ещё" does. A sheet? A scroll? A second row?

### G4. No empty, error, or offline states

The app has `ConnectivityObserver`, `enableWhileOnline`, and `onReconnected` helpers used by the existing community screens. A new feed needs an empty state (no posts in this category), a failure state, and offline behaviour. The plan covers none of them, so they will either be skipped or invented inconsistently.

### G5. No definition of done beyond "tests pass"

Neither phase states what a reviewer should look at to accept the work. For a screen being built against a supplied design, that should include a comparison against the design.

---

## Smaller, still worth fixing

### N1. `community_bearer`'s principal is used without being described
The plan says to reuse it. It never says the principal carries `.nickname`, which is how existing writes populate that column (`main.py:3506`, `:3817`). Sonnet will go looking.

### N2. `UploadImageSanitizer` is described as if it were a static utility
It is an interface at `util/UploadImageSanitizer.kt:32`, implemented by `DefaultUploadImageSanitizer`, provided through Hilt at `AppModule.kt:205`. Phase 3 says "route the image through `UploadImageSanitizer`" without saying it must be injected.

### N3. Contradiction about `likeCount` / `likedByMe`
Step 1 says keep them because removing them widens the diff, and justifies it by saying Phase 2 replaces the card wholesale. If the card is replaced wholesale, the fields are dead either way, so the stated reason does not hold. Harmless, but it is the kind of loose reasoning that erodes trust in the rest of the document.

### N4. Search route auth is left ambiguous
Section 3.3 opens with "All of these take `community_bearer` except where noted", then never notes anything about search, while separately floating anonymous reads for the list route.

### N5. Counter consistency has no failure story
`vote_count`, `comment_count`, and `view_count` are denormalised. The plan says to update them in the same transaction, which is right, but says nothing about what happens if they drift, and provides no way to recompute.

### N6. Migration mechanism is not named
The repository has two: raw `CREATE TABLE IF NOT EXISTS` plus `_add_column_if_missing` in `server/community.py`, and Alembic in `account-server/migrations/`. The forum tables belong to the first. The plan never says which, and a reader who has just come from `account-server/` could reasonably guess wrong.

---

## What the draft got right

Worth recording so the rewrite does not lose it.

The decision to make posts a separate entity rather than widening `community_scans` is correct and the stated reason holds up: scan retention rules were written for diagnostic images, not user prose.

Front-loading the "what changed under you" section is right, and the warning that the older `docs/` plans describe a deleted identity model will save real time.

Reusing the existing report and block routes instead of building a second moderation system is the right instinct.

The instruction to verify plan claims against code before relying on them is good, and this review is the proof it was needed.
