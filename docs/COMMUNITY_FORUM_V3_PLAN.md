# Community v3: Forum Plan

Turning the scan feed into the question-and-answer forum in the target design, on top of the account system that arrived with the account beta merge.

Revision 2. The first draft was reviewed and rewritten. Findings are recorded in `COMMUNITY_FORUM_V3_PLAN_REVIEW.md`; every one is addressed here.

## 0. Read this first

You are picking this up cold. Everything below was verified against the code on branch `claude/garden-refactor` at commit `0be8016`. Do not trust the older community documents in `docs/`. They describe a device-key identity model that the merge deleted, and following them will waste your time.

### What just changed under you

`origin/account-beta-test` was merged in. It added a second service, `account-server/`, a FastAPI app with sign-in through Yandex ID and VK ID, generated nicknames, refresh-token rotation, and account deletion. Community writes are now bound to a signed account through `account_actor_id`.

Gone, with the client plumbing still lying around: `/api/community/like` with its `community_likes` table, and `/api/community/authors/adopt` with `community_authors`. Likes were ours, the account branch never had them, and the like route carried no auth dependency. `CommunityIdentity.kt` and the `X-Community-Author-Key` header have nothing to talk to.

### Two defects the merge left behind, both confirmed

**Feed paging is broken.** `ScanRepository` sends a `before` parameter to `/community/nearby` (line 1501). The merged server's handler at `main.py:4041` accepts `country`, `region`, `lat`, `lon`, `disease`, `species`, `limit`, `scope`, and nothing else. The parameter is silently ignored, so `NearbyScansActivity.loadMore()` refetches page one every time the user scrolls. Verified by reading both sides. Phase 0 fixes it.

**A like call now sends an empty identity.** `NearbyScansActivity:191` calls `setCommunityLike(item.scanId, "", next)` against a route that no longer exists. Phase 0 deletes the whole path.

### What exists on the server

Read `server/main.py` from roughly line 3398 to 4400 before writing anything. Community routes: `POST /community/scan`, `GET /community/nearby`, `GET /community/map`, `/heatmap`, `/by-species`, `POST /community/opinion`, `GET /community/opinions/{scan_id}`, `POST /community/view`, `POST /community/scan-counts`, the four `/community/safety/*` routes, and `GET /community/thumb/{filename}`.

Every write carries `dependencies=[Depends(community_bearer)]`. Match it. A new write route without it is a forgeable route, and we already removed one of those.

The bearer yields a principal object. It carries `.nickname`, which is how existing writes populate their nickname column. See `main.py:3506` and `main.py:3817` for the pattern to copy.

`server/community.py` holds schema and data access. You will reuse `_actors_blocked`, `_actor_is_deleted`, and `_identity_predicate`. Read them first.

Migrations here are raw `CREATE TABLE IF NOT EXISTS` plus the `_add_column_if_missing` helper in that same file. The Alembic setup under `account-server/migrations/` belongs to the other service and is not what you want.

### What exists on Android

`ui/community/` holds `NearbyScansActivity`, `NearbyScansAdapter`, `CommunityMapActivity`, `MyScanRepliesActivity`, `OpinionViews`. The opinion sheet is at `ui/result/CommunityOpinionSheet.kt`. Layouts: `activity_nearby_scans.xml`, `item_nearby_scan.xml`, `sheet_community_opinion.xml`, `sheet_nearby_filter.xml`.

The feed is already one cursor-paged column rather than a grid, which is closer to the target than the account branch's staggered grid. Keep the structure, replace the card.

`res/menu/bottom_nav_menu.xml` has five entries: `homeFragment`, `cameraFragment`, `gardenFragment`, `galleryFragment`, `profileFragment`. **There is no community destination.** These are Navigation-component destinations, so adding one means editing the nav graph, not only the menu file.

### The gap, stated plainly

A community entry today is a **scan**: a photo the classifier examined, plus a diagnosis and a coarse region. The target shows a **post**: a title, a body, a category, an optional photo, no diagnosis. Those are different objects. A post is not a scan with the diagnosis hidden. Everything below follows from that.

## 1. What the target design specifies

Header reading "Сообщество" with subtitle "Задавайте вопросы и делитесь опытом". Search icon and a round green compose button on the right.

A horizontally scrolling category strip below it, each entry a thin-stroke icon above a label. Visible: Все, Уход, Болезни, Удобрения, Вредители, Полив, then "Ещё". Active entry is green in both icon and label, with a green underline roughly 3dp tall. Inactive is grey.

A row of sort pills under that: Лучшие, Свежие, Без ответа, Популярные. Active is a filled green pill, inactive is outlined, single selection.

Then the cards. Each holds a circular avatar around 40dp, the author nickname, a relative timestamp, an overflow menu at the far right, a bold title around 17sp, two or three muted body lines, and a bottom row with a green-tinted category tag followed by upvote, comment, and view counts as outline icon plus number. A rounded thumbnail around 96dp sits right when the post has a photo.

Bottom navigation shows five entries with Сообщество active.

Note that the final card is tagged "Освещение", which is absent from the visible strip. Categories overflow into "Ещё".

### Do not invent a palette

The app has one. Take greens, surfaces, and text colours from `res/values/colors.xml` and the existing dark theme, which the screenshot is consistent with. If a colour has no token, add one to `colors.xml` rather than hardcoding hex in a layout.

There are 84 `ic_*` drawables. Reuse before drawing. Nobody has checked whether watering, fertiliser, pests, lighting, or propagation are among them, so audit that in Phase 2 and list what needs making. Match the existing stroke weight; do not import a heavier icon set.

## 2. Decisions already made

These are settled. If you believe one is wrong, say so and stop. Do not quietly build something else.

**Posts are a new entity.** New tables, new routes, new DTOs. Scan sharing keeps working and is not touched. Widening `community_scans` would force existing rows to pretend they have titles and categories, and would put free-form user text into a table whose retention rules were written for diagnostic images.

**Upvote only.** The design shows one up arrow. One vote per account per post; voting again removes it.

**Categories are a fixed server list**, not user-created: `care`, `disease`, `fertilizer`, `pests`, `watering`, `lighting`, `propagation`, `other`. The server returns a key, the app resolves the label from `strings.xml`. That keeps labels working offline and translation in one place.

**Views are deduped per account and counted on open.** Confirmed with the product owner. This will produce numbers close to comment counts, not the 232-against-12 ratio in the mockup, which implies undeduped impression counting. That difference is intentional and accepted. Do not "fix" it later by inflating the count, and do not file the discrepancy as a bug.

**Community takes the Gallery slot in the bottom nav, and Gallery folds into Home.** Confirmed with the product owner. Gallery is not deleted: it becomes a section reachable from Home, and it keeps the community badges the merge added in `GalleryFragment.loadCommunityBadges`. The nav graph and `bottom_nav_menu.xml` both change.

**Reuse the existing safety layer.** Reports and blocks already work against `account_actor_id`. Posts and comments become new target types on the existing routes. Do not build a second moderation system.

**Reads may be anonymous, writes may not.** The bundled privacy policy already promises "Anyone may read the community without an account". The list, detail, and search routes are readable without a token. Every write requires the bearer.

## 3. Phase 0: clear the wreckage

Small, mechanical, entirely deletion plus one bug fix. Do this first and commit it separately.

Delete the dead like and adopt plumbing. In `ScanRepository.kt`: `setCommunityLike`, `adoptCommunityAuthor`, `ensureCommunityAuthorAdopted`, and the `X-Community-Author-Key` header. In `CommunityUploader.kt`: both `ensureCommunityAuthorAdopted` calls. In `Models.kt`: `CommunityLikeRequest`, `CommunityLikeResponse`, `CommunityAuthorAdoptRequest`, `CommunityAuthorAdoptResponse`, and the now-unused `likeCount` and `likedByMe` on `CommunityScanItem`. Delete `util/CommunityIdentity.kt` and `util/CommunityIdentityTest.kt`, and the `adoptCommunityAuthor` test in `ScanRepositoryNetworkTest.kt`. Remove the like row from `item_nearby_scan.xml` and `sheet_community_opinion.xml`, the `toggleLike`, `onLike` and `updateLike` paths in `NearbyScansActivity`, `CommunityMapActivity` and `NearbyScansAdapter`, and `renderLike` in `CommunityOpinionSheet`.

Then fix the paging regression. Add a `before` parameter to `/community/nearby` in `server/main.py`, matching what the client already sends: an integer `created_at` cursor, returning rows strictly older than it, newest first, tie-broken by row id so a shared timestamp cannot loop. Add a test that a second page excludes the first page's rows.

Fix the stale kdoc on `NearbyScansActivity` while you are there. It still describes likes and a vote graph.

Done when: `ruff check .` is clean, the server suite is green apart from the known Windows failures below, and the deletion commit contains no additions beyond the `before` parameter and its test.

## 4. Phase 1: the server

Stop and start a new conversation after this phase.

### Schema

Add to `server/community.py` in the existing `CREATE TABLE IF NOT EXISTS` style.

`forum_posts`: `post_id` text primary key, `account_actor_id` text not null, `nickname` text, `category` text not null with a CHECK against the eight keys, `title` text not null, `body` text not null, `thumb_path` text, `created_at` integer not null, `updated_at` integer, `comment_count` integer default 0, `vote_count` integer default 0, `view_count` integer default 0. Index `(category, created_at)` and `(created_at)`.

`forum_votes`: `post_id`, `account_actor_id`, `created_at`, primary key on the first two.

`forum_views`: same shape, same primary key, mirroring how scan views dedupe.

`forum_comments`: `comment_id`, `post_id`, `account_actor_id`, `nickname`, `body`, `created_at`. Index `(post_id, created_at)`.

The three counters on `forum_posts` are denormalised deliberately, because the feed sorts and filters on them. Update each inside the same transaction as the write that changes it. Also add a `recompute_forum_counters(post_id)` helper that rebuilds all three from the source tables, and call it from the tests. Without a way to recompute, drift is unrecoverable.

### Nickname renames

`forum_posts` and `forum_comments` both store a denormalised nickname. `community_update_actor_nickname`, reached through `internal_account_nickname` at `main.py:3375`, exists to keep historical nicknames current when someone renames. **Extend it to cover both new tables.** Skip this and a rename updates scans and opinions while forum content shows the old name forever, with nothing recording which was right.

### Routes

Writes take `dependencies=[Depends(community_bearer)]`. Reads (list, detail, search) do not.

`POST /community/forum/post` creates one. Validate title length, body length, and category against the enum. Put any uploaded image through the same path `/community/scan` uses, which re-encodes and strips EXIF via `community._save_thumbnail`. Do not write a second image path.

`GET /community/forum/posts` lists. Parameters: `category` (absent or `all` for everything), `sort`, `before`, `limit`. Filter out blocked and deleted actors using `_actors_blocked` and `_actor_is_deleted`. The cursor is the same contract as Phase 0's fix: an integer `created_at`, strictly older, tie-broken by row id.

Sort semantics, exactly: `fresh` is `created_at DESC`. `unanswered` is `comment_count = 0` then `created_at DESC`. `popular` is `view_count DESC`. `best` is `vote_count DESC` with `created_at DESC` as tiebreak. No decay formula in this phase. Note that `popular` and `best` need their own cursor handling, since a `created_at` cursor does not paginate a count-ordered list; use `(sort_value, created_at, id)` as the cursor tuple for those two.

`GET /community/forum/post/{post_id}` returns the post and its comments.

`POST /community/forum/post/{post_id}/vote` toggles and returns the resulting count and state.

`POST /community/forum/post/{post_id}/view` records a deduped view.

`POST /community/forum/post/{post_id}/comment` appends. No one-per-author rule here; a forum thread is a conversation, unlike a scan opinion.

`GET /community/forum/search?q=` does a `LIKE` match over title and body, newest first, capped. `LIKE` is the right amount of machinery at this table size, and moving to FTS5 later touches one function.

Extend the safety report and block target types to accept `post` and `forum_comment`. No new safety routes.

### Rate limiting

The account branch added `_consume_daily_write(operation, address, limit)` and the `COMMUNITY_*_DAILY_QUOTA` pattern. Free-form user text with photos is the most abusable surface in the app. Apply a quota to post creation, commenting, and voting, following the existing helper. Do not skip this.

### Tests

`server/tests/test_forum.py`. Cover: a post round-trips; a bad category is rejected; each of the four sorts returns the order it claims; paging returns disjoint pages for every sort mode; a blocked actor's posts are absent from another actor's feed; voting twice leaves the count at zero rather than two; view dedupe holds across repeat calls; an unauthenticated write is rejected while an unauthenticated read succeeds; a nickname rename updates forum rows; `recompute_forum_counters` agrees with the denormalised values after a mixed workload.

`conftest.py` already swaps in a fake pipeline, so no model weights are needed.

Run `ruff check .` and `pytest tests -q` from `server/`. Four failures in `test_account_auth.py` are expected on Windows only: they call `chmod(0o640)` and Windows has no POSIX permissions. Those files are byte-identical to the account branch, and Linux CI passes them. Everything else must be green.

Regenerate the API document with `python tools/export_openapi.py`, since `server/openapi.json` is checked in and CI compares it.

Done when: all of the above is green, `openapi.json` is regenerated, and no write route lacks the bearer dependency.

## 5. Phase 2: the feed screen

New conversation.

Build `ui/community/ForumFragment` and give it the fourth bottom-nav slot. Edit both `res/menu/bottom_nav_menu.xml` and the nav graph. Move `galleryFragment` out of the bottom nav and surface it from Home, keeping its community badges working. `NearbyScansActivity` stays reachable from inside the forum; do not delete it.

Layouts: `fragment_forum.xml` (header, category strip, sort row, list), `item_forum_post.xml`, `item_forum_category.xml`.

The card is a `MaterialCardView` at roughly 16dp corner radius, no stroke, background one step lighter than the screen. The thumbnail is a `ShapeableImageView`. Build the metrics row as a horizontal `LinearLayout` of icon and text pairs rather than a chip group, because chips carry their own padding and will not align with the design.

The category strip is a `RecyclerView` with a horizontal `LinearLayoutManager`. The underline is a view inside the item layout, shown on selection, not an item decoration. Tapping "Ещё" opens a bottom sheet listing every category, which is the same pattern `sheet_nearby_filter.xml` already uses.

Sort pills are a `ChipGroup` in single selection. Material 3 filter chips already provide filled and outlined states; use the theme rather than two custom drawables.

Relative timestamps ("1 ч назад") come from `DateUtils.getRelativeTimeSpanString`, which handles Russian plural agreement correctly. Watch the locale trap documented in `PlantReminderWorker` and `OpinionNotifier`: an application context is never wrapped by `LocaleHelper`, so string resolution falls back to the system locale and will disagree with a Russian-in-app, English-system user. Resolve through a locale-wrapped context.

Empty, error, and offline states are required, not optional. The screen needs an empty state per category, a failure state, and offline behaviour. Use the existing `ConnectivityObserver`, `enableWhileOnline`, and `onReconnected` helpers the current community screens already use.

DTOs go in `data/model/Models.kt` as `@Serializable` with both `@SerializedName` and `@SerialName` on every field, matching the surrounding style. Repository calls sit in `ScanRepository.kt` beside the existing community calls; writes route through `authenticatedAppCall`.

Both languages. Every string in `values/strings.xml` and `values-ru/strings.xml`, including all eight category labels and the four sort labels. The Russian text in the screenshot is the target copy; use it verbatim.

Robolectric tests under `src/test/`, following the existing pattern. Not instrumented tests.

Done when: the screen matches the supplied design side by side, both languages render without truncation, empty and offline states are reachable, `ktlintCheck`, `detekt` and `testDebugUnitTest` pass, and the nav change leaves Gallery reachable.

## 6. Phase 3: compose, detail, search

New conversation.

Compose screen: category picker, title field, body field, optional photo from camera or gallery. The image must go through `UploadImageSanitizer`, which is an interface at `util/UploadImageSanitizer.kt:32`, implemented by `DefaultUploadImageSanitizer`, injected through Hilt (`AppModule.kt:205`). Inject it; it is not a static utility. The round green header button opens this screen.

Detail screen: the full post, the comment thread, a reply field. Overflow menus on both card and detail offer report and block, wired to the safety routes that already exist.

Search sits behind the magnifier and hits the search route.

Done when: a post can be created, seen in the feed, opened, commented on, voted on, reported, and blocked, in both languages.

## 7. Things that will bite you

`BuildConfig.ACCOUNT_UI_ENABLED` gates a lot of account behaviour. Check what it does to your screen before assuming a signed-in state exists. `scheduleOpinionChecks` and `checkOpinionsNow` in `PlantDiseasesApp.kt` are gated on it plus the notifications preference.

`server/config.json` beats environment variables, not the reverse, and blank counts as unset. See `server/config.py`.

Do not build or train locally. CI runs `ruff` and `pytest` for the server, and `ktlintCheck`, `detekt`, `testDebugUnitTest` and an R8 release build for Android, on every push to `claude/**`. Targeted single tests locally are fine. A full `assembleRelease` is not.

The privacy policy exists twice on purpose. `android/app/src/main/assets/privacy_policy.html` backs the offline in-app screen; `privacy.html` at the repository root is read by `android-release.yml` and by `account-server/tests/test_deployment_handoff.py`. The app copy carries a `?lang=` script the root copy does not. If forum posts change what data is collected, both need updating, and the app copy must keep its script.

## 8. Kickoff prompt

Paste this to start.

> Read `docs/COMMUNITY_FORUM_V3_PLAN.md` in full before touching anything, then execute Phase 0 and Phase 1 only, sections 3 and 4. Stop at the end of Phase 1 and report.
>
> We are on branch `claude/garden-refactor`. The account beta was merged at `0be8016`, which deleted the device-key community identity and the likes feature. Older community documents in `docs/` describe the deleted model; ignore them and treat the code as truth.
>
> Phase 0 is deletion plus one confirmed bug fix (community feed paging is broken: the client sends `before`, the server no longer accepts it). Commit Phase 0 separately from Phase 1.
>
> Stop when `ruff check .` is clean and `pytest tests -q` is green apart from the four Windows-only `test_account_auth.py` failures the plan describes.
>
> Verify every claim in this plan against the code before relying on it. If something does not match what you find, stop and say so rather than working around it. The plan has already been wrong once and was corrected; assume it can be wrong again.
