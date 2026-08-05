# Lunar Calendar v2 + Garden Care Refactor

**Execution plan for Sonnet. Two epics, one pass. Est. ~5 h lunar + ~7.5 h garden.**

---

## Context

Two bodies of work, driven by real defects the user hit on-device and by a spec they want built.

**Epic A — the lunar calendar shipped last session is half-broken.** Verified in code:
- `LunarCalendarView.buildDayCell` branches `isToday -> ring` ELSE `rating fill`, so today loses its rating colour, and the `selected` field is never used for the ring at all.
- The cell tap handler calls `renderToday(date)` but **not** `renderGrid()` — the ring never moves, so day switching looks dead.
- "Ближайшие благоприятные дни" rows *do* fire, but the card they update sits far above the fold, so the `>` reads as broken.
- Zodiac signs render via emoji `glyph` strings, which the system emoji font draws as coloured circles.
- `MoonPhaseView`'s terminator maths is wrong — it draws a flat-edged blob, and it is static.
- Section expand/collapse uses a blunt 180 ms `AutoTransition` with an instant chevron snap.
- `LunarCalendar` takes only a `LocalDate` — advice is identical for a tomato and a cactus.

**Epic B — the Garden refactor is genuinely unbuilt.** `docs/ROADMAP.md:215-217` says so; verified independently. Today the Garden is a flat list; this turns it into a care hub.

**One critical historical fact:** `MIGRATION_4_5` (`Database.kt:511-547`) exists *solely* to DROP a free-text `plants.room` column. Someone already built rooms this way and reverted it. We are **not** re-adding that column — see §B1.1.

**Scope note on "every plant":** the plant set is **open-ended**, not enumerable. Species arrive as free text from PlantNet / Wikipedia / the community feed (that is how "Томат" appeared — it is nowhere in the repo). So "finalize the calendar for every plant" is delivered as: a pure local classifier mapping any free-text species to one of 7 categories, plus guaranteed complete coverage of 2026–2027. Nothing is precomputed or persisted.

---

## Global hard constraints — read before touching anything

1. **Do not touch `server/`.** No new endpoints, no DTO fields. `CarePlanResponse` is read-only here.
2. **Do not rename or delete any existing `@+id`** in `activity_species_detail.xml`, `fragment_garden.xml`, `item_plant_card.xml`. `view_lunar_calendar.xml` is the one file that gets restructured — and even there every id is *preserved*, only repositioned.
3. **Do not modify `CarePlanCardView.checklistSection` / `checkpointsTimeline`.** That card is shared by `SpeciesDetailActivity` (no plant), `ResultActivity` (scan not yet attached to a plant) and `PlantDetailActivity`. Stateful checkboxes there would have nothing to persist against in two of three hosts. The persistent checklist is a **separate, plant-scoped card** (§B5.2).
4. **Do not rebuild progression / photo-compare.** `buildProgressionCard`, `PhotoCompareActivity`, `SparklineView` are shipped and working.
5. **Do not add a 6th bottom-nav tab.** `ShellFragment.PAGES` is a fixed 5-page pager; a 6th breaks the tab↔page index mapping.
6. **Every new parameter is trailing with a default.** Non-negotiable — this is what keeps `LunarCalendarTest`, `GardenFragment` and `AppModule` compiling.
7. Every new colour goes in **both** `values/colors.xml` and `values-night/colors.xml`. Every new string goes in **both** `values/strings.xml` (EN) and `values-ru/strings.xml` (RU).
8. Use existing tokens only: `@dimen/space_*`, `@dimen/radius_*`, `@dimen/text_*`, `@color/{primary,surface,surface_variant,on_surface,on_surface_secondary,healthy_green,warning_amber,disease_red,divider,card_stroke}`, `@style/App.GlassCard`.
9. **Do not run a full Gradle build.** CI runs ktlintCheck + detekt + testDebugUnitTest + assembleRelease. At most run single unit tests.
10. minSdk 29, Kotlin 2.1.21, JVM 17. detekt is lax (LongMethod / MagicNumber / LargeClass off). ktlint: `max_line_length = off`, wildcard-import and trailing-comma rules disabled.

11. **Verified numbers in this document were computed, not guessed.** Where it says "verified numerically", trust the figure and do not re-derive it.

---

# EPIC A — Lunar calendar v2

## A1. Plant categories

### A1.1 New file `android/app/src/main/java/com/plantdiseases/app/util/PlantCategory.kt`

```kotlin
enum class PlantCategory { FRUITING, ROOT, LEAFY, FLOWERING, SUCCULENT, FOLIAGE, HERB }

object PlantCategoryClassifier {
    fun classify(scientificName: String?, commonName: String?): PlantCategory
    internal fun normalize(raw: String?): List<String>       // lowercase, ё→е, non-letter → space, split, drop len<3
    internal fun binomial(scientificName: String?): String?  // "genus species", lowercase, or null
}
```

**Default when nothing matches: `FOLIAGE`** (this is a houseplant app; foliage is the modal case). Document that in KDoc.

### A1.2 Classifier algorithm — exact order

```
1. b = binomial(scientificName)
   if (b != null && BINOMIAL_OVERRIDES.containsKey(b)) return BINOMIAL_OVERRIDES[b]
2. g = first token of scientificName, lowercased
   if (GENUS_MAP.containsKey(g)) return GENUS_MAP[g]
3. tokens = normalize(commonName) + normalize(scientificName)
   for (category in PRIORITY)
       for (stem in KEYWORDS[category])
           if (tokens.any { it.startsWith(stem) }) return category
4. return PlantCategory.FOLIAGE
```

`PRIORITY = [SUCCULENT, HERB, ROOT, LEAFY, FRUITING, FLOWERING, FOLIAGE]` — most-specific vocabulary first.

**Matching rule: token `startsWith(stem)`, minimum stem length 4.** Never `contains` on the whole string — `"pea"` would match `"peace lily"`, `"peach"` and `"peat"`. Every stem below is ≥4 chars for exactly this reason.

**`BINOMIAL_OVERRIDES`** (checked before genus; resolves genus collisions):

| binomial | category | why |
|---|---|---|
| `solanum tuberosum` | ROOT | genus Solanum is FRUITING |
| `allium schoenoprasum` | HERB | chives; genus Allium is ROOT |
| `allium sativum` | ROOT | garlic (explicit) |
| `brassica oleracea` | LEAFY | explicit |
| `beta vulgaris` | ROOT | explicit |
| `ficus carica` | FRUITING | fig; genus Ficus is FOLIAGE |
| `dracaena trifasciata` | SUCCULENT | snake plant; genus Dracaena is FOLIAGE |
| `kalanchoe blossfeldiana` | SUCCULENT | watering rules outweigh its flowering |

**`GENUS_MAP`** (lowercase genus → category):

- **FRUITING**: `solanum, lycopersicon, capsicum, cucumis, cucurbita, citrullus, fragaria, citrus, physalis, passiflora, vitis, musa, coffea, punica, olea, persea, pisum, phaseolus, zea`
- **ROOT**: `daucus, raphanus, pastinaca, allium, zingiber, curcuma, manihot, beta, armoracia, helianthus`
- **LEAFY**: `lactuca, spinacia, brassica, eruca, cichorium, valerianella, nasturtium, rumex`
- **HERB**: `ocimum, mentha, thymus, rosmarinus, salvia, petroselinum, anethum, coriandrum, origanum, melissa, lavandula, artemisia, satureja, levisticum, stevia`
- **SUCCULENT**: `echeveria, aloe, haworthia, haworthiopsis, crassula, sedum, sempervivum, kalanchoe, agave, gasteria, euphorbia, opuntia, mammillaria, gymnocalycium, echinocactus, echinopsis, ferocactus, cereus, schlumbergera, rhipsalis, lithops, portulacaria, pachyphytum, graptopetalum, adenium, pachypodium, sansevieria, aeonium, senecio`
- **FLOWERING**: `phalaenopsis, dendrobium, cattleya, cymbidium, oncidium, paphiopedilum, saintpaulia, streptocarpus, anthurium, spathiphyllum, begonia, pelargonium, geranium, rosa, hibiscus, gardenia, jasminum, camellia, clivia, hippeastrum, cyclamen, primula, chrysanthemum, gerbera, rhododendron, bougainvillea, fuchsia, impatiens, hoya, tulipa, narcissus, crocus, lilium, sinningia, plumeria, stephanotis`
- **FOLIAGE**: `monstera, philodendron, epipremnum, scindapsus, ficus, dracaena, chlorophytum, calathea, goeppertia, maranta, aglaonema, dieffenbachia, syngonium, zamioculcas, alocasia, colocasia, caladium, peperomia, pilea, tradescantia, nephrolepis, asplenium, adiantum, pteris, platycerium, howea, chamaedorea, dypsis, yucca, schefflera, fatsia, hedera, cissus, aspidistra, fittonia, hypoestes, ctenanthe, stromanthe`

**`KEYWORDS`** (stems, EN + RU together; RU stems truncated before case endings):

- **SUCCULENT**: `succulent, cact, cacti, aloe, jade, eche, hawor, agave, sedum, stonecrop, houseleek, kalanchoe, lithop, суккулент, кактус, алоэ, толстянк, денежн, эхевери, хавор, агав, очиток, молодил, каланхоэ, щучий, тёщин, тещин, декабрист, молочай, литопс`
- **HERB**: `basil, mint, thyme, rosemary, sage, parsley, dill, cilantro, coriander, oregano, marjoram, balm, lavender, tarragon, chive, savory, lovage, stevia, herb, базилик, мята, мяты, тимьян, чабрец, розмарин, шалфей, петрушк, укроп, кинза, кориандр, орегано, майоран, мелисс, лаванд, эстрагон, тархун, шнитт, чабер, зелень, прян`
- **ROOT**: `carrot, beet, radish, potato, turnip, parsnip, onion, garlic, shallot, leek, ginger, turmeric, celeriac, rutabaga, swede, horseradish, cassava, tuber, taro, морков, свёкл, свекл, редис, редьк, картоф, репа, пастернак, чеснок, порей, имбир, куркум, брюкв, хрен, батат, топинамбур, клубнелук`
- **LEAFY**: `lettuce, spinach, cabbage, kale, chard, arugula, rocket, chicory, endive, choy, choi, mizuna, watercress, sorrel, microgreen, salad, collard, broccoli, cauliflower, салат, шпинат, капуст, кейл, мангольд, руккол, цикори, эндиви, кресс, щавел, микрозелен, брокколи, цветна`
- **FRUITING**: `tomato, pepper, chili, chilli, cucumber, eggplant, aubergine, zucchini, courgette, squash, pumpkin, melon, watermelon, strawberry, bean, peas, corn, maize, citrus, lemon, lime, orange, mandarin, tangerine, grapefruit, banana, pomegranate, avocado, coffee, grape, passion, томат, помидор, перец, чили, огур, баклажан, кабач, цуккини, тыкв, дын, арбуз, клубник, земляник, фасол, горох, кукуруз, цитрус, лимон, лайм, апельсин, мандарин, грейпфрут, банан, инжир, гранат, авокадо, кофе, виноград, маракуй`
- **FLOWERING**: `orchid, violet, anthurium, peace, begonia, geranium, pelargonium, rose, hibiscus, gardenia, jasmine, camellia, clivia, amaryllis, cyclamen, primrose, chrysanthemum, gerbera, azalea, bougainvillea, fuchsia, impatiens, tulip, daffodil, crocus, lily, bloom, flower, орхиде, фаленопсис, фиалк, сенполи, антуриум, спатифиллум, бегони, геран, пеларгони, роза, розы, гибискус, гардени, жасмин, камели, кливи, амариллис, гиппеаструм, цикламен, примул, хризантем, гербер, азали, бугенвилле, фукси, бальзамин, хойя, тюльпан, нарцисс, крокус, лили, цвету`
- **FOLIAGE**: `monstera, philodendron, pothos, ivy, fern, palm, dracaena, rubber, fiddle, spider, prayer, calathea, peperomia, pilea, монстер, филодендрон, потос, сциндапсус, плющ, папоротник, пальм, драцен, фикус, хлорофитум, калате, маранта, пепероми, пиле, замиокулькас`

Note the deliberate `"peace"` stem in FLOWERING and the absence of `"pea"` anywhere.

New test `PlantCategoryClassifierTest.kt`: `Solanum lycopersicum`/`Томат`→FRUITING; `Solanum tuberosum`→ROOT; `Aloe vera`→SUCCULENT; `Dracaena trifasciata`/`Сансевиерия`→SUCCULENT; `Spathiphyllum wallisii`/`Peace lily`→FLOWERING; `Monstera deliciosa`→FOLIAGE; `Ocimum basilicum`/`Базилик`→HERB; unknown junk→FOLIAGE; `null,null`→FOLIAGE.

### A1.3 How category modulates `LunarCalendar`

All new params **trailing with defaults**:

```kotlin
private data class Rules(
    val goodSigns: Set<Zodiac>, val badSigns: Set<Zodiac>,
    val waxingTasks: List<Task>, val waningTasks: List<Task>,
    val avoidWaterOnWaterSigns: Boolean,
)
private val RULES: Map<PlantCategory, Rules>
private val WATER_SIGNS = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES)

fun rating(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): Rating
fun favourableTasks(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): List<Task>
fun avoidTasks(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): List<Task>
fun dayInfo(date: LocalDate, category: PlantCategory = PlantCategory.FOLIAGE): DayInfo
fun month(year: Int, month: Int, category: PlantCategory = PlantCategory.FOLIAGE): List<DayInfo>
fun upcomingFavourable(from: LocalDate, limit: Int = 4, category: PlantCategory = PlantCategory.FOLIAGE): List<DayInfo>
```

Delete the now-unused private `fertileSigns` / `semiFertileSigns`. Keep `isTurningPoint` unchanged.

**`RULES` — copy verbatim:**

| Category | goodSigns | badSigns | waxingTasks | waningTasks | avoidWaterOnWaterSigns |
|---|---|---|---|---|---|
| FRUITING | CANCER, SCORPIO, PISCES, LIBRA, TAURUS | LEO, AQUARIUS, ARIES | PLANTING, FEEDING, REPOTTING | PRUNING, REPOTTING | false |
| ROOT | TAURUS, CAPRICORN, VIRGO, SCORPIO | LEO, AQUARIUS, GEMINI | FEEDING | PLANTING, REPOTTING, PRUNING | false |
| LEAFY | CANCER, SCORPIO, PISCES, LIBRA | LEO, ARIES, AQUARIUS | PLANTING, FEEDING, WATERING | PRUNING | false |
| FLOWERING | LIBRA, CANCER, PISCES, TAURUS, VIRGO | LEO, ARIES, AQUARIUS | PLANTING, FEEDING, REPOTTING | PRUNING, REPOTTING | false |
| SUCCULENT | CAPRICORN, TAURUS, VIRGO, SAGITTARIUS, LEO | CANCER, SCORPIO, PISCES | REPOTTING, PLANTING | PRUNING, REPOTTING | **true** |
| FOLIAGE | CANCER, SCORPIO, PISCES, VIRGO, LIBRA | LEO, ARIES, AQUARIUS | REPOTTING, FEEDING, PLANTING | PRUNING, REPOTTING | false |
| HERB | CANCER, PISCES, SCORPIO, GEMINI, LIBRA | ARIES, LEO, CAPRICORN | PLANTING, FEEDING, WATERING | PRUNING | false |

**Exact bodies:**

```kotlin
fun rating(date, category) = when {
    isTurningPoint(date)                      -> Rating.BAD
    zodiac(date) in rules(category).badSigns  -> Rating.BAD
    zodiac(date) in rules(category).goodSigns -> Rating.GOOD
    else                                      -> Rating.NEUTRAL
}

fun favourableTasks(date, category): List<Task> {
    if (isTurningPoint(date)) return emptyList()
    val r = rules(category); val sign = zodiac(date); val waxing = isWaxing(date)
    if (sign in r.badSigns) return if (waxing) emptyList() else listOf(Task.PRUNING)
    val base = if (waxing) r.waxingTasks else r.waningTasks
    val water = if (sign in WATER_SIGNS && !r.avoidWaterOnWaterSigns) listOf(Task.WATERING) else emptyList()
    return (base + water).distinct().take(3)
}

fun avoidTasks(date, category): List<Task> {
    if (isTurningPoint(date)) return listOf(Task.PLANTING, Task.REPOTTING)
    val r = rules(category)
    val forced = if (r.avoidWaterOnWaterSigns && zodiac(date) in WATER_SIGNS) listOf(Task.WATERING) else emptyList()
    val good = favourableTasks(date, category).toSet()
    return (forced + Task.entries.filter { it !in good && it !in forced }).take(2)
}
```

**Verified numerically** (do not re-derive): over 2026–2027 this yields ≈35 % GOOD / 30 % NEUTRAL / 35 % BAD, versus today's flat ~58 % green. `upcomingFavourable(2026-08-18, 4)` returns 4 days for **every** category within 90 days (worst case FOLIAGE spans 20 days), so the existing test asserting `size == 4` still passes.

### A1.4 Species → `LunarCalendarView`

`activity_species_detail.xml` already declares `<LunarCalendarView android:id="@+id/lunar_calendar">` inside `body_lunar`, so `binding.lunarCalendar` exists — it is simply never configured today.

```kotlin
// LunarCalendarView
private var category: PlantCategory = PlantCategory.FOLIAGE

fun setSpecies(scientificName: String?, commonName: String?) {
    val next = PlantCategoryClassifier.classify(scientificName, commonName)
    if (next == category && hasRendered) return
    category = next
    renderAll()
}
```

In `SpeciesDetailActivity.onCreate`, immediately after `binding.tvSpeciesScientific.text = scientificName` and **before** `attachSections()`:

```kotlin
binding.lunarCalendar.setSpecies(scientificName, commonName)
binding.lunarCalendar.onRequestScrollTo = { target -> scrollSpeciesTo(target) }
```

Surface it: `TextView @+id/lunar_category_note` under the panel title, text `getString(R.string.lunar_category_note, getString(categoryLabelRes(category)))`.

Strings: `lunar_category_note` = `"Tailored for: %1$s"` / `"Подобрано для: %1$s"`; `lunar_cat_fruiting` `"fruiting plants"`/`"плодовых"`; `lunar_cat_root` `"root crops"`/`"корнеплодов"`; `lunar_cat_leafy` `"leafy greens"`/`"зелени"`; `lunar_cat_flowering` `"flowering plants"`/`"цветущих"`; `lunar_cat_succulent` `"succulents and cacti"`/`"суккулентов и кактусов"`; `lunar_cat_foliage` `"foliage houseplants"`/`"декоративно-лиственных"`; `lunar_cat_herb` `"herbs"`/`"пряных трав"`.

> Out of scope: `PlantDetailActivity` (the *garden* plant screen) has no lunar section. Do not add one this pass.

## A2. Year bounds (Jan 2026 – Dec 2027)

```kotlin
val MIN_MONTH: YearMonth = YearMonth.of(2026, 1)
val MAX_MONTH: YearMonth = YearMonth.of(2027, 12)
val MIN_DATE: LocalDate = MIN_MONTH.atDay(1)
val MAX_DATE: LocalDate = MAX_MONTH.atEndOfMonth()

fun clampMonth(m: YearMonth) = minOf(maxOf(m, MIN_MONTH), MAX_MONTH)
fun clampDate(d: LocalDate) = when {
    d.isBefore(MIN_DATE) -> MIN_DATE
    d.isAfter(MAX_DATE)  -> MAX_DATE
    else -> d
}
fun isInRange(d: LocalDate) = !d.isBefore(MIN_DATE) && !d.isAfter(MAX_DATE)
```

`upcomingFavourable` must clamp too, else it proposes dates the grid cannot reach:

```kotlin
generateSequence(clampDate(from).plusDays(1)) { it.plusDays(1) }
    .takeWhile { !it.isAfter(MAX_DATE) }
    .take(90)
    .map { dayInfo(it, category) }
    .filter { it.rating == Rating.GOOD }
    .take(limit).toList()
```

In `LunarCalendarView`:
```kotlin
private val today = LocalDate.now()
private val todayInRange = LunarCalendar.isInRange(today)
private val anchor = LunarCalendar.clampDate(today)
private var shownMonth = YearMonth.from(anchor)
private var selected = anchor
```

Arrows clamp, then `renderGrid(); updateNavArrows()`. `updateNavArrows()` sets **both** `isEnabled` and `isClickable` (some ROMs still deliver clicks to a disabled `ImageView`) plus `alpha = if (can) 1f else 0.28f`.

**Device clock outside range:** do not blank the UI. Render against `anchor`; use `R.string.lunar_today` only when `selected == today && todayInRange`, else a plain date title; never show the today dot; show `@id/lunar_range_note` with `lunar_out_of_range` = `"This calendar covers 2026–2027."` / `"Календарь охватывает 2026–2027 годы."`

## A3. Selected day + inline detail panel

### A3.1 The three-state problem
`today`, `selected` and `rating` are orthogonal. Current code collapses them into one branch. **Solution: one view per state** in a composed cell.

New `res/layout/item_lunar_day_cell.xml` — 36dp `FrameLayout`, clickable/focusable, containing:
`View @+id/lunar_cell_fill` (match_parent) · `View @+id/lunar_cell_ring` (match_parent, `@drawable/bg_lunar_day_selected`, gone) · `TextView @+id/lunar_cell_day` (match_parent, centre, `@dimen/text_body`) · `View @+id/lunar_cell_today_dot` (4dp, bottom|center_horizontal, marginBottom 3dp, `@drawable/bg_lunar_today_dot`, gone).

New drawables: `bg_lunar_day_selected.xml` (oval, transparent solid + 2dp `@color/primary` stroke); `bg_lunar_today_dot.xml` (oval, solid `@color/primary`).
**Delete** `bg_lunar_day_today.xml` — grep first; the only reference is `buildDayCell`.

### A3.2 `view_lunar_calendar.xml` restructure
Collapse to **two cards**. The standalone top "today" card is deleted — its being above the fold is precisely why switching read as dead.

**Card 1** (`App.GlassCard`, `cardBackgroundColor="@color/surface"`), vertical:
1. header row `@+id/lunar_prev` · `@+id/lunar_month_label` · `@+id/lunar_next` *(ids unchanged)*
2. `@+id/lunar_dow_row` *(unchanged)*
3. `GridLayout @+id/lunar_grid` columnCount 7 *(unchanged)*
4. **new** 1dp `@color/divider`, marginTop `@dimen/space_lg`
5. **new** `LinearLayout @+id/lunar_day_panel` (vertical, marginTop `space_lg`):
   `TextView @+id/lunar_today_title` *(id kept, relocated)* · `TextView @+id/lunar_category_note` *(new)* · `TextView @+id/lunar_range_note` *(new, gone)* · row of `MoonPhaseView @+id/lunar_moon` 64dp *(kept)* + column of `@+id/lunar_phase_name` *(kept)* and row of `ImageView @+id/lunar_sign_icon` (20dp, tint primary) + `TextView @+id/lunar_sign` *(kept)* · `@+id/lunar_good_label` + `@+id/lunar_good_tasks` *(kept)* · `@+id/lunar_avoid_label` + `@+id/lunar_avoid_tasks` *(kept)*
6. `@+id/lunar_legend` *(unchanged)*

**Card 2** — `@+id/lunar_upcoming_list` unchanged. Add `ImageView @+id/lunar_row_sign_icon` (20dp, marginEnd `space_sm`, tint primary) to `item_lunar_favourable_day.xml` between the dot and the text column.

All ids preserved; only tree position changes, so existing `findViewById` calls keep working.

### A3.3 Render flow

```kotlin
private fun renderAll() {
    renderGrid(); renderPanel(selected, animateMoon = false)
    renderLegend(); renderUpcoming(); updateNavArrows(); hasRendered = true
}

private fun buildDayCell(date: LocalDate, inMonth: Boolean): View {
    val cell = LayoutInflater.from(context).inflate(R.layout.item_lunar_day_cell, grid, false)
    cell.layoutParams = GridLayout.LayoutParams().apply {
        width = dp(36); height = dp(36); setMargins(dp(2), dp(2), dp(2), dp(2))
    }
    val day = cell.findViewById<TextView>(R.id.lunar_cell_day)
    day.text = date.dayOfMonth.toString()
    if (!inMonth || !LunarCalendar.isInRange(date)) {
        cell.findViewById<View>(R.id.lunar_cell_fill).background = null
        day.setTextColor(context.getColor(R.color.on_surface_secondary))
        cell.alpha = 0.35f; cell.isClickable = false; cell.isFocusable = false
        return cell
    }
    val rating = LunarCalendar.rating(date, category)
    cell.findViewById<View>(R.id.lunar_cell_fill).setBackgroundResource(dayBackgroundRes(rating))
    day.setTextColor(context.getColor(dayForegroundRes(rating)))
    cell.findViewById<View>(R.id.lunar_cell_today_dot).visibility =
        if (todayInRange && date == today) View.VISIBLE else View.GONE
    cell.findViewById<View>(R.id.lunar_cell_ring).visibility =
        if (date == selected) View.VISIBLE else View.GONE
    cell.tag = date
    cell.contentDescription = accessibleDayLabel(date, rating)
    cell.setOnClickListener { selectDate(date) }
    return cell
}

/** Single entry point for selection — grid AND upcoming rows. */
private fun selectDate(date: LocalDate, scrollToPanel: Boolean = false) {
    val target = LunarCalendar.clampDate(date)
    if (target == selected && hasRendered && !scrollToPanel) return
    selected = target
    val month = YearMonth.from(target)
    if (month != shownMonth) { shownMonth = month; renderGrid(); updateNavArrows() }
    else updateSelectionRings()
    renderPanel(target, animateMoon = true)
    if (scrollToPanel) onRequestScrollTo?.invoke(dayPanel)
}

/** O(42) ring toggle, no re-inflation — the ring visibly follows the tap. */
private fun updateSelectionRings() {
    for (i in 0 until grid.childCount) {
        val cell = grid.getChildAt(i)
        val date = cell.tag as? LocalDate ?: continue
        cell.findViewById<View>(R.id.lunar_cell_ring).visibility =
            if (date == selected) View.VISIBLE else View.GONE
    }
}
```

`renderPanel(date, animateMoon)` sets the title (today vs plain), category note, `moonView.setCycle(LunarCalendar.cyclePosition(date).toFloat(), animate = animateMoon)`, phase name, `lunar_sign_icon` → `zodiacIconRes(...)`, `lunar_sign` → **localized name only**, and the two task rows via the existing `fillTaskRow`.

`renderUpcoming()` uses `upcomingFavourable(anchor, 4, category)`, sets the dot from `dayBackgroundRes(info.rating)`, sets the sign icon, and:
```kotlin
row.setOnClickListener { selectDate(info.date, scrollToPanel = true) }
```

### A3.4 Scroll-to
`LunarCalendarView` does not own its scroller — expose a callback, never walk the parent chain:
```kotlin
var onRequestScrollTo: ((View) -> Unit)? = null
```
In `SpeciesDetailActivity`:
```kotlin
private fun scrollSpeciesTo(target: View) {
    binding.speciesScroll.post {
        val t = IntArray(2); target.getLocationOnScreen(t)
        val s = IntArray(2); binding.speciesScroll.getLocationOnScreen(s)
        val margin = (24 * resources.displayMetrics.density).toInt()
        binding.speciesScroll.smoothScrollBy(0, t[1] - s[1] - margin)
    }
}
```

## A4. `MoonPhaseView` rewrite

### A4.1 API
Replace `illumination` + `waxing` with one normalized synodic position — this makes the maths one `cos` and makes animation across the new/full seam and the side-flip fall out for free.
```kotlin
var cycle: Float = 0.5f; private set          // 0=new, .25=first quarter, .5=full, .75=last quarter
fun setCycle(value: Float, animate: Boolean = false)
```
Add `fun cyclePosition(date: LocalDate): Double = moonAge(date) / SYNODIC_MONTH` to `LunarCalendar`.

### A4.2 Correct terminator maths
The terminator is a great circle in projection ⇒ a **semi-ellipse** sharing the disc's vertical semi-axis `R`, horizontal semi-axis `a = R·|cos(2π·cycle)|`. Let `f = cos(2π·cycle)`.

Cardinal checks (the current code fails these — verify by hand before moving on):
`cycle=0` → `f=+1`, `a=R`, bulge fully into the lit half ⇒ unlit disc ✓ · `cycle=.25` → `f=0` ⇒ straight line, right half lit ✓ · `cycle=.5` → `f=−1` ⇒ bulge fully away ⇒ full disc ✓ · `cycle=.75` → `f=0`, mirrored ⇒ left half lit ✓

```kotlin
override fun onDraw(canvas: Canvas) {
    val r = (min(width, height) / 2f) - strokeInset
    val cx = width / 2f; val cy = height / 2f
    disc.set(cx - r, cy - r, cx + r, cy + r)
    canvas.drawCircle(cx, cy, r, glowPaint)
    canvas.drawCircle(cx, cy, r, darkPaint)

    val save = canvas.save()
    if (cycle >= 0.5f) canvas.scale(-1f, 1f, cx, cy)      // mirror for waning

    val f = cos(2.0 * PI * cycle).toFloat()
    val a = max(abs(f) * r, 0.01f)
    term.set(cx - a, cy - r, cx + a, cy + r)

    litPath.reset()
    litPath.arcTo(disc, -90f, 180f)                        // right limb 12→3→6
    litPath.arcTo(term, 90f, if (f >= 0f) -180f else 180f) // terminator back 6→12
    litPath.close()

    canvas.drawPath(litPath, litPaint)
    canvas.save(); canvas.clipPath(litPath)
    drawMaria(canvas, cx, cy, r)
    canvas.restore()
    canvas.restoreToCount(save)
}
```
`-180f` sweeps CCW through 0° ⇒ right half of the ellipse (crescent). `+180f` sweeps CW through 180° ⇒ left half (gibbous). **This sign is the exact bug in the current implementation.**

### A4.3 Realism (cheap, deterministic)
Build all shaders in `onSizeChanged`, never `onDraw`.
- Colours (both `values/` and `values-night/`): `moon_lit_hi` `#FFFDF6`/`#F2EFE6`; `moon_lit_lo` `#D6D2C6`/`#B9B4A6`; `moon_dark` `#242B31`/`#161B20`; `moon_mare` `#8E8A7E`/`#7A766B`; `moon_glow` `#33FFFFFF`/`#22FFFFFF`.
- `litPaint` = `RadialGradient(cx-0.28f*r, cy-0.28f*r, 1.35f*r, moon_lit_hi, moon_lit_lo, CLAMP)` — off-centre highlight reads as a sphere.
- `glowPaint` = `RadialGradient(cx, cy, 1.22f*r, moon_glow, TRANSPARENT, CLAMP)`, drawn first.
- Maria: fixed table, `moon_mare` at alpha 26, clipped to the lit path — deterministic, no randomness:
  `(-0.30,-0.28,0.26) (0.10,-0.42,0.16) (0.26,-0.10,0.22) (-0.12,0.14,0.30) (0.34,0.30,0.18) (-0.40,0.34,0.14) (0.02,0.48,0.12)`
- Soft edge: **do not** use `BlurMaskFilter` (forces a software layer every frame). Stroke `litPath` 1 px in `moon_lit_lo` at alpha 90.

### A4.4 Animation
**Phase sweep** on day change — shortest arc across the 0/1 seam:
```kotlin
fun setCycle(value: Float, animate: Boolean = false) {
    val target = ((value % 1f) + 1f) % 1f
    phaseAnimator?.cancel(); phaseAnimator = null
    if (!animate || !isAttachedToWindow || width == 0) { cycle = target; invalidate(); return }
    val end = when {
        target - cycle > 0.5f -> target - 1f
        cycle - target > 0.5f -> target + 1f
        else -> target
    }
    phaseAnimator = ValueAnimator.ofFloat(cycle, end).apply {
        duration = 520L
        interpolator = PathInterpolator(0.2f, 0f, 0f, 1f)
        addUpdateListener { cycle = normalize(it.animatedValue as Float); invalidate() }
        start()
    }
}
```
**Idle libration** — ±1.6° rotation applied around `drawMaria` **only**, so the terminator never wobbles (a wobbling terminator reads as broken, not alive): `ValueAnimator.ofFloat(-1f, 1f)`, 9 000 ms, `INFINITE`, `REVERSE`, linear.

**Lifecycle — implement both hooks, this is where leaks and battery burn live:**
```kotlin
override fun onVisibilityAggregated(isVisible: Boolean) {
    super.onVisibilityAggregated(isVisible)
    if (isVisible) startIdle() else { stopIdle(); phaseAnimator?.cancel(); phaseAnimator = null }
}
override fun onDetachedFromWindow() {
    stopIdle(); phaseAnimator?.cancel(); phaseAnimator = null
    super.onDetachedFromWindow()
}
```
`onVisibilityAggregated` accounts for **ancestor** visibility — essential because the lunar section starts collapsed (`body.visibility = GONE`), so the idle animator correctly stays stopped until expanded. `onWindowVisibilityChanged` alone misses that case.

Keep `DayInfo.illumination` / `isWaxing` on the model — removing them is gratuitous churn.

## A5. Zodiac vector drawables

12 files `res/drawable/ic_zodiac_<sign>.xml` (lowercase enum name). Shared template — stroke-based so they tint cleanly at 20dp:
```xml
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp" android:height="24dp"
    android:viewportWidth="24" android:viewportHeight="24">
    <path android:fillColor="@android:color/transparent" android:strokeColor="#FF000000"
        android:strokeWidth="1.6" android:strokeLineCap="round" android:strokeLineJoin="round"
        android:pathData="…" />
</vector>
```

| file | pathData |
|---|---|
| aries | `M4,16 C4,10 6,7 8.5,7 C11,7 12,9.5 12,13 M20,16 C20,10 18,7 15.5,7 C13,7 12,9.5 12,13` |
| taurus | `M6,4 C6,8 8.6,10.5 12,10.5 C15.4,10.5 18,8 18,4 M17,16 A5,5 0 1,1 7,16 A5,5 0 1,1 17,16` |
| gemini | `M6,4 C9,6 15,6 18,4 M6,20 C9,18 15,18 18,20 M9,5 L9,19 M15,5 L15,19` |
| cancer | `M3.5,10 C6,6.5 11,6 13.5,8.5 M20.5,14 C18,17.5 13,18 10.5,15.5 M8,10.5 m-2.2,0 a2.2,2.2 0 1,0 4.4,0 a2.2,2.2 0 1,0 -4.4,0 M16,13.5 m-2.2,0 a2.2,2.2 0 1,0 4.4,0 a2.2,2.2 0 1,0 -4.4,0` |
| leo | `M8,17 a3.4,3.4 0 1,1 4.6,-3.2 C10.5,11 9.5,7 12,5.2 C14.3,3.6 17,5 17,7.6 C17,9.6 16,11 16,13 C16,15.6 17.6,17.4 20,17.6` |
| virgo | `M4,7 C5.6,7 6,8.4 6,10 L6,17 M6,9 C7.6,7 9.8,7 10,10 L10,17 M10,9 C11.6,7 13.8,7 14,10 L14,15.5 C14,18.5 16,20 18.5,20 M14,13 C14,10.5 16,9 18,9.6 C20.6,10.4 21,14 18.6,16.6 C17,18.4 15,19.4 12.6,20` |
| libra | `M3,19 L21,19 M3,14.5 L8.5,14.5 M15.5,14.5 L21,14.5 M8.5,14.5 A4,4 0 1,1 15.5,14.5` |
| scorpio | `M3,7 C4.6,7 5,8.4 5,10 L5,17 M5,9 C6.6,7 8.8,7 9,10 L9,17 M9,9 C10.6,7 12.8,7 13,10 L13,17 C13,19 14.5,20.4 16.6,20.4 L20.6,20.4 M17.6,17.6 L20.6,20.4 L17.6,23` |
| sagittarius | `M5,19 L19,5 M12.5,5 L19,5 L19,11.5 M8,12 L13,17` |
| capricorn | `M3,7 C4.6,7 5,8.4 5,10 L5,17 M5,9 C7,6.6 10.6,7 11.6,10.6 L13.4,17 M13,12.6 C15.6,11 18.6,12.4 18.6,15.2 C18.6,17.6 16.6,19 14.6,18.4 C13,17.9 12.4,16 13.4,14.6` |
| aquarius | `M3,10 L6.5,7 L10,10 L13.5,7 L17,10 L20.5,7 M3,17 L6.5,14 L10,17 L13.5,14 L17,17 L20.5,14` |
| pisces | `M6,4 C3,8 3,16 6,20 M18,4 C21,8 21,16 18,20 M4,12 L20,12` |

Then:
1. **Delete the `glyph` parameter** from `LunarCalendar.Zodiac` → `enum class Zodiac { ARIES, TAURUS, … }`. Compile-breaking: land it in the **same edit** as both `LunarCalendarView` usages (`renderPanel` and `renderUpcoming`).
2. Add `@DrawableRes private fun zodiacIconRes(z: Zodiac): Int`, mirroring the existing `zodiacStringRes` when-block.
3. Bind at `lunar_sign_icon` and `lunar_row_sign_icon`; the TextViews carry the localized name only.

> These are clean geometric approximations, not licensed typographic glyphs. If real SVGs turn up later, swap only `pathData` — filenames, viewport and binding stay identical.

## A6. Section expand/collapse animation

Replace `AutoTransition` in `ExpandableSectionView.setExpanded` with a measured-height `ValueAnimator`. `AutoTransition` re-lays-out the whole `NestedScrollView` subtree, cannot cross-fade the body, and pairs badly with the instant `chevron.rotation` snap.

```kotlin
private companion object { const val EXPAND_MS = 300L; const val COLLAPSE_MS = 240L }
private val expandInterp = PathInterpolator(0.05f, 0.7f, 0.1f, 1.0f)    // M3 emphasized decelerate
private val collapseInterp = PathInterpolator(0.3f, 0.0f, 0.8f, 0.15f)  // M3 emphasized accelerate
private val chevronInterp = PathInterpolator(0.2f, 0f, 0f, 1f)
private var bodyAnimator: ValueAnimator? = null

fun setExpanded(expanded: Boolean, animate: Boolean = true) {
    if (expanded == isExpanded) return
    isExpanded = expanded
    bodyAnimator?.cancel(); bodyAnimator = null
    updateAccessibility(); onToggle?.invoke(expanded)

    val chevronTarget = if (expanded) 180f else 0f
    val canAnimate = animate && isAttachedToWindow && width > 0
    if (!canAnimate) {
        body.visibility = if (expanded) View.VISIBLE else View.GONE
        body.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
        body.alpha = 1f; chevron.rotation = chevronTarget
        return
    }
    chevron.animate().rotation(chevronTarget)
        .setDuration(if (expanded) EXPAND_MS else COLLAPSE_MS)
        .setInterpolator(chevronInterp).start()

    val from: Int; val to: Int
    if (expanded) {
        body.visibility = View.VISIBLE
        val wSpec = MeasureSpec.makeMeasureSpec(
            (width - paddingLeft - paddingRight).coerceAtLeast(0), MeasureSpec.EXACTLY)
        body.measure(wSpec, MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED))
        from = 0; to = body.measuredHeight
    } else { from = body.height; to = 0 }

    bodyAnimator = ValueAnimator.ofInt(from, to).apply {
        duration = if (expanded) EXPAND_MS else COLLAPSE_MS
        interpolator = if (expanded) expandInterp else collapseInterp
        addUpdateListener { a ->
            body.layoutParams.height = a.animatedValue as Int
            body.alpha = if (expanded) a.animatedFraction else 1f - a.animatedFraction
            body.requestLayout()
        }
        addListener(object : AnimatorListenerAdapter() {
            override fun onAnimationEnd(animation: Animator) {
                bodyAnimator = null
                body.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
                body.alpha = 1f
                if (!expanded) body.visibility = View.GONE
                body.requestLayout()
            }
        })
        start()
    }
}
```
- Imports: `android.animation.{Animator, AnimatorListenerAdapter, ValueAnimator}`, `android.view.animation.PathInterpolator`. **Remove** both `android.transition.*` imports.
- If `to == 0` on expand (empty body), skip the animator and jump to the terminal state.
- Add `onDetachedFromWindow` cancelling `bodyAnimator` and `chevron.animate()`.
- In `SpeciesDetailActivity.attachSections`, change the scroll to `smoothScrollTo(0, (section.top - 8.dp).coerceAtLeast(0))`, keeping it inside the existing `post { }`.

---

# EPIC B — Garden refactor

## B1. Schema 5 → 6

All in `Database.kt` (the file's "everything in one file" convention). **Do not create new files under `data/local/`.**

### B1.1 On re-adding rooms — read this first
`MIGRATION_4_5` dropped a free-text `room TEXT` column. That design failed predictably: no canonical list, so `"Kitchen"`, `"kitchen"`, `"Кухня"` fragment into three groups; no rename; no ordering; no delete.

**We are not re-adding that column.** Instead: a first-class `rooms` table + nullable FK on `plants` — rename is one UPDATE, delete is `ON DELETE SET NULL`, ordering is explicit, grouping is integer equality. **Put this justification verbatim in the `MIGRATION_5_6` KDoc so the next person does not revert it again.**

### B1.2 New entities

```kotlin
@Entity(tableName = "rooms")
data class RoomEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "name") val name: String,
    @ColumnInfo(name = "icon") val icon: String? = null,
    @ColumnInfo(name = "sort_order") val sortOrder: Int = 0,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
)
```
`PlantEntity` gains **one trailing defaulted field** `@ColumnInfo(name = "room_id") val roomId: Long? = null`, plus on its `@Entity`: `foreignKeys = [ForeignKey(RoomEntity::class, ["id"], ["room_id"], onDelete = SET_NULL)]` and `indices = [Index("created_at"), Index("room_id")]`.

```kotlin
@Entity(tableName = "plant_care_tasks",
    foreignKeys = [ForeignKey(PlantEntity::class, ["id"], ["plant_id"], onDelete = CASCADE)],
    indices = [Index(value = ["plant_id","step_key"], unique = true),
               Index(value = ["plant_id","done_at"]), Index(value = ["due_at"])])
data class PlantCareTaskEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "plant_id") val plantId: Long,
    /** Soft reference — deliberately NOT a FK; scans are user-deletable and
     *  losing the checklist along with them is wrong. */
    @ColumnInfo(name = "scan_id") val scanId: Long? = null,
    @ColumnInfo(name = "step_key") val stepKey: String,
    @ColumnInfo(name = "source") val source: String,        // treatment | sanitation | checkpoint
    @ColumnInfo(name = "ordinal") val ordinal: Int = 0,
    @ColumnInfo(name = "text") val text: String,
    @ColumnInfo(name = "locale") val locale: String = "en",
    @ColumnInfo(name = "due_at") val dueAt: Long? = null,   // NULL = do now
    @ColumnInfo(name = "done_at") val doneAt: Long? = null,
    @ColumnInfo(name = "dismissed_at") val dismissedAt: Long? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "disease_key") val diseaseKey: String = "unknown",
)
```

### B1.3 `MIGRATION_5_6`

```kotlin
val MIGRATION_5_6 = object : Migration(5, 6) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("""
            CREATE TABLE IF NOT EXISTS `rooms` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `name` TEXT NOT NULL, `icon` TEXT,
                `sort_order` INTEGER NOT NULL DEFAULT 0,
                `created_at` INTEGER NOT NULL)
        """.trimIndent())
        // ADD COLUMN ... REFERENCES is legal because the default is NULL. This
        // avoids the drop-and-rebuild of MIGRATION_4_5, which is now risky on
        // `plants` given four child tables carry FKs back to it.
        db.execSQL("ALTER TABLE `plants` ADD COLUMN `room_id` INTEGER " +
            "REFERENCES `rooms`(`id`) ON UPDATE NO ACTION ON DELETE SET NULL")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_plants_room_id` ON `plants` (`room_id`)")
        db.execSQL("""
            CREATE TABLE IF NOT EXISTS `plant_care_tasks` (
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `plant_id` INTEGER NOT NULL, `scan_id` INTEGER,
                `step_key` TEXT NOT NULL, `source` TEXT NOT NULL,
                `ordinal` INTEGER NOT NULL, `text` TEXT NOT NULL,
                `locale` TEXT NOT NULL, `due_at` INTEGER, `done_at` INTEGER,
                `dismissed_at` INTEGER, `created_at` INTEGER NOT NULL,
                `disease_key` TEXT NOT NULL,
                FOREIGN KEY(`plant_id`) REFERENCES `plants`(`id`)
                    ON UPDATE NO ACTION ON DELETE CASCADE)
        """.trimIndent())
        db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS `index_plant_care_tasks_plant_id_step_key` ON `plant_care_tasks` (`plant_id`, `step_key`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_plant_care_tasks_plant_id_done_at` ON `plant_care_tasks` (`plant_id`, `done_at`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_plant_care_tasks_due_at` ON `plant_care_tasks` (`due_at`)")
    }
}
```
Then `version = 6`, add both entities, add `abstract fun roomDao(): RoomDao` / `plantCareTaskDao(): PlantCareTaskDao`, and register `MIGRATION_5_6`.

**Gotchas:** Room's `TableInfo` compares FK `onDelete` **and exact index names** — the names above match Room's `index_<table>_<col>…` convention, do not rename. Keep SQL nullability in sync with Kotlin (`disease_key` is NOT NULL / non-null `String`). Do **not** declare `@ColumnInfo(defaultValue = …)` — leaving it off keeps both sides simple.

## B2. Stable identity for care-plan treatment steps

The hardest problem here. The server returns `treatment: List[str]` — flat, localized, order not guaranteed across locales, text regenerated by `_polish_plan_with_llm` on every call, no ids. Hashing fails on the first reword; array index fails on the first reorder.

**Design: identity = (diseaseKey, intent slug, occurrence index), with a hashed fallback.**

New `util/CareStepIntent.kt`:
```kotlin
object CareStepIntent {
    enum class Slug(val key: String) {
        ISOLATE("isolate"), TREAT_INSECTICIDE("treat_insecticide"),
        TREAT_FUNGICIDE("treat_fungicide"), TREAT_SOAP("treat_soap"),
        SANITIZE_TOOLS("sanitize_tools"), SOIL_REPLACE("soil_replace"),
        REPOT("repot"), REMOVE_AFFECTED("remove_affected"), PRUNE("prune"),
        RINSE("rinse"), ADJUST_WATERING("adjust_watering"),
        ADJUST_HUMIDITY("adjust_humidity"), ADJUST_LIGHT("adjust_light"),
        ADJUST_AIRFLOW("adjust_airflow"), FEED("feed"), MONITOR("monitor"), OTHER("other"),
    }
    fun normalize(text: String): String          // lowercase, ё→е, non-letter → space, collapse
    fun slugOf(text: String): Slug               // first slug in declaration order whose vocab matches
    fun textDigest(text: String): String         // 8 hex chars of normalize(text)
    fun stepKey(diseaseKey: String, slug: Slug, occurrence: Int, text: String): String =
        if (slug == Slug.OTHER) "$diseaseKey|other|${textDigest(text)}"
        else "$diseaseKey|${slug.key}|$occurrence"
    fun checkpointKey(diseaseKey: String, day: Int) = "$diseaseKey|checkpoint|$day"
}
```
**`Slug` declaration order is match priority** — most specific first. "wash the leaves with soapy water" hits both RINSE and TREAT_SOAP and must resolve to TREAT_SOAP. Matching rule is token `startsWith(stem)`, min stem 4, same discipline as §A1.

| slug | stems (EN + RU) |
|---|---|
| ISOLATE | `isolat, quarantin, separat, изолир, каранти, отдели, отсади` |
| TREAT_INSECTICIDE | `insectic, acaricid, pyrethr, neem, imidaclo, инсектиц, акарицид, пиретр` |
| TREAT_FUNGICIDE | `fungicid, copper, chlorothalonil, sulfur, sulphur, bordeaux, фунгицид, медьсод, бордос, сера, серой` |
| TREAT_SOAP | `soap, soapy, alcohol, мыльн, мылом, спирт` |
| SANITIZE_TOOLS | `steriliz, sterilis, sanitiz, sanitis, disinfect, blade, shear, scissor, стерилиз, дезинф, инструмент, лезви, ножниц, секатор` |
| SOIL_REPLACE | `substrate, potting, soil, грунт, субстрат, земл, почв` |
| REPOT | `repot, replant, переса, пересад, горшок` |
| REMOVE_AFFECTED | `remove, discard, dispose, destroy, удали, удале, уничтож, выброс, утилиз, оборв` |
| PRUNE | `prune, prunin, trim, pinch, cutback, обрезк, обреж, подрезк, прищип` |
| RINSE | `rinse, wash, shower, hose, промы, смой, обмой, душ, ополосн` |
| ADJUST_WATERING | `water, waterin, overwater, underwater, drain, drainag, полив, залив, пересуш, дренаж` |
| ADJUST_HUMIDITY | `humid, humidif, mist, tray, влажн, опрыск, увлажнит, поддон` |
| ADJUST_LIGHT | `light, sunlight, shade, window, свет, освещ, солнц, тень, притен, окно` |
| ADJUST_AIRFLOW | `airflo, ventilat, circulat, breeze, воздух, проветр, циркуляц, обдув` |
| FEED | `fertil, feedin, nutrien, подкорм, удобр, питани` |
| MONITOR | `monitor, inspect, observ, recheck, наблюд, провер, осмотр, следит, контрол` |
| OTHER | (no stems — fallback) |

### B2.2 Reconciliation
`PlantRepository.syncCareTasks(plantId, scanId, diseaseKey, plan, locale, nowMs)` runs on every successful `/api/care-plan` for a plant that has a scan:

```
batchKeys = mutableSetOf<String>(); counters = mutableMapOf<Slug, Int>()

fun emit(source, index, text, dueAt) {
    slug = slugOf(text)
    n = counters.getOrDefault(slug, 0); counters[slug] = n + 1
    key = stepKey(diseaseKey, slug, n, text); batchKeys += key
    existing = dao.getByPlantAndKey(plantId, key)
    if (existing == null) dao.insert(...)
    else dao.refresh(existing.id, text, index, locale, dueAt)   // done_at PRESERVED
}
plan.treatment?.filter{isNotBlank}?.forEachIndexed { i,t -> emit("treatment", i, t, null) }
plan.sanitation?.filter{isNotBlank}?.forEachIndexed { i,t -> emit("sanitation", 100+i, t, null) }
plan.checkpoints?.filter{check.isNotBlank}?.forEach { cp ->
    key = checkpointKey(diseaseKey, cp.day); batchKeys += key
    // Anchor on the SCAN timestamp, not now — otherwise every re-fetch pushes
    // the checkpoint further out and it never becomes due.
    due = (scanTimestampMs ?: nowMs) + cp.day * CareSchedule.DAY_MS
    if (existing == null) insert(source="checkpoint", ordinal=200+cp.day, dueAt=due, ...)
    else dao.refresh(existing.id, cp.check, 200+cp.day, locale, existing.dueAt)  // keep ORIGINAL due
}
dao.deleteStaleForDisease(plantId, diseaseKey, batchKeys.toList())
```
**One `counters` map for the whole batch** across treatment + sanitation — per-source counters would let a treatment `prune|0` and a sanitation `prune|0` collide under the UNIQUE index and crash the insert.

`deleteStaleForDisease` removes only rows for **this** disease that are absent from the batch **and never completed** — completed rows are kept as history. Rows for other `disease_key`s stay but are never displayed (the UI filters on the current verdict); archive them via `archiveOtherDiseases(plantId, currentKey, now)` called from `syncCareTasks` only when `ScanRepository.isHealthyVerdict(latestScan)`.

**Documented limitation (put in the KDoc):** a step classified `OTHER` re-binds only on identical normalized text; a reword loses its tick. That is the honest trade — fuzzy matching would silently mis-bind unrelated steps, which is worse.

New `CareStepIntentTest.kt`: soapy-wash → TREAT_SOAP (not RINSE), EN and RU; isolate EN/RU → ISOLATE; EN and RU renderings of the same intent produce the **same** `stepKey`; two pruning steps → `prune|0`, `prune|1`; nonsense → OTHER with a stable key across two calls.

## B3. Today queue + the N+1 fix

### B3.1 New DAO methods (all in `Database.kt`)
- `PlantDao.setRoom(plantId, roomId)`.
- `ScanDao.latestScanPerPlant()` — correlated `MAX(timestamp)` subquery, **not `MAX(id)`**; `scanCountPerPlant()`; `recentScansForPlant(plantId, limit)`.
- `PlantJournalDao.latestPerPlantAndType()` → `JournalAnchor(plantId, type, timestamp)`; `latestPerPlant()`.
- `PlantReminderDao.getAllEnabled()`.
- `PlantReminderEventDao.lastDonePerPlantAndType()` → `DoneAnchor`; `latestPendingFor(reminderId)`; `expireStalePending(cutoffMs, nowMs)`; `actionCountsSince(plantId, sinceMs)` → `ActionCount`.
- `RoomDao`: `getAll()` (ORDER BY sort_order, name), `insert`, `update`, `deleteById`, `findByName(name)` `COLLATE NOCASE`.
- `PlantCareTaskDao`: `getByPlantAndKey`, `forPlantAndDisease`, `openTasks(nowMs)` (`done_at IS NULL AND dismissed_at IS NULL AND (due_at IS NULL OR due_at <= :nowMs)`), `openCountPerPlant`, `insert`, `refresh`, `markDone`, `markUndone`, `archiveOtherDiseases`, `deleteStaleForDisease`.

### B3.2 Rewrite `PlantRepository.getPlants()`
Currently `3N + 1` queries; a cross-plant queue on top would be `O(N)` again. Fix at the DAO level: **8 batch queries, constant in plant count.** Build maps from `latestScanPerPlant`, `scanCountPerPlant`, `latestPerPlant`, `latestPerPlantAndType`, `lastDonePerPlantAndType`, `getAllEnabled`, `openCountPerPlant`, `roomDao.getAll()`, then `plants.map { … }`.

`PlantSummary` gains **two trailing defaulted fields**: `room: RoomEntity? = null`, `openCareTasks: Int = 0`.

**Also fix `soonestReminderDueAt` while here.** It currently `mapNotNull`s away `check`-type reminders (because `reminderTypeToJournalType("check") == null`), which is why they never produce a chip. New anchor rule — never drop a reminder:
```
anchor = max(journalAnchor(type)?.timestamp ?: 0L,
             doneAnchor(reminder.type)?.doneAt ?: 0L,   // works for EVERY type incl. "check"
             plantCreatedAt)
dueAt = CareSchedule.nextDueAt(anchor, reminder.intervalDays)
```
**Behaviour change to call out in the commit message:** plants with only a `check` reminder will start showing a next-action chip and can appear in "Needs attention". That is the correct behaviour and was the bug.

### B3.3 Queue model
```kotlin
enum class QueueKind { REMINDER, CARE_TASK }
data class CareQueueItem(
    val kind: QueueKind, val plantId: Long, val plantName: String,
    val plantThumbUri: String?,                 // coverPhotoUri ?: latestScan.imagePath
    val reminderId: Long = 0L, val reminderType: String = "",
    val careTaskId: Long = 0L, val label: String,
    val dueAt: Long, val overdue: Boolean,
)
suspend fun getTodayQueue(nowMs: Long = System.currentTimeMillis(), limit: Int = 50): List<CareQueueItem>
```
Implementation, one `withContext(Dispatchers.IO)`, reusing the same batch queries:
1. `expireStalePending(cutoffMs = nowMs - 7 * DAY_MS, nowMs)` — **this is the fix for "dismissing the notification strands the event as pending forever"**. Cutoff is 7 days, **not** `now`, or legitimately-pending same-day events get marked skipped and tank the adherence score.
2. Build `plantsById` + thumbnails.
3. Reminders: compute `dueAt` via a shared extracted `dueAtFor(...)`; keep those `<= endOfTodayMs(nowMs)`.
4. Care tasks: `openTasks(nowMs)` → `dueAt = due_at ?: created_at`.
5. Sort `overdue` first, then `dueAt` asc, then `plantName`; `take(limit)`.

Add `CareSchedule.endOfDayMs(nowMs, cal)` so it is unit-tested with the rest of the date maths.

### B3.4 One-tap Done
`ReminderManager` needs a `Context` the repository lacks — split it:
```kotlin
suspend fun completeReminderNow(reminderId: Long, dueAt: Long, nowMs: Long = …): PlantReminderEntity?
suspend fun completeCareTask(taskId: Long, nowMs: Long = …)
suspend fun uncompleteCareTask(taskId: Long)
```
`completeReminderNow` resolves (or synthesises then resolves) the pending event and **reuses the existing `resolveReminderEvent(id, "done")`** so the journal back-fill happens exactly as from the notification — do not duplicate that logic. Caller (which has a `Context`) then calls `ReminderManager.rescheduleAfterCare(context, reminder, plantName)`; check its exact signature at `util/ReminderManager.kt:67` before wiring.

## B4. Health heuristic

New `util/PlantHealth.kt` — pure, **all thresholds `const val` at the top, the single tunable place**:
```kotlin
enum class State { UNKNOWN, HEALTHY, RECOVERING, DECLINING, CRITICAL }
const val TREND_WINDOW = 4; const val STALE_SCAN_DAYS = 45L
const val ADHERENCE_POOR = 0.40; const val ADHERENCE_GOOD = 0.70
const val OVERDUE_DOWNGRADE_COUNT = 2
const val SCORE_HEALTHY = 2; const val SCORE_LOW = -1
const val SCORE_MEDIUM = -2; const val SCORE_HIGH = -3
const val CRITICAL_THRESHOLD = -3; const val RECOVERY_DELTA = 0.5
data class ScanSignal(val timestampMs: Long, val isHealthy: Boolean, val severity: String, val isNotAPlant: Boolean)
data class Input(val scans: List<ScanSignal>, val adherence: Double?, val overdueCount: Int, val openCareTasks: Int, val nowMs: Long)
data class Verdict(val state: State, @StringRes val reasonRes: Int, val score: Int)
fun evaluate(input: Input): Verdict
```
Algorithm: drop `isNotAPlant`, take `TREND_WINDOW` newest → empty ⇒ UNKNOWN; newest older than `STALE_SCAN_DAYS` ⇒ UNKNOWN; `score(s) = if healthy 2 else high −3 / medium −2 / else −1`; `latest = score(first)`, `prev = mean(score(rest))`; then
```kotlin
var state = when {
    latest > 0 && (prev == null || prev > 0)       -> HEALTHY
    latest > 0                                      -> RECOVERING   // sick → healthy
    prev != null && latest > prev + RECOVERY_DELTA  -> RECOVERING   // severity easing
    latest <= CRITICAL_THRESHOLD                    -> CRITICAL
    else                                            -> DECLINING
}
```
Adherence modifier is **downgrade-only**: `slipping = adherence < POOR || overdueCount >= 2`; HEALTHY+slipping → RECOVERING with `health_reason_care_slipping`; RECOVERING + adherence ≥ GOOD + no open tasks → reason `health_reason_on_track`. **Never promote DECLINING/CRITICAL on adherence alone — improvement requires a scan.** Document that.

Repo support: `careAdherence(plantId, windowDays = 30, nowMs)` = done / (done+skipped+snoozed+pending), null when denominator 0; `healthVerdict(plantId, nowMs)`.

Strings EN/RU: `health_state_{healthy,recovering,declining,critical,unknown}`, `health_reason_{no_scans,stale,improving,worsening,stable,care_slipping,on_track}`. Colours: HEALTHY→`healthy_green`, RECOVERING→`warning_amber`, DECLINING/CRITICAL→`disease_red`, UNKNOWN→`on_surface_secondary`.

New `PlantHealthTest.kt`: no scans→UNKNOWN; stale→UNKNOWN; sick→healthy→RECOVERING; single high-severity→CRITICAL; medium→low→RECOVERING; low→high→DECLINING; healthy+adherence 0.2→RECOVERING/`care_slipping`; `isNotAPlant` ignored.

## B5. UI surfaces

### B5.1 Today queue — in the Garden tab, not a new tab
`fragment_garden.xml`: insert between `garden_hero` and `garden_search_layout`:
```xml
<FrameLayout android:id="@+id/garden_today_slot" android:layout_width="match_parent"
    android:layout_height="wrap_content" android:layout_marginTop="@dimen/space_md"
    android:visibility="gone" />
```
New files: `ui/garden/TodayQueueView.kt` (`bind(items, maxRows = 4, onDone, onOpenAll, onOpenPlant)`; hides the slot when empty; "See all (N)" when `items.size > maxRows`), `ui/garden/TodayQueueActivity.kt` + `res/layout/activity_today_queue.xml`, `res/layout/item_care_queue.xml` (44dp thumb `queue_thumb`, `queue_plant`, `queue_label`, `queue_due`, 40dp circular `queue_done` using the existing `ic_check`), `res/drawable/bg_check_circle.xml`. **Register `TodayQueueActivity` in `AndroidManifest.xml`** next to `PlantDetailActivity` (same theme, `parentActivityName=".MainActivity"`) — otherwise "See all" throws `ActivityNotFoundException`.

`GardenFragment`: load the queue in `loadPlants()`, render before `renderPlantList`, Done handler is optimistic (remove row → suspend work → `ReminderManager.rescheduleAfterCare` → `loadPlants(animate = false)`) plus `Haptics.tick`.
> Scope creep, do not do: also putting the queue on `HomeFragment`. One home for it.

### B5.2 Care checklist — plant detail
New `ui/garden/detail/DetailCareChecklistController.kt` following the existing controller shape (`KoinComponent`, `by inject()`, `build*` returns a `View`): `build(plantId, diseaseKey): View`, `refresh()`.

In `PlantDetailActivity.render()`, **one line** between the progression card and the care-plan slot:
```kotlin
content.addView(careChecklist.build(plantId, latestDiseaseKey))
```
Writing the tasks: inside `DetailExtraCardsController.buildCarePlanSlot`'s `result.onSuccess { plan -> … }`, after `card.bind(...)`, call `plantRepository.syncCareTasks(...)` then `onCareTasksSynced?.invoke()` — a **new constructor parameter** wired in `PlantDetailActivity` to `{ careChecklist.refresh() }`.

> **CRITICAL — the #1 way this gets broken:** do **not** call the existing `onReload` (`loadPlant()`) from the care-plan success handler. `loadPlant()` → `render()` → `buildCarePlanSlot()` → network → success → `onReload()` is an infinite fetch loop. Only `careChecklist.refresh()`. Put a comment at the call site.

`item_care_checklist.xml`: `MaterialCheckBox @+id/care_step_check` + `TextView @+id/care_step_text` + optional `@+id/care_step_due` (checkpoints). Done rows get `STRIKE_THRU_TEXT_FLAG` and `alpha = 0.55f`. Toggle → `completeCareTask`/`uncompleteCareTask`, re-render that row only. Group under three sub-headers by `source`.

### B5.3 Health header
`DetailHeaderController.buildHealthStrip(verdict): View` — a pill row using the existing `statPill` idiom. **Do not restructure `buildHeader`.** Add to `render()` right after `buildHeader(...)`, loading `healthVerdict` in `loadPlant()` alongside `wateringSuggestion`.

`GardenFragment.applyHealthDot` takes a `PlantHealth.State`. Add a trailing `healthState: PlantHealth.State = UNKNOWN` to `PlantSummary`, computed **from batch data already loaded** (`latestScan` + `openCareTasks` + overdue-ness) — **never** by calling `healthVerdict(plantId)` per card, which would reintroduce the N+1 just removed. Garden cards get a single-scan trend; the full trend lives on the detail screen. Document that asymmetry.

### B5.4 Rooms
Grouping in `renderPlantList` after the existing `due`/`rest` partition: `rest.groupBy { it.room?.id }`, ordered by (null last, `sortOrder`, `name`); **emit room headers only when `grouped.size > 1`** — a single-room garden should not grow a redundant header. Assignment via a `MaterialAutoCompleteTextView` in the existing `showPlantDialog` (blank → null; existing name via `findByName` case-insensitive → its id; new text → `roomDao.insert`). Long-press menu gains `move_to_room`.
> Do not add a room filter chip row — YAGNI this pass.

### B5.5 Koin
Extend the existing `PlantRepository` single in `AppModule.kt` with `roomDao()` and `plantCareTaskDao()`. New constructor params must be **trailing**.

New strings (EN + RU): `today_queue_title/subtitle/see_all/empty`, `care_checklist_title/treatment/sanitation/checkpoints/empty`, `garden_section_no_room`, `plant_room`, `plant_room_new`, `move_to_room`, `room_name`, plus the `health_*` set.

---

## Risk list — what a literal-minded executor gets wrong

| # | Risk | Guard rail |
|---|---|---|
| R1 | Infinite care-plan fetch loop via `onReload()` | Only `careChecklist.refresh()`; comment at the call site |
| R2 | `Zodiac.glyph` removal breaks the build | Enum edit + both `LunarCalendarView` usages in **one** edit; `rg "\.glyph"` must be empty after |
| R3 | Non-trailing / non-defaulted new params | Every new param trailing with a default. Full stop |
| R4 | Room schema drift → crash at open or silent wipe | Types, nullability, FK `onDelete`, index **names** exactly as §B1 |
| R5 | Checkpoint due dates drift forever | Preserve `existing.dueAt` on update; anchor new rows on the **scan** timestamp |
| R6 | `step_key` occurrence collision | One `counters` map across the whole batch |
| R7 | Animator leaks / battery burn | `onVisibilityAggregated` **and** `onDetachedFromWindow` in both views |
| R8 | Height animation measures 0 | `canAnimate = animate && isAttachedToWindow && width > 0` |
| R9 | `arcTo` sweep sign inverted (today's exact bug) | Verify the four cardinal cases by hand |
| R10 | Classifier false positives (`contains`) | Token `startsWith`, min stem 4, priority order, unit tests |
| R11 | Reintroducing N+1 via per-card `healthVerdict` | Garden cards use batch data only |
| R12 | `latestScanPerPlant` picking wrong row | Correlated `MAX(timestamp)`, not `MAX(id)` |
| R13 | Out-of-range cells clickable | Guard on `!inMonth || !isInRange(date)`, set `isClickable = false` |
| R14 | `values-night` colours forgotten → invisible moon | Both files, same commit |
| R15 | RU strings forgotten | Every new `R.string.` in `values-ru/strings.xml` too |
| R16 | `TodayQueueActivity` missing from manifest | Manifest entry beside `PlantDetailActivity` |
| R17 | `expireStalePending` too aggressive | Cutoff `now − 7 days`, never `now` |

---

## Ordering

Lunar first — self-contained, no DB surface, fast tests. If time runs out, ship Epic A alone; nothing in it depends on the schema bump.

| # | Step | Est. |
|---|---|---|
| 1 | `PlantCategory.kt` + `PlantCategoryClassifierTest.kt` | 45 m |
| 2 | `LunarCalendar`: category rules, year bounds, `cyclePosition`, drop `Zodiac.glyph`; extend `LunarCalendarTest` | 45 m |
| 3 | 12 `ic_zodiac_*.xml`, `bg_lunar_day_selected`, `bg_lunar_today_dot`, `item_lunar_day_cell.xml`; delete `bg_lunar_day_today.xml` | 30 m |
| 4 | `MoonPhaseView` rewrite + `moon_*` colours ×2 | 60 m |
| 5 | `view_lunar_calendar.xml` restructure + upcoming-row icon | 30 m |
| 6 | `LunarCalendarView`: `setSpecies`, `selectDate`, `updateSelectionRings`, `updateNavArrows`, `onRequestScrollTo`, icons | 60 m |
| 7 | `SpeciesDetailActivity` wiring | 15 m |
| 8 | `ExpandableSectionView` animation | 30 m |
| 9 | Lunar strings EN + RU | 20 m |
| — | **Checkpoint:** `testDebugUnitTest --tests "*Lunar*" --tests "*PlantCategory*"` | |
| 10 | `Database.kt`: entities, DAOs, `MIGRATION_5_6`, version 6 | 60 m |
| 11 | `CareStepIntent.kt` + test | 45 m |
| 12 | `PlantHealth.kt` + test | 40 m |
| 13 | `PlantRepository` batched + queue + completion + sync; `AppModule` | 90 m |
| 14 | Queue UI: item layout, `bg_check_circle`, `TodayQueueView`, `TodayQueueActivity` + manifest | 60 m |
| 15 | `GardenFragment`: today slot, room grouping, room picker, health dot | 60 m |
| 16 | Checklist controller + `DetailExtraCardsController` hook + `PlantDetailActivity` + health strip | 75 m |
| 17 | Garden strings EN + RU | 25 m |

## Verification (no full build)

```bash
cd android && ./gradlew :app:testDebugUnitTest --tests "com.plantdiseases.app.util.*"
```
Then these must all return **zero** hits:
```bash
rg "\.glyph" android/app/src
rg "bg_lunar_day_today" android/app/src
rg "AutoTransition" android/app/src/main/java/com/plantdiseases/app/ui/common
```
And these must return **two** hits each (light + night): `rg "moon_lit_hi" android/app/src/main/res`, `rg "lunar_good_bg" android/app/src/main/res`.

Manual pass on-device: open a plant → every section collapsed, expand glides with the chevron → Lunar shows "Подобрано для: …" → tap any grid day, ring follows immediately and the panel below updates with an animated moon → tap a nearest-favourable row, it selects that date and scrolls to the panel → arrows stop at Jan 2026 / Dec 2027 → Garden shows a Today card with working one-tap Done → a diseased plant shows a persistent checklist whose ticks survive app restart.

## Explicitly rejected (do not do)

1. Checkboxes inside `CarePlanCardView` — two of its three hosts have no plant to persist against.
2. Re-adding `plants.room` as free text — dropped for cause; the table+FK design is what enables rename/delete/reorder.
3. Caching health state in a `plants` column — stale the moment a reminder fires. Derive it.
4. A 6th bottom-nav tab — breaks `ShellFragment.PAGES` index mapping.
5. Fuzzy text matching to re-bind reworded care steps — silent mis-binding is worse than a lost tick.
6. `BlurMaskFilter` on the moon terminator — forces a software layer every frame.
7. Adding a lunar section to `PlantDetailActivity` (the garden plant screen) — separate task, not this pass.

---

## Kick-off prompt

Paste this to start the implementing session:

> Read `docs/LUNAR_V2_AND_GARDEN_REFACTOR_PLAN.md` in full, then execute it end to end in this repo, following the ordering in the "Ordering" section. Obey the global hard constraints exactly — especially: no `server/` changes, no new Gradle dependency, no 6th bottom-nav tab, every new parameter trailing-with-a-default, and every new string in **both** `values/strings.xml` and `values-ru/strings.xml` plus every new colour in **both** `values/colors.xml` and `values-night/colors.xml`.
>
> Two things that will silently break the build or the app if you get them wrong, so treat them as checkpoints:
> - Deleting `Zodiac.glyph` is compile-breaking — do the enum edit and both `LunarCalendarView` usages in the **same** edit.
> - Never call `onReload()` / `loadPlant()` from the care-plan success handler in `DetailExtraCardsController`; that is an infinite fetch loop. Only `careChecklist.refresh()`.
>
> Stop at the "Checkpoint" line after step 9 and run
> `./gradlew :app:testDebugUnitTest --tests "com.plantdiseases.app.util.*"`
> before starting Epic B. When finished, run the Verification section's greps and report any that do not return the expected hit count, plus anything in the Risk list you could not satisfy.
