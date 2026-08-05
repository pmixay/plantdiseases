# Plant screen → collapsible sections + Lunar calendar

**Execution plan for an implementing model (Sonnet). Target: ≤ 1 hour, one pass.**

Goal: rebuild the species/plant screen so it opens as a stack of **collapsed
sections** (About / Care / Diseases & pests / Fertilising / Lunar calendar /
Tips). Tapping a header expands that section in place. Add a **lunar calendar**
from scratch — it does not exist in the app today.

The screen is data-driven, so changing it once covers **every plant in the app**
(bundled `SpeciesCatalog.popular`, identify results, Home carousel, everything
that launches `SpeciesDetailActivity`). No per-plant work.

---

## 0. Hard constraints — read before touching anything

1. **No server changes.** Everything new is computed on-device. All section
   content already arrives via the existing `/api/care-plan`,
   `/api/species-info`, `/api/species-diseases`, `/api/species-profile` calls
   that `SpeciesDetailActivity` already makes. Do not add endpoints, DTO fields,
   or repository methods.
2. **Do not rename or delete any existing `@+id` in
   `res/layout/activity_species_detail.xml`.** The restructure is a **re-parent
   only**: existing views move *inside* the new section bodies, keeping their
   ids and attributes. Every `binding.xxx` reference in
   `SpeciesDetailActivity.kt` must keep compiling untouched.
3. **Bilingual.** Every new user-visible string needs an entry in **both**
   `res/values/strings.xml` (EN) and `res/values-ru/strings.xml` (RU).
4. **Use existing design tokens.** `@dimen/space_*`, `@dimen/radius_*`,
   `@dimen/text_*`, `@color/primary`, `@color/surface_variant`,
   `@color/on_surface`, `@color/on_surface_secondary`, `@style/App.GlassCard`.
   The dark palette in `values-night/colors.xml` already matches the reference
   screenshot (`background #0A0F0C`, `surface #131A14`) — do not invent a new
   palette.
5. **Both themes.** Any new colour added to `values/colors.xml` needs a
   `values-night/colors.xml` counterpart.
6. Kotlin style: ktlint official (no wildcard imports, trailing commas,
   ≤ 100-char lines). detekt is lax (`LongMethod`, `MagicNumber`, `LargeClass`
   all off) — do not contort code to satisfy it.
7. Do **not** run a full Gradle build. CI does that. At most run
   `./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.LunarCalendarTest"`.

### Note on the reference screenshot

The screenshot's lunar data is a **mock** (it shows 18 Aug 2026 as *Virgo*;
the real Moon is in *Scorpio* that day, and the device clock says a different
date). Copy the **layout, spacing, colours and interaction** literally. Do
**not** try to reproduce its numbers — implement real astronomy (§4) and let
the values fall where they fall.

---

## 1. Files you will touch

**New files (9):**

| Path | What |
|---|---|
| `android/app/src/main/java/com/plantdiseases/app/ui/common/ExpandableSectionView.kt` | Reusable collapsible section card |
| `android/app/src/main/java/com/plantdiseases/app/util/LunarCalendar.kt` | Pure-Kotlin moon math + day ratings |
| `android/app/src/main/java/com/plantdiseases/app/ui/widget/MoonPhaseView.kt` | Canvas-drawn moon disc |
| `android/app/src/main/java/com/plantdiseases/app/ui/widget/LunarCalendarView.kt` | The whole lunar section body |
| `android/app/src/main/res/layout/view_lunar_calendar.xml` | Its layout |
| `android/app/src/main/res/layout/item_lunar_favourable_day.xml` | One "upcoming favourable day" row |
| `android/app/src/main/res/drawable/ic_moon.xml` | Crescent icon for the section header |
| `android/app/src/main/res/drawable/bg_lunar_day_good.xml` / `_neutral.xml` / `_bad.xml` / `_today.xml` | Calendar-cell circles (4 files) |
| `android/app/src/test/java/com/plantdiseases/app/util/LunarCalendarTest.kt` | Unit test |

**Edited files (5):**

- `android/app/src/main/res/layout/activity_species_detail.xml` (restructure)
- `android/app/src/main/java/com/plantdiseases/app/ui/species/SpeciesDetailActivity.kt` (wiring)
- `android/app/src/main/res/values/strings.xml`
- `android/app/src/main/res/values-ru/strings.xml`
- `android/app/src/main/res/values/colors.xml` + `values-night/colors.xml`

**Read first (do not edit):** `SpeciesDetailActivity.kt`,
`activity_species_detail.xml`, `ui/common/CarePlanCardView.kt` (its
`toggleDetails()` is the expand idiom already used in the app),
`res/layout/item_species_disease.xml` (row styling to mimic).

---

## 2. Strings

Add to `res/values/strings.xml` (EN) and `res/values-ru/strings.xml` (RU).
Keep them in one block near the existing `species_*` strings.

```xml
<!-- Plant screen · collapsible sections -->
<string name="section_about_title">About the plant</string>
<string name="section_about_subtitle">Description, traits and varieties.</string>
<string name="section_care_title">Care</string>
<string name="section_care_subtitle">Watering, light, temperature, humidity and pruning.</string>
<string name="section_diseases_title">Diseases and pests</string>
<string name="section_diseases_subtitle">Common problems and how to treat them.</string>
<string name="section_fertiliser_title">Fertiliser and feeding</string>
<string name="section_fertiliser_subtitle">What to feed with, and when.</string>
<string name="section_lunar_title">Lunar calendar</string>
<string name="section_lunar_subtitle">Favourable days for planting and care.</string>
<string name="section_tips_title">Tips</string>
<string name="section_tips_subtitle">Useful growing recommendations.</string>
<string name="section_expand">Expand section</string>
<string name="section_collapse">Collapse section</string>

<!-- Lunar calendar -->
<string name="lunar_today">Today, %1$s</string>
<string name="lunar_good_for">The day is good for:</string>
<string name="lunar_avoid">Better to postpone:</string>
<string name="lunar_upcoming">Upcoming favourable days</string>
<string name="lunar_legend_good">favourable</string>
<string name="lunar_legend_neutral">neutral</string>
<string name="lunar_legend_bad">unfavourable</string>
<string name="lunar_prev_month">Previous month</string>
<string name="lunar_next_month">Next month</string>

<!-- Moon phases -->
<string name="moon_new">New Moon</string>
<string name="moon_waxing_crescent">Waxing Moon</string>
<string name="moon_first_quarter">First Quarter</string>
<string name="moon_waxing_gibbous">Waxing Moon</string>
<string name="moon_full">Full Moon</string>
<string name="moon_waning_gibbous">Waning Moon</string>
<string name="moon_last_quarter">Last Quarter</string>
<string name="moon_waning_crescent">Waning Moon</string>

<!-- Zodiac signs -->
<string name="zodiac_aries">Aries</string>
<string name="zodiac_taurus">Taurus</string>
<string name="zodiac_gemini">Gemini</string>
<string name="zodiac_cancer">Cancer</string>
<string name="zodiac_leo">Leo</string>
<string name="zodiac_virgo">Virgo</string>
<string name="zodiac_libra">Libra</string>
<string name="zodiac_scorpio">Scorpio</string>
<string name="zodiac_sagittarius">Sagittarius</string>
<string name="zodiac_capricorn">Capricorn</string>
<string name="zodiac_aquarius">Aquarius</string>
<string name="zodiac_pisces">Pisces</string>

<!-- Lunar tasks -->
<string name="lunar_task_planting">Planting</string>
<string name="lunar_task_repotting">Repotting</string>
<string name="lunar_task_feeding">Feeding</string>
<string name="lunar_task_watering">Watering</string>
<string name="lunar_task_pruning">Pruning</string>

<!-- Weekday initials, Monday first -->
<string name="lunar_dow_mon">Mon</string>
<string name="lunar_dow_tue">Tue</string>
<string name="lunar_dow_wed">Wed</string>
<string name="lunar_dow_thu">Thu</string>
<string name="lunar_dow_fri">Fri</string>
<string name="lunar_dow_sat">Sat</string>
<string name="lunar_dow_sun">Sun</string>

<!-- Fertiliser / tips section bodies -->
<string name="fertiliser_interval">Feed every %1$d weeks during active growth.</string>
<string name="fertiliser_interval_none">No regular feeding needed for this plant.</string>
<string name="fertiliser_npk">Fertiliser: %1$s</string>
<string name="fertiliser_soil">Soil mix: %1$s</string>
<string name="section_empty">Details will appear once the plant profile has loaded.</string>
```

RU values (`values-ru/strings.xml`) — use these exact strings, they match the
reference screenshot:

```xml
<string name="section_about_title">О растении</string>
<string name="section_about_subtitle">Описание, особенности и виды.</string>
<string name="section_care_title">Уход</string>
<string name="section_care_subtitle">Полив, свет, температура, влажность и обрезка.</string>
<string name="section_diseases_title">Болезни и вредители</string>
<string name="section_diseases_subtitle">Распространённые проблемы и способы лечения.</string>
<string name="section_fertiliser_title">Удобрения и подкормка</string>
<string name="section_fertiliser_subtitle">Чем и когда подкармливать.</string>
<string name="section_lunar_title">Лунный календарь</string>
<string name="section_lunar_subtitle">Благоприятные дни для посадки и ухода.</string>
<string name="section_tips_title">Советы</string>
<string name="section_tips_subtitle">Полезные рекомендации по выращиванию.</string>
<string name="section_expand">Развернуть раздел</string>
<string name="section_collapse">Свернуть раздел</string>

<string name="lunar_today">Сегодня, %1$s</string>
<string name="lunar_good_for">День благоприятен для:</string>
<string name="lunar_avoid">Лучше отложить:</string>
<string name="lunar_upcoming">Ближайшие благоприятные дни</string>
<string name="lunar_legend_good">благоприятные</string>
<string name="lunar_legend_neutral">нейтральные</string>
<string name="lunar_legend_bad">неблагоприятные</string>
<string name="lunar_prev_month">Предыдущий месяц</string>
<string name="lunar_next_month">Следующий месяц</string>

<string name="moon_new">Новолуние</string>
<string name="moon_waxing_crescent">Растущая Луна</string>
<string name="moon_first_quarter">Первая четверть</string>
<string name="moon_waxing_gibbous">Растущая Луна</string>
<string name="moon_full">Полнолуние</string>
<string name="moon_waning_gibbous">Убывающая Луна</string>
<string name="moon_last_quarter">Последняя четверть</string>
<string name="moon_waning_crescent">Убывающая Луна</string>

<string name="zodiac_aries">Овен</string>
<string name="zodiac_taurus">Телец</string>
<string name="zodiac_gemini">Близнецы</string>
<string name="zodiac_cancer">Рак</string>
<string name="zodiac_leo">Лев</string>
<string name="zodiac_virgo">Дева</string>
<string name="zodiac_libra">Весы</string>
<string name="zodiac_scorpio">Скорпион</string>
<string name="zodiac_sagittarius">Стрелец</string>
<string name="zodiac_capricorn">Козерог</string>
<string name="zodiac_aquarius">Водолей</string>
<string name="zodiac_pisces">Рыбы</string>

<string name="lunar_task_planting">Посадка</string>
<string name="lunar_task_repotting">Пересадка</string>
<string name="lunar_task_feeding">Подкормка</string>
<string name="lunar_task_watering">Полив</string>
<string name="lunar_task_pruning">Обрезка</string>

<string name="lunar_dow_mon">Пн</string>
<string name="lunar_dow_tue">Вт</string>
<string name="lunar_dow_wed">Ср</string>
<string name="lunar_dow_thu">Чт</string>
<string name="lunar_dow_fri">Пт</string>
<string name="lunar_dow_sat">Сб</string>
<string name="lunar_dow_sun">Вс</string>

<string name="fertiliser_interval">Подкармливать каждые %1$d недель в период активного роста.</string>
<string name="fertiliser_interval_none">Регулярная подкормка этому растению не нужна.</string>
<string name="fertiliser_npk">Удобрение: %1$s</string>
<string name="fertiliser_soil">Субстрат: %1$s</string>
<string name="section_empty">Детали появятся, когда загрузится профиль растения.</string>
```

---

## 3. Colours and drawables

### 3a. `res/values/colors.xml` — append before `</resources>`

```xml
<!-- ─── Lunar calendar day ratings ───────────────────────────
     Filled circle + matching text colour per rating. Mirrors the
     healthy/warning/disease semantic ramp so the calendar reads as
     part of the same design system. -->
<color name="lunar_good_bg">#DCFCE7</color>
<color name="lunar_good_fg">#15803D</color>
<color name="lunar_neutral_bg">#FEF3C7</color>
<color name="lunar_neutral_fg">#B45309</color>
<color name="lunar_bad_bg">#FEE2E2</color>
<color name="lunar_bad_fg">#B91C1C</color>
```

### 3b. `res/values-night/colors.xml` — append before `</resources>`

```xml
<!-- Lunar calendar (dark): deep fills with bright glyph text, the
     inverse of the light ramp — matches every other dark chip. -->
<color name="lunar_good_bg">#14532D</color>
<color name="lunar_good_fg">#4ADE80</color>
<color name="lunar_neutral_bg">#3F2A06</color>
<color name="lunar_neutral_fg">#FBBF24</color>
<color name="lunar_bad_bg">#450A0A</color>
<color name="lunar_bad_fg">#F87171</color>
```

### 3c. `res/drawable/ic_moon.xml`

24dp crescent, same authoring style as the other `ic_*.xml` (white fill,
tinted at use site):

```xml
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="24"
    android:viewportHeight="24"
    android:tint="@color/primary">
    <path
        android:fillColor="@android:color/white"
        android:pathData="M12,3c0.13,0 0.26,0 0.39,0.01C10.93,4.47 10,6.63 10,9c0,4.42 3.13,8 7,8 0.68,0 1.33,-0.11 1.95,-0.31C17.7,19.28 15.04,21 12,21 7.03,21 3,16.97 3,12S7.03,3 12,3z" />
</vector>
```

### 3d. Calendar-cell backgrounds

`res/drawable/bg_lunar_day_good.xml` (repeat for `_neutral` / `_bad`, swapping
the colour token):

```xml
<shape xmlns:android="http://schemas.android.com/apk/res/android"
    android:shape="oval">
    <solid android:color="@color/lunar_good_bg" />
</shape>
```

`res/drawable/bg_lunar_day_today.xml` — the "today" ring from the screenshot
(hollow, 2dp primary stroke):

```xml
<shape xmlns:android="http://schemas.android.com/apk/res/android"
    android:shape="oval">
    <solid android:color="@android:color/transparent" />
    <stroke android:width="2dp" android:color="@color/primary" />
</shape>
```

---

## 4. `util/LunarCalendar.kt` — the maths (pure Kotlin, no Android imports)

Package `com.plantdiseases.app.util`. **No Android types** — that keeps it
unit-testable on the JVM without Robolectric. It returns enums and ints; the
view layer maps those to string resources.

```kotlin
package com.plantdiseases.app.util

import java.time.LocalDate
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.sin

/**
 * Offline lunar-gardening calculator.
 *
 * Everything here is arithmetic on the date — no network, no permissions, no
 * stored state — so the lunar section renders instantly and works offline for
 * every species in the app.
 *
 * Accuracy: the Moon's ecliptic longitude uses the standard low-precision
 * series (four largest terms), good to roughly ±0.3°, i.e. the zodiac sign is
 * right except within ~half an hour of a sign boundary. That is far inside the
 * tolerance of a gardening calendar.
 */
object LunarCalendar {

    /** Mean synodic month, days. */
    private const val SYNODIC_MONTH = 29.530588853

    /** Julian Day of the new moon of 2000-01-06 18:14 UT — the age epoch. */
    private const val NEW_MOON_EPOCH_JD = 2451550.1

    /** Julian Day of J2000.0 (2000-01-01 12:00 TT). */
    private const val J2000_JD = 2451545.0

    enum class Phase {
        NEW, WAXING_CRESCENT, FIRST_QUARTER, WAXING_GIBBOUS,
        FULL, WANING_GIBBOUS, LAST_QUARTER, WANING_CRESCENT,
    }

    enum class Zodiac(val glyph: String) {
        ARIES("♈"), TAURUS("♉"), GEMINI("♊"), CANCER("♋"),
        LEO("♌"), VIRGO("♍"), LIBRA("♎"), SCORPIO("♏"),
        SAGITTARIUS("♐"), CAPRICORN("♑"), AQUARIUS("♒"), PISCES("♓"),
    }

    enum class Rating { GOOD, NEUTRAL, BAD }

    enum class Task { PLANTING, REPOTTING, FEEDING, WATERING, PRUNING }

    /** Everything the UI needs for one calendar day. */
    data class DayInfo(
        val date: LocalDate,
        val phase: Phase,
        val zodiac: Zodiac,
        /** 0.0 = new, 1.0 = full. */
        val illumination: Double,
        val isWaxing: Boolean,
        val rating: Rating,
        val favourable: List<Task>,
        val avoid: List<Task>,
    )

    // ── Astronomy ────────────────────────────────────────────────

    /** Julian Day at 12:00 UT on [date]. */
    private fun julianDay(date: LocalDate): Double = date.toEpochDay() + 2440588.0

    /** Days elapsed since the last new moon, 0 until [SYNODIC_MONTH]. */
    fun moonAge(date: LocalDate): Double {
        val age = (julianDay(date) - NEW_MOON_EPOCH_JD) % SYNODIC_MONTH
        return if (age < 0) age + SYNODIC_MONTH else age
    }

    fun illumination(date: LocalDate): Double =
        (1 - cos(2 * Math.PI * moonAge(date) / SYNODIC_MONTH)) / 2

    fun isWaxing(date: LocalDate): Boolean = moonAge(date) < SYNODIC_MONTH / 2

    fun phase(date: LocalDate): Phase = when (moonAge(date)) {
        in 0.0..1.0 -> Phase.NEW
        in 1.0..6.4 -> Phase.WAXING_CRESCENT
        in 6.4..8.4 -> Phase.FIRST_QUARTER
        in 8.4..13.8 -> Phase.WAXING_GIBBOUS
        in 13.8..15.8 -> Phase.FULL
        in 15.8..21.1 -> Phase.WANING_GIBBOUS
        in 21.1..23.1 -> Phase.LAST_QUARTER
        else -> Phase.WANING_CRESCENT
    }

    /** Apparent ecliptic longitude of the Moon in degrees, 0..360. */
    fun moonLongitude(date: LocalDate): Double {
        val d = julianDay(date) - J2000_JD
        val meanLongitude = 218.316 + 13.176396 * d
        val meanAnomaly = 134.963 + 13.064993 * d
        val elongation = 297.850 + 12.190749 * d
        val sunAnomaly = 357.529 + 0.98560028 * d
        val lambda = meanLongitude +
            6.289 * sin(Math.toRadians(meanAnomaly)) -
            1.274 * sin(Math.toRadians(2 * elongation - meanAnomaly)) +
            0.658 * sin(Math.toRadians(2 * elongation)) -
            0.186 * sin(Math.toRadians(sunAnomaly))
        val normalised = lambda % 360
        return if (normalised < 0) normalised + 360 else normalised
    }

    fun zodiac(date: LocalDate): Zodiac =
        Zodiac.entries[floor(moonLongitude(date) / 30.0).toInt().coerceIn(0, 11)]

    // ── Gardening rules ──────────────────────────────────────────

    /** Water signs — traditionally the most productive for growth work. */
    private val fertileSigns = setOf(Zodiac.CANCER, Zodiac.SCORPIO, Zodiac.PISCES)

    /** Earth/air signs that are workable but less productive. */
    private val semiFertileSigns = setOf(Zodiac.TAURUS, Zodiac.CAPRICORN, Zodiac.LIBRA, Zodiac.VIRGO)

    /** New/full moon days: the classic "do nothing" window. */
    private fun isTurningPoint(date: LocalDate): Boolean {
        val age = moonAge(date)
        return age < 1.0 || age > SYNODIC_MONTH - 1.0 ||
            (age > SYNODIC_MONTH / 2 - 1.0 && age < SYNODIC_MONTH / 2 + 1.0)
    }

    fun rating(date: LocalDate): Rating = when {
        isTurningPoint(date) -> Rating.BAD
        zodiac(date) in fertileSigns -> Rating.GOOD
        zodiac(date) in semiFertileSigns -> Rating.GOOD
        else -> Rating.NEUTRAL
    }

    /**
     * Tasks the day suits, most-recommended first, capped at three so the row
     * in the UI never wraps. Waxing moon favours above-ground growth work
     * (planting, repotting, feeding); waning favours pruning and root work;
     * water signs favour watering.
     */
    fun favourableTasks(date: LocalDate): List<Task> {
        if (isTurningPoint(date)) return emptyList()
        val waxing = isWaxing(date)
        val sign = zodiac(date)
        val fertile = sign in fertileSigns || sign in semiFertileSigns
        return buildList {
            if (waxing && fertile) add(Task.REPOTTING)
            if (waxing) add(Task.FEEDING)
            if (waxing && fertile) add(Task.PLANTING)
            if (sign in fertileSigns) add(Task.WATERING)
            if (!waxing && !fertile) add(Task.PRUNING)
        }.take(3)
    }

    /** Tasks to postpone — the complement of [favourableTasks], capped at two. */
    fun avoidTasks(date: LocalDate): List<Task> {
        val good = favourableTasks(date).toSet()
        if (isTurningPoint(date)) return listOf(Task.PLANTING, Task.REPOTTING)
        return Task.entries.filter { it !in good }.take(2)
    }

    fun dayInfo(date: LocalDate): DayInfo = DayInfo(
        date = date,
        phase = phase(date),
        zodiac = zodiac(date),
        illumination = illumination(date),
        isWaxing = isWaxing(date),
        rating = rating(date),
        favourable = favourableTasks(date),
        avoid = avoidTasks(date),
    )

    /** Every day of [year]-[month], in order — the calendar grid source. */
    fun month(year: Int, month: Int): List<DayInfo> {
        val first = LocalDate.of(year, month, 1)
        return (0 until first.lengthOfMonth()).map { dayInfo(first.plusDays(it.toLong())) }
    }

    /** The next [limit] GOOD days strictly after [from] — the "upcoming" list. */
    fun upcomingFavourable(from: LocalDate, limit: Int = 4): List<DayInfo> =
        generateSequence(from.plusDays(1)) { it.plusDays(1) }
            .take(60)
            .map(::dayInfo)
            .filter { it.rating == Rating.GOOD }
            .take(limit)
            .toList()
}
```

**`java.time` is safe here:** the module is `minSdk = 29` and Kotlin 2.1.21, so
`LocalDate` / `YearMonth` / `DateTimeFormatter` and enum `.entries` all work
natively. No desugaring, no new dependency.

---

## 5. `ui/widget/MoonPhaseView.kt`

The screenshot shows a photo-like moon. A Canvas-drawn disc matches the app's
vector-only asset policy and costs ~50 lines.

```kotlin
package com.plantdiseases.app.ui.widget

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import kotlin.math.abs
import kotlin.math.min

/**
 * Draws the Moon's lit fraction: a lit disc, then the shadow drawn as a
 * half-disc plus an ellipse whose width tracks the terminator. Set
 * [illumination] (0..1) and [waxing]; the view invalidates itself.
 */
class MoonPhaseView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    private val litPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#E8E8E4") }
    private val darkPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.parseColor("#20261F") }
    private val oval = RectF()

    var illumination: Double = 0.5
        set(value) { field = value.coerceIn(0.0, 1.0); invalidate() }

    var waxing: Boolean = true
        set(value) { field = value; invalidate() }

    override fun onDraw(canvas: Canvas) {
        val radius = min(width, height) / 2f
        val cx = width / 2f
        val cy = height / 2f
        canvas.drawCircle(cx, cy, radius, litPaint)

        // Shadow half: waxing moons are lit on the right, so the shadow sits
        // on the left, and vice versa.
        val shadowOnLeft = waxing
        oval.set(
            if (shadowOnLeft) cx - radius else cx,
            cy - radius,
            if (shadowOnLeft) cx else cx + radius,
            cy + radius,
        )
        canvas.drawArc(
            RectF(cx - radius, cy - radius, cx + radius, cy + radius),
            if (shadowOnLeft) 90f else 270f,
            180f,
            true,
            darkPaint,
        )

        // Terminator ellipse: bulges into the lit half before quarter (adds
        // shadow) and into the dark half after (removes it).
        val terminator = abs(1 - 2 * illumination).toFloat() * radius
        oval.set(cx - terminator, cy - radius, cx + terminator, cy + radius)
        canvas.drawOval(oval, if (illumination < 0.5) darkPaint else litPaint)
    }
}
```

---

## 6. `ui/common/ExpandableSectionView.kt` — the collapsible section

The single reusable piece. Header (icon-in-circle + title + subtitle +
chevron) is inflated in code; the body is an empty `FrameLayout` the host
fills with any view.

```kotlin
package com.plantdiseases.app.ui.common

import android.content.Context
import android.util.AttributeSet
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.transition.AutoTransition
import android.transition.TransitionManager
import com.google.android.material.card.MaterialCardView
import com.plantdiseases.app.R

/**
 * One collapsible section on the plant screen: a tappable header row and a
 * body that is GONE until the user expands it. Every section on the screen is
 * an instance of this, so spacing, chevron behaviour and the expand animation
 * stay identical across Care / Diseases / Lunar / Tips.
 *
 * The body is intentionally a bare container — hosts add whatever view they
 * already had (the existing CarePlanCardView, the disease list, the new
 * LunarCalendarView), so no existing rendering logic changes.
 */
class ExpandableSectionView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : MaterialCardView(context, attrs, defStyle) {

    private val header: LinearLayout
    private val iconView: ImageView
    private val titleView: TextView
    private val subtitleView: TextView
    private val chevron: ImageView
    val body: FrameLayout

    var isExpanded: Boolean = false
        private set

    var onToggle: ((Boolean) -> Unit)? = null

    init {
        radius = resources.getDimension(R.dimen.radius_lg)
        cardElevation = 0f
        useCompatPadding = false
        setContentPadding(0, 0, 0, 0)
        setCardBackgroundColor(context.getColor(R.color.surface_variant))

        val column = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        addView(column, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))

        header = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(14))
            isClickable = true
            isFocusable = true
            setBackgroundResource(outValueSelectableBackground())
            setOnClickListener { toggle() }
        }
        column.addView(header)

        iconView = ImageView(context).apply {
            setBackgroundResource(R.drawable.bg_icon_circle)
            setPadding(dp(9), dp(9), dp(9), dp(9))
            imageTintList = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.primary),
            )
        }
        header.addView(iconView, LinearLayout.LayoutParams(dp(42), dp(42)))

        val texts = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        header.addView(
            texts,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = dp(14)
                marginEnd = dp(8)
            },
        )
        titleView = TextView(context).apply {
            setTextColor(context.getColor(R.color.on_surface))
            setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX,
                resources.getDimension(R.dimen.text_title_sm))
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        }
        subtitleView = TextView(context).apply {
            setTextColor(context.getColor(R.color.on_surface_secondary))
            setTextSize(android.util.TypedValue.COMPLEX_UNIT_PX,
                resources.getDimension(R.dimen.text_body_sm))
        }
        texts.addView(titleView)
        texts.addView(subtitleView)

        chevron = ImageView(context).apply {
            setImageResource(R.drawable.ic_chevron_down)
            imageTintList = android.content.res.ColorStateList.valueOf(
                context.getColor(R.color.on_surface_secondary),
            )
        }
        header.addView(chevron, LinearLayout.LayoutParams(dp(24), dp(24)))

        body = FrameLayout(context).apply {
            visibility = View.GONE
            setPadding(dp(12), 0, dp(12), dp(12))
        }
        column.addView(body)
        updateAccessibility()
    }

    fun bind(iconRes: Int, titleRes: Int, subtitleRes: Int) {
        iconView.setImageResource(iconRes)
        titleView.setText(titleRes)
        subtitleView.setText(subtitleRes)
    }

    /** Adds [content] as the section body, replacing anything already there. */
    fun setContent(content: View) {
        body.removeAllViews()
        body.addView(
            content,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            ),
        )
    }

    fun toggle() = setExpanded(!isExpanded)

    fun setExpanded(expanded: Boolean, animate: Boolean = true) {
        if (expanded == isExpanded) return
        isExpanded = expanded
        if (animate) {
            (parent as? ViewGroup)?.let {
                TransitionManager.beginDelayedTransition(it, AutoTransition().setDuration(180))
            }
        }
        body.visibility = if (expanded) View.VISIBLE else View.GONE
        chevron.rotation = if (expanded) 180f else 0f
        updateAccessibility()
        onToggle?.invoke(expanded)
    }

    private fun updateAccessibility() {
        header.contentDescription = context.getString(
            if (isExpanded) R.string.section_collapse else R.string.section_expand,
        )
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun outValueSelectableBackground(): Int {
        val outValue = android.util.TypedValue()
        context.theme.resolveAttribute(
            android.R.attr.selectableItemBackground, outValue, true,
        )
        return outValue.resourceId
    }
}
```

`android.transition.*` is the framework API (no dependency). If it misbehaves
inside the `NestedScrollView`, just drop the `TransitionManager` lines and flip
visibility directly — do **not** add a new Gradle dependency for this.

---

## 7. Lunar section body — layout + view

### 7a. `res/layout/item_lunar_favourable_day.xml`

One row of the "upcoming favourable days" list (dot + date + glyph + sign
name + chevron), mirroring `item_species_disease.xml`'s spacing:

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- One "upcoming favourable day" row in the lunar section. Rows are built by
     LunarCalendarView; tapping one selects that date in the grid. -->
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    android:layout_width="match_parent"
    android:layout_height="wrap_content"
    android:background="?attr/selectableItemBackground"
    android:gravity="center_vertical"
    android:orientation="horizontal"
    android:paddingVertical="@dimen/space_md">

    <View
        android:id="@+id/lunar_row_dot"
        android:layout_width="12dp"
        android:layout_height="12dp"
        android:layout_marginEnd="@dimen/space_md"
        android:background="@drawable/bg_lunar_day_good" />

    <LinearLayout
        android:layout_width="0dp"
        android:layout_height="wrap_content"
        android:layout_weight="1"
        android:orientation="vertical">

        <TextView
            android:id="@+id/lunar_row_date"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:textColor="@color/primary"
            android:textSize="@dimen/text_body" />

        <TextView
            android:id="@+id/lunar_row_sign"
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="2dp"
            android:textColor="@color/on_surface"
            android:textSize="@dimen/text_body" />
    </LinearLayout>

    <ImageView
        android:layout_width="20dp"
        android:layout_height="20dp"
        android:contentDescription="@null"
        android:importantForAccessibility="no"
        android:src="@drawable/ic_chevron_right"
        app:tint="@color/on_surface_secondary" />
</LinearLayout>
```

### 7b. `res/layout/view_lunar_calendar.xml`

Vertical stack: **today card → month grid + legend → upcoming days**. (The
screenshot's two-column split is a tablet rendering; on a phone the same
blocks stack. Keep one layout — the section reads correctly at both widths.)

Structure to build (ids matter, the view class binds them):

```
LinearLayout (vertical, id=lunar_root)
├── MaterialCardView  style=@style/App.GlassCard, cardBackgroundColor=@color/surface
│   └── LinearLayout (vertical)
│       ├── TextView  id=lunar_today_title      textColor=@color/primary, 16sp, medium
│       ├── LinearLayout (horizontal, gravity=center_vertical, marginTop=space_md)
│       │   ├── com.plantdiseases.app.ui.widget.MoonPhaseView  id=lunar_moon  64dp × 64dp
│       │   └── LinearLayout (vertical, marginStart=space_lg)
│       │       ├── TextView  id=lunar_phase_name   textColor=@color/on_surface, text_title_sm
│       │       └── TextView  id=lunar_sign         textColor=@color/on_surface, text_body, marginTop=2dp
│       ├── View  (1dp divider, @color/divider, marginTop=space_lg)
│       ├── TextView  id=lunar_good_label   text=@string/lunar_good_for, @color/primary, text_body_sm
│       ├── LinearLayout  id=lunar_good_tasks   (horizontal, marginTop=space_sm)   ← icon+label tiles added in code
│       ├── TextView  id=lunar_avoid_label  text=@string/lunar_avoid, @color/disease_red, text_body_sm
│       └── LinearLayout  id=lunar_avoid_tasks  (horizontal, marginTop=space_sm)
│
├── MaterialCardView  style=@style/App.GlassCard, cardBackgroundColor=@color/surface, marginTop=space_md
│   └── LinearLayout (vertical)
│       ├── LinearLayout (horizontal, gravity=center_vertical)   ← month nav
│       │   ├── ImageButton id=lunar_prev  ic_chevron_right rotated 180 (or ic_back), tint on_surface_secondary
│       │   ├── TextView    id=lunar_month_label  layout_weight=1, gravity=center, text_title_sm, medium
│       │   └── ImageButton id=lunar_next  ic_chevron_right, tint on_surface_secondary
│       ├── LinearLayout  id=lunar_dow_row  (horizontal, marginTop=space_md)  ← 7 weighted TextViews, Mon..Sun
│       ├── GridLayout    id=lunar_grid     columnCount=7, marginTop=space_sm
│       └── LinearLayout  id=lunar_legend   (vertical, marginTop=space_lg)   ← 3 rows built in code
│
└── MaterialCardView  style=@style/App.GlassCard, cardBackgroundColor=@color/surface, marginTop=space_md
    └── LinearLayout (vertical)
        ├── TextView  text=@string/lunar_upcoming, text_title_sm, medium, @color/on_surface
        └── LinearLayout  id=lunar_upcoming_list  (vertical, marginTop=space_xs)
```

Weekday header TextViews: `layout_width=0dp`, `layout_weight=1`,
`gravity=center`, `textSize=@dimen/text_body_sm`,
`textColor=@color/on_surface_secondary`, text = `@string/lunar_dow_mon`…`_sun`.

### 7c. `ui/widget/LunarCalendarView.kt`

```kotlin
package com.plantdiseases.app.ui.widget

import android.content.Context
import android.util.AttributeSet
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.widget.FrameLayout
import android.widget.GridLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.plantdiseases.app.R
import com.plantdiseases.app.util.LunarCalendar
import java.time.LocalDate
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The whole lunar-calendar section body: today's phase + task advice, a
 * month grid coloured by day rating, a legend, and the next favourable dates.
 *
 * Entirely offline — [LunarCalendar] derives everything from the date, so the
 * section renders instantly for every species and needs no server call.
 */
class LunarCalendarView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : FrameLayout(context, attrs, defStyle) {

    private val today: LocalDate = LocalDate.now()
    private var shownMonth: YearMonth = YearMonth.from(today)
    private var selected: LocalDate = today

    // findViewById the ids from view_lunar_calendar.xml here.

    init {
        LayoutInflater.from(context).inflate(R.layout.view_lunar_calendar, this, true)
        // wire lunar_prev / lunar_next to shownMonth.minusMonths(1)/plusMonths(1) + renderGrid()
        renderToday(selected)
        renderGrid()
        renderLegend()
        renderUpcoming()
    }
    ...
}
```

Implementation notes for each render function:

- **`renderToday(date)`** — `LunarCalendar.dayInfo(date)`; set
  `lunar_today_title` to `getString(R.string.lunar_today, formatDate(date))`
  where `formatDate` uses `DateTimeFormatter.ofPattern("d MMMM", Locale.getDefault())`
  (gives "18 августа" in RU, "18 August" in EN); set `MoonPhaseView.illumination`
  / `.waxing`; `lunar_phase_name` = phase string; `lunar_sign` =
  `"${zodiac.glyph}  ${getString(signStringRes)}"`.
- **task tiles** — for each `Task`, add a vertical `LinearLayout` (24dp
  `ImageView` tinted `@color/primary` for favourable / `@color/disease_red`
  for avoid, plus an 11sp caption below). Icon map:
  `PLANTING → R.drawable.ic_garden`, `REPOTTING → R.drawable.ic_repot`,
  `FEEDING → R.drawable.ic_fertilize`, `WATERING → R.drawable.ic_watering`,
  `PRUNING → R.drawable.ic_prune` (all already exist). Hide the
  label + row when the list is empty.
- **`renderGrid()`** — set `lunar_month_label` to
  `DateTimeFormatter.ofPattern("LLLL yyyy", Locale.getDefault())` capitalised.
  Clear `lunar_grid`, then add 42 cells: leading days from the previous month
  and trailing days from the next, dimmed (`on_surface_secondary` at 40 %
  alpha, no background, not clickable) — the screenshot shows exactly this.
  Each in-month cell is a `TextView`, 36dp × 36dp with 2dp margins,
  `gravity=center`, background from
  `GOOD → bg_lunar_day_good`, `NEUTRAL → bg_lunar_day_neutral`,
  `BAD → bg_lunar_day_bad`, text colour the matching `lunar_*_fg`.
  Rating GOOD **and** it being today are independent: for today, wrap by
  setting `bg_lunar_day_today` when the day is unrated (rating is not GOOD/BAD),
  otherwise use a `LayerDrawable` of the rating fill + the today ring. Simplest
  working version: if `date == today`, use `bg_lunar_day_today` and
  `@color/primary` text (matches the screenshot's outlined "18").
  Column index: `(dayOfWeek.value - 1)` so the week starts Monday.
  Tapping an in-month cell calls `renderToday(date)` and re-renders the grid so
  the selection ring follows the tap.
- **`renderLegend()`** — three rows, each a 12dp circle with the rating
  background + a `text_body_sm` label (`lunar_legend_good` / `_neutral` /
  `_bad`) in `on_surface_secondary`.
- **`renderUpcoming()`** — `LunarCalendar.upcomingFavourable(today, 4)`, inflate
  `item_lunar_favourable_day.xml` per entry, set `lunar_row_date` to the
  formatted date, `lunar_row_sign` to `"${glyph}  ${signName}"`, and on click
  jump the grid to that month and select the date.

Keep phase/sign/task → string-resource mapping in three small `private fun
…Res(x): Int` `when` blocks inside this view. Do **not** put Android
resource ids in `LunarCalendar.kt`.

---

## 8. Restructure `activity_species_detail.xml`

**This is the risky step. It is a move, not a rewrite.**

Keep untouched, in this order, at the top of the existing inner
`LinearLayout`: `iv_species_hero`, `photo_strip_scroll`/`photo_strip`,
`tv_species_title`, `tv_species_scientific`, `progress`.

Then **replace** everything from `tv_species_extract` down to (but not
including) the bottom button row with six sections. Each is:

```xml
<com.plantdiseases.app.ui.common.ExpandableSectionView
    android:id="@+id/section_about"
    android:layout_width="match_parent"
    android:layout_height="wrap_content"
    android:layout_marginTop="@dimen/space_md" />
```

ids: `section_about`, `section_care`, `section_diseases`,
`section_fertiliser`, `section_lunar`, `section_tips`.

The **existing views keep their ids and move into per-section container
`LinearLayout`s** that live in the same file, declared with
`android:visibility="gone"` **outside** the sections is *not* possible in XML —
so instead declare each section's content as a child `LinearLayout` inside a
plain hidden container at the bottom of the file:

```xml
<!-- Section bodies. Declared here so every existing view keeps its id and
     attributes; SpeciesDetailActivity re-parents each block into its
     ExpandableSectionView at runtime (see attachSections()). -->
<LinearLayout
    android:id="@+id/section_content_pool"
    android:layout_width="match_parent"
    android:layout_height="wrap_content"
    android:orientation="vertical"
    android:visibility="gone">

    <LinearLayout android:id="@+id/body_about" … >
        tv_species_extract, btn_wikipedia, card_flowering, card_native_range
    </LinearLayout>

    <LinearLayout android:id="@+id/body_care" … >
        care_slot, card_toxicity
    </LinearLayout>

    <LinearLayout android:id="@+id/body_diseases" … >
        tv_diseases_title, tv_diseases_subtitle, pb_diseases, list_diseases
    </LinearLayout>

    <LinearLayout android:id="@+id/body_fertiliser" … >
        <TextView android:id="@+id/tv_fertiliser_body" … />
    </LinearLayout>

    <LinearLayout android:id="@+id/body_lunar" … >
        <com.plantdiseases.app.ui.widget.LunarCalendarView android:id="@+id/lunar_calendar" … />
    </LinearLayout>

    <LinearLayout android:id="@+id/body_tips" … >
        <TextView android:id="@+id/tv_tips_body" … />
    </LinearLayout>
</LinearLayout>
```

Each `body_*` is `orientation="vertical"`, `layout_width="match_parent"`,
`layout_height="wrap_content"`.

Every moved view keeps its **existing** id, `visibility`, margins and
attributes verbatim — only `layout_marginTop` on the first child of each body
may be dropped. `tv_species_extract` keeps its id so `loadDetail()` still
works. `care_slot` keeps its id so `loadCare()` still works. And so on.

The bottom action row (`btn_add_garden` / `btn_scan_health`) stays where it is,
after the sections.

---

## 9. Wire it in `SpeciesDetailActivity.kt`

Add **one** new method and **one** call. Do not change any existing method.

In `onCreate`, right after `binding.tvSpeciesScientific.text = scientificName`:

```kotlin
attachSections()
```

Then:

```kotlin
/**
 * Moves each pre-declared body block out of the hidden pool and into its
 * ExpandableSectionView. Re-parenting (rather than rebuilding) means every
 * existing loader — loadDetail / loadCare / loadDiseases / loadSpeciesProfile —
 * keeps writing to the very same view ids it always has.
 */
private fun attachSections() {
    val pool = binding.sectionContentPool
    fun move(section: ExpandableSectionView, body: View, icon: Int, title: Int, subtitle: Int) {
        pool.removeView(body)
        body.visibility = View.VISIBLE
        section.bind(icon, title, subtitle)
        section.setContent(body)
    }
    move(binding.sectionAbout, binding.bodyAbout, R.drawable.ic_garden,
        R.string.section_about_title, R.string.section_about_subtitle)
    move(binding.sectionCare, binding.bodyCare, R.drawable.ic_shield_check,
        R.string.section_care_title, R.string.section_care_subtitle)
    move(binding.sectionDiseases, binding.bodyDiseases, R.drawable.ic_pest,
        R.string.section_diseases_title, R.string.section_diseases_subtitle)
    move(binding.sectionFertiliser, binding.bodyFertiliser, R.drawable.ic_fertilize,
        R.string.section_fertiliser_title, R.string.section_fertiliser_subtitle)
    move(binding.sectionLunar, binding.bodyLunar, R.drawable.ic_moon,
        R.string.section_lunar_title, R.string.section_lunar_subtitle)
    move(binding.sectionTips, binding.bodyTips, R.drawable.ic_guide,
        R.string.section_tips_title, R.string.section_tips_subtitle)
    pool.visibility = View.GONE

    // Expanding a section scrolls its header to the top so the freshly
    // revealed content isn't opened below the fold.
    listOf(
        binding.sectionAbout, binding.sectionCare, binding.sectionDiseases,
        binding.sectionFertiliser, binding.sectionLunar, binding.sectionTips,
    ).forEach { section ->
        section.onToggle = { expanded ->
            if (expanded) {
                binding.speciesScroll.post {
                    binding.speciesScroll.smoothScrollTo(0, section.top)
                }
            }
        }
    }
}
```

All sections start collapsed — that is the requested behaviour, and it also
means the (previously always-visible) loading spinners for flowering/native
range no longer flash on screen.

### 9a. Fertiliser + Tips bodies (no new network calls)

Both are filled from the `CarePlanResponse` that `loadCare()` **already**
fetches. At the end of `loadCare()`'s `if (plan != null)` branch add:

```kotlin
bindFertiliser(plan)
bindTips(plan)
```

and add:

```kotlin
/** Feeding section — assembled from the care plan already on hand
 *  (fertilise_weeks / fertiliser_npk / soil_mix), so no extra request. */
private fun bindFertiliser(plan: CarePlanResponse) {
    val lines = buildList {
        add(
            if (plan.fertiliseWeeks > 0) {
                getString(R.string.fertiliser_interval, plan.fertiliseWeeks)
            } else {
                getString(R.string.fertiliser_interval_none)
            },
        )
        plan.fertiliserNpk?.takeIf { it.isNotBlank() }
            ?.let { add(getString(R.string.fertiliser_npk, it)) }
        plan.soilMix?.takeIf { it.isNotBlank() }
            ?.let { add(getString(R.string.fertiliser_soil, it)) }
        plan.seasonNote?.takeIf { it.isNotBlank() }?.let { add(it) }
    }
    binding.tvFertiliserBody.text = lines.joinToString("\n\n")
}

/** Tips section — the care plan's free-text guidance, one paragraph per item. */
private fun bindTips(plan: CarePlanResponse) {
    val lines = listOfNotNull(
        plan.keyAction, plan.placement, plan.acclimation, plan.wateringMethod,
    ).filter { it.isNotBlank() } + plan.redFlags.orEmpty().filter { it.isNotBlank() }
    binding.tvTipsBody.text = if (lines.isEmpty()) {
        getString(R.string.section_empty)
    } else {
        lines.joinToString("\n\n") { "• $it" }
    }
}
```

Initialise both `TextView`s in the layout with
`android:text="@string/section_empty"` so a failed care-plan load still shows
something sensible instead of an empty card.

`LunarCalendarView` needs **no** wiring at all — it computes from the date in
its own `init`.

---

## 10. Unit test

`android/app/src/test/java/com/plantdiseases/app/util/LunarCalendarTest.kt` —
plain JUnit, no Robolectric:

```kotlin
package com.plantdiseases.app.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

class LunarCalendarTest {

    @Test
    fun `known new moon has near-zero age`() {
        assertTrue(LunarCalendar.moonAge(LocalDate.of(2000, 1, 6)) < 1.0)
    }

    @Test
    fun `age stays inside the synodic month`() {
        var date = LocalDate.of(2024, 1, 1)
        repeat(400) {
            val age = LunarCalendar.moonAge(date)
            assertTrue("age out of range on $date", age >= 0.0 && age < 29.6)
            date = date.plusDays(1)
        }
    }

    @Test
    fun `2026-08-18 is a waxing moon in Scorpio`() {
        val info = LunarCalendar.dayInfo(LocalDate.of(2026, 8, 18))
        assertTrue(info.isWaxing)
        assertEquals(LunarCalendar.Zodiac.SCORPIO, info.zodiac)
    }

    @Test
    fun `every day of a month gets a rating and a month has 28-31 entries`() {
        val days = LunarCalendar.month(2026, 8)
        assertEquals(31, days.size)
        assertTrue(days.all { it.rating in LunarCalendar.Rating.entries })
    }

    @Test
    fun `upcoming favourable days are all good and strictly after today`() {
        val from = LocalDate.of(2026, 8, 18)
        val upcoming = LunarCalendar.upcomingFavourable(from, 4)
        assertEquals(4, upcoming.size)
        assertTrue(upcoming.all { it.rating == LunarCalendar.Rating.GOOD })
        assertTrue(upcoming.all { it.date.isAfter(from) })
    }
}
```

Reference values (verified): 2026-08-18 → moon age ≈ 5.34 d, waxing, Scorpio,
longitude ≈ 217.6°.

Run only this:

```bash
./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.LunarCalendarTest"
```

---

## 11. Order of work (recommended)

1. Strings (EN + RU) → colours → drawables. **~10 min.** Nothing compiles
   against them yet, so mistakes are cheap to spot.
2. `LunarCalendar.kt` + its test. **~12 min.** Green test before any UI.
3. `MoonPhaseView.kt`, `ExpandableSectionView.kt`. **~10 min.**
4. `view_lunar_calendar.xml` + `item_lunar_favourable_day.xml` +
   `LunarCalendarView.kt`. **~15 min.**
5. Restructure `activity_species_detail.xml`. **~8 min.**
6. `attachSections()` + `bindFertiliser()` + `bindTips()`. **~5 min.**

---

## 12. Self-check before finishing

- [ ] Every `binding.` reference in `SpeciesDetailActivity.kt` that existed
      before still resolves — no id was renamed or dropped.
- [ ] `loadDetail`, `loadCare`, `loadDiseases`, `loadSpeciesProfile`,
      `renderDiseases`, `addDiseaseRow`, `bindToxicity`, `renderPhotos`,
      `addToGarden` are byte-for-byte unchanged apart from the two added calls
      in `loadCare`.
- [ ] No file under `server/` was touched. No new Gradle dependency.
- [ ] Every new string exists in **both** `values/strings.xml` and
      `values-ru/strings.xml`, with identical `%1$s` / `%1$d` placeholders.
- [ ] Every new colour exists in **both** `values/colors.xml` and
      `values-night/colors.xml`.
- [ ] All six sections start collapsed; tapping a header toggles exactly one
      section; the chevron rotates.
- [ ] `LunarCalendarTest` passes.
- [ ] No wildcard imports; no line over 100 chars; trailing commas on
      multi-line argument lists (ktlint).

## 13. Out of scope (do not do)

- Do **not** touch `PlantDetailActivity` (the *garden* plant screen). It is a
  different screen with journal/timeline/reminders. The new
  `ExpandableSectionView` and `LunarCalendarView` are reusable there later, but
  that is a separate change.
- Do not add lunar reminders, notifications, or WorkManager jobs.
- Do not persist expansion state across launches.
- Do not add a tablet-specific two-column lunar layout.

---

## 14. Kick-off prompt

Paste this to start the implementing session:

> Read `docs/PLANT_SCREEN_SECTIONS_PLAN.md` in full, then execute it end to end
> in this repo. Work through sections 1–10 in the order given in section 11.
> Obey section 0 exactly — especially: no server changes, no new Gradle
> dependency, and **no existing `@+id` in `activity_species_detail.xml` may be
> renamed or deleted** (the restructure is a re-parent only, so every
> `binding.*` reference in `SpeciesDetailActivity.kt` keeps compiling). Add
> every new string to both `values/strings.xml` and `values-ru/strings.xml`,
> and every new colour to both `values/colors.xml` and
> `values-night/colors.xml`. When done, run
> `./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.LunarCalendarTest"`
> and walk the section 12 checklist, reporting any box you could not tick.
