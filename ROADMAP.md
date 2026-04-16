# PlantDiseases — Roadmap v5

> Честный план того, что ещё нужно сделать. Приоритет — красивое, отзывчивое и функциональное Android-приложение. Ничего из уже выполненного в v4 здесь нет.

---

## Аудит: где проект реально слаб

**Android-клиент** работает и покрывает базовые сценарии, но есть неприятные технические долги, реальные баги и UI-шероховатости, которые жюри заметит.

**Сервер** функционален, но Rate-limit на IP + один ThreadPool из 2 потоков = если жюри попробует тестить с двух устройств одновременно, они получат либо 429, либо очередь. Модели не обучены — это блокер.

**Главное ощущение**: приложение напоминает «студенческий проект с хорошим фундаментом». До конкурентов уровня PictureThis / PlantIn не хватает не фич, а полировки: отзывчивости, микро-анимаций, продуманных состояний загрузки, persistent-функционала (календарь ухода), и нескольких «не-банальных» идей.

---

## БЛОК 0 — Критические баги (чинить СЕЙЧАС)

Это не «хотелось бы исправить», это то, что точно крэшнёт на демо или увидит ревьюер.

### 0.1 `ResultActivity.setupPinchToZoom` — сломанный pinch-to-zoom
```kotlin
val imageFrame = binding.cardImage.findViewById<FrameLayout>(R.id.card_image)?.getChildAt(0)
    ?: binding.ivPlant.parent as? View ?: return true
```
- `R.id.card_image` указывает на сам `MaterialCardView` (id самой карточки), а не на FrameLayout внутри. `findViewById` возвращает `cardImage` обратно, `getChildAt(0)` — внутренний FrameLayout, и дальше `scaleX/Y` применяется к неправильному View.
- `setOnTouchListener { ... true }` **всегда** возвращает `true`, что ломает пропаганду событий — при активном zoom-жесте NestedScrollView не сможет скроллиться.
- **Правильно**: дать `FrameLayout` явный id (`@+id/image_frame`), применять scale к нему, и возвращать `scaleGestureDetector.isInProgress` из touch listener, чтобы скролл работал.

### 0.2 `ImageUtils.hasGreenContent` выполняется на main thread
В `CameraFragment.checkPlantAndNavigate` функция вызывается синхронно из `onImageSaved` callback. Внутри — `bitmap.getPixel()` в цикле (медленная операция, до 40K вызовов). На среднем Android это 200-500мс фриз UI после нажатия shutter.
- **Правильно**: вынести в `lifecycleScope.launch(Dispatchers.Default)`, показать progress-индикатор.

### 0.3 `ImageUtils.computeBlurScore` — то же самое
Работает в `lifecycleScope.launch { ... withContext(Dispatchers.IO) {} }` — это ОК, но сама реализация через `getPixel()` медленнее в 20-30 раз чем `getPixels()` на массив.
- **Правильно**: использовать `bitmap.getPixels(pixels, 0, w, 0, 0, w, h)` и работать с `IntArray` напрямую.

### 0.4 `ImageUtils.prepareImageForUpload` — потенциальный OOM
`BitmapFactory.decodeFile(imagePath)` без `inSampleSize` — если пользователь выбрал из галереи фото с камеры на 48Мп, это 180МБ в памяти и мгновенный крэш.
- **Правильно**: двухпроходное декодирование — сначала `inJustDecodeBounds=true`, посчитать `inSampleSize` на основе max dimension, потом декодировать.

### 0.5 `createShareableImage` + `shareWithImage` — OOM и утечка
- `BitmapFactory.decodeFile` без опций — та же проблема.
- Bitmap копируется через `.copy(ARGB_8888)` — двойная память.
- Старые `share_*.jpg` в cache dir никогда не удаляются — накапливаются до очистки кеша системой.
- **Правильно**: downscale до 1920px максимум, очистка старых файлов перед созданием нового (`cacheDir.listFiles { it.name.startsWith("share_") }.forEach { it.delete() }`).

### 0.6 `RetryInterceptor` — retry на POST `/api/analyze`
3 retry × 30сек timeout = пользователь ждёт до 90+ секунд ошибки, думая что приложение висит. Плюс если сервер вернул 500, retry не делается (только на IOException), но при timeout (ConnectException) делается — что на слабой сети означает 3 повторных отправки большого изображения.
- **Правильно**: для `/api/analyze` retry=1 (или 0), только для GET-эндпоинтов делать 3 retry.

### 0.7 `ResultActivity.animateSkeleton` — рекурсивная анимация без stop-guard
```kotlin
.withEndAction {
    child.animate().alpha(0.3f).setDuration(800).withEndAction {
        if (binding.skeletonLayout.visibility == View.VISIBLE) {
            animateSkeleton()
        }
    }.start()
}
```
- Если пользователь быстро вышел — view освобождён, но анимация всё ещё может дёрнуть `animateSkeleton()` и спровоцировать NPE на `binding`.
- **Правильно**: использовать `ValueAnimator` с `repeatMode = REVERSE, repeatCount = INFINITE` и отменять в `onDestroy`.

### 0.8 `HeatmapOverlayView.animateIn` — двойная анимация alpha
```kotlin
animate().alpha(1f).setDuration(800).start()    // анимирует view.alpha
val animator = android.animation.ValueAnimator.ofFloat(0f, 0.6f)  // анимирует overlayAlpha
```
Обе работают параллельно, view.alpha и overlayAlpha — разные вещи, но обе изменяют непрозрачность. Получается «двойное затухание», которое выглядит странно на медленных устройствах.
- **Правильно**: оставить только кастомный ValueAnimator и убрать `.animate().alpha()`.

### 0.9 `HeatmapOverlayView.setDetectionRegionNormalized` — мёртвый код
Функция существует для обратной совместимости, но вызывается только в одном месте (`ResultActivity`) и только когда `regionX == null`. Сейчас это невозможно, потому что сервер всегда отдаёт region. Упрощает код на 20 строк — удалить.

### 0.10 `GalleryAdapter.onBindViewHolder` — конкатенация строк с `%`
```kotlin
tvConfidence.text = "${(item.confidence * 100).toInt()}%"
```
Не поддерживает локализацию (в арабском, например, цифры и проценты пишутся по-другому). Везде использовать `getString(R.string.confidence_format, percent)`.

### 0.11 `ProfileFragment` — `fallbackToDestructiveMigration` не настроен, но миграций только 2
Это не баг сам по себе, но если разработчик добавит поле в `ScanEntity` и забудет миграцию — приложение крэшнётся у всех существующих пользователей при обновлении. Нужны интеграционные тесты на миграции (есть `MigrationTestHelper` в Room).

### 0.12 `classifier.py._classify_demo` — `np.random.choice([])` крэшит
Защита через `if pool else names[0]` есть, но если `names` пустой (редкий случай, если classes.json пустой) — всё равно падёт. Демо-режим должен быть bulletproof.

---

## БЛОК 1 — Блокеры защиты

Без этого жюри можно не звать.

### 1.1 Обучить модели
Не обучено — это уже обсуждалось. Добавлю специфику:
- **Accuracy для защиты**: детектор ≥ 94% (это достижимо за 20 минут на T4 для бинарной задачи), классификатор ≥ 82% (если брать топ-15 самых представленных классов из PlantVillage).
- **Что точно пойдёт не так**: на классификаторе будет перекос — в PlantVillage есть ~10x больше фото `tomato_healthy`, чем `root_rot` (которого там вообще почти нет). Нужен **class-balanced sampler** (`WeightedRandomSampler` в torch) или нужно **убрать классы с <500 изображениями** и переписать `DEFAULT_CLASS_NAMES`. Сейчас список из 15 классов — это «хотелка», которая не соответствует реальному PlantVillage.
- **После обучения** — прогнать на 20+ своих фото с комнатных растений (не из датасета). Если accuracy на этих «в-дикой-природе» фото < 50% — нужно fine-tune на небольшом ручном датасете.
- **Сохранить** `accuracy_detector`, `accuracy_classifier` и `confusion_matrix.png` в `server/models/` и отдавать через `/api/health` для экрана «О модели».

### 1.2 Адаптивный API base URL (без ребилда)
Сейчас URL захардкожен в `build.gradle.kts`. Физическое устройство жюри → нужно менять код и пересобирать. Позор.
- В `ProfileFragment` добавить поле «Адрес сервера» (EditText + Save), сохранять в DataStore.
- `PlantApiClient` читает URL из DataStore при инициализации, откатывается на `BuildConfig.API_BASE_URL` если не задан.
- Плюс кнопка «Проверить подключение» рядом с полем.

### 1.3 Экран «Нет моделей» для демо-режима
Если кто-то клонирует репозиторий и запустит без обученных моделей — сейчас `/api/health` отвечает `pipeline_mode: "demo"`, и приложение шлёт запросы, получает мусор. Ревьюер подумает что модель плохая.
- На старте приложение делает health-check. Если `pipeline_mode == "demo"` — жёлтый банер сверху «Сервер в демо-режиме. Диагнозы будут примерными».
- Это спасает и от ситуации «сервер запустился, но модели не загрузились».

---

## БЛОК 2 — UI/UX polish (ГЛАВНЫЙ ПРИОРИТЕТ)

Это то, что превратит приложение из «студенческого» в «продуктовое». Фокус на мелочах, которые накопительно создают ощущение премиум.

### 2.1 Цветовая палитра — переосмыслить
Текущая палитра (`#2E7D32`, `#C8E6C9`) — стандартный Material green. Скучно и предсказуемо.
- **Предложение**: живая, «ботаническая» палитра.
  - Primary `#1F7A3F` (более приглушённый, благородный зелёный)
  - Secondary `#D4A574` (тёплый охристый — для CTA и акцентов, контрастирует с зелёным)
  - Primary container `#D7EDE0` (мягкий, не кислотный)
  - Surface elevated `#FAFDF9` (чуть зеленоватый тёплый белый)
  - Healthy `#2E8B57` (sea green, не лайм)
  - Disease `#C44536` (терракотовый, не кислотный красный)
- **Тёмная тема**: сейчас `#66BB6A` primary — слишком яркий и «прыгает» на OLED. Заменить на `#7FB285` (пастельный), фон `#0F1210` вместо `#121212`.
- **Material You** на Android 12+: включить `DynamicColors.applyToActivitiesIfAvailable(this)` в `PlantDiseasesApp` — тогда на новых устройствах приложение автоматически возьмёт обои пользователя. Это wow-деталь, которая сегодня стоит ~5 строк кода.

### 2.2 Skeleton screens вместо спиннеров — везде
Сейчас skeleton есть только в `ResultActivity`. Пусто и скучно в других местах:
- **Galleryfragment**: до загрузки списка показывать 6 placeholder-карточек с shimmer-эффектом. Библиотека `com.facebook.shimmer:shimmer` — 10KB, стоит того.
- **ProfileFragment**: пока грузятся stats, карточки показывают placeholder'ы с shimmer, а не «0».
- **GuideDetailSheet**: открытие bottom sheet сейчас — instant, потому что данные в памяти. OK, пропустить.

### 2.3 Отзывчивость камеры
Сейчас при нажатии shutter: вибрация (ОК) → через ~200-500мс переход в AnalysisActivity. Выглядит как лаг.
- **Shutter-flash анимация**: затемнение экрана на 50мс + белая вспышка сверху. Это микро-деталь, но даёт ощущение «настоящей камеры».
- **Pinch-to-zoom на preview**: CameraX поддерживает через `camera.cameraControl.setZoomRatio()`. Добавить `ScaleGestureDetector` на `cameraPreview`. Индикатор zoom (например, «1.5x») внизу справа.
- **Haptic на успешный focus**: после `startFocusAndMetering().addListener { ... }` — лёгкая вибрация 20мс если `result.isFocusSuccessful`.
- **Grid overlay** (правило третей) — toggle в profile. Просто два горизонтальных и два вертикальных полупрозрачных line-ов поверх preview.

### 2.4 Экран результата — reduce visual noise
Сейчас 6+ карточек подряд — тяжело. Пользователь скроллит как в туннеле.
- **Sticky header**: при скролле название болезни и confidence зафиксированы сверху в компактном виде (как в iOS App Store при скролле страницы приложения). Это даёт постоянный контекст без занимания места.
- **Объединить карточки**: «Status» + «Confidence» + «Date» — это одна логическая группа. Сейчас это три отдельные карточки. Объединить в одну card с 3 строками, и высвободить ~80dp вертикального пространства.
- **Tabs** между «Treatment» и «Prevention» вместо двух карточек подряд (как в iOS Health app).
- **Collapse guide CTA**: когда есть low-confidence warning + retry button + share + delete — в нижней части кнопки стекаются в лесенку. Заменить на один bottom action bar с тремя иконками.

### 2.5 Анимации и переходы
- **Shared element transition** на переходе Gallery → Result: фото растения плавно увеличивается в новую активити, а не просто исчезает. Требует `ActivityOptions.makeSceneTransitionAnimation` + `android:transitionName` на обеих сторонах. 30 минут работы, wow-эффект огромный.
- **Motion layout для hero image**: в ResultActivity фото растения на старте занимает весь верх. При скролле вверх — уменьшается до 120dp и сдвигается влево, рядом появляется название. Реализуется через `AppBarLayout.ScrollingViewBehavior` + collapsing toolbar.
- **Нет ripple** на некоторых CardView — добавить `android:foreground="?attr/selectableItemBackground"` на всех кликабельных карточках. Сейчас стоит только на `cardHowToUse`.
- **Stagger animation** для списков — когда открывается Gallery, карточки появляются с задержкой 40мс каждая (scale 0.9→1.0, alpha 0→1). RecyclerView item animator с `LayoutAnimationController`.
- **Lottie для confidence bar** в Top-3: сейчас `LinearProgressIndicator` анимируется стандартно — заменить на кастомную анимацию с easeOutCubic кривой и задержкой по позиции (главный результат анимируется первым, остальные — на 150мс позже каждый).

### 2.6 Haptic feedback — точечно
Haptic — это то, что превращает «цифровой продукт» в «тактильный». Но нельзя перегружать.
- ✓ Вибрация на shutter — уже есть.
- **Добавить**: вибрация-успех (паттерн «короткий-пауза-короткий») когда приходит результат анализа.
- **Добавить**: лёгкая вибрация (10мс) при свайпе между chip-фильтрами в Gallery.
- **Не добавлять**: вибрация на каждый клик (раздражает).

### 2.7 Empty states — каждый уникален
Сейчас Gallery использует общий Lottie `empty_plants.json`, Profile — текст «No scans yet». Оба скучные.
- **Gallery empty**: Lottie остаётся, но добавить CTA-кнопку «Сделать первый скан» которая переключает на вкладку Scan.
- **Profile empty**: то же самое, с CTA. + «Покажите статистику своим друзьям» заглушка для share-ссылки (пока не работает, но намекает на будущее).
- **Guide search без результатов**: сейчас просто пустой список. Добавить иллюстрацию + текст «Ничего не найдено по запросу "...". Попробуйте другое слово».
- **Gallery filter без результатов** (нажал «Healthy» но нет здоровых растений): «Пока нет ничего в этой категории. Продолжайте сканировать — ваша зелёная полка растёт!»

### 2.8 Search UX в Guide
- Запрос длиной < 2 символа не должен триггерить поиск (отсекает шум).
- **Debounce** 300мс — сейчас каждая буква запускает перефильтрацию, на длинных списках тормозит.
- **Highlight** совпадений в названиях жёлтым цветом (`SpannableStringBuilder` + `BackgroundColorSpan`).
- **Clear button** (X) в поле когда есть текст.
- **Recent searches** — последние 5 запросов показываются при фокусе на пустом поле (chip-row). DataStore.

### 2.9 Tap-to-focus — настоящий
Сейчас индикатор фокусировки это drawable-ring. Не передаёт обратной связи об успехе.
- Подписаться на `future.addListener { result -> ... }`:
  - `isFocusSuccessful` = true → индикатор становится зелёным и плавно исчезает.
  - = false → краснеет и дрожит (translate animation ±3dp).
- Это 15 строк кода, но даёт ощущение что камера «живая».

### 2.10 Нижняя навигация — индикатор состояния
- **Badge** на иконке Gallery когда есть новый результат (после анализа). Исчезает при открытии вкладки. `BottomNavigationView.getOrCreateBadge(R.id.galleryFragment).number = 1`.
- **Subtle bounce** на иконку Scan при первом открытии приложения (после онбординга) — подсказка «начни отсюда».

### 2.11 Edge-to-edge — долечить
Есть, но:
- На экране Camera статус-бар с системными иконками накладывается на preview — нужны контрастные иконки или dark scrim сверху.
- На Onboarding нижняя системная навигация «съедает» дот-индикаторы на некоторых устройствах с gesture navigation. Добавить `fitsSystemWindows` или WindowInsets padding на `bottom_section`.

### 2.12 Плавная смена языка
Сейчас при смене языка через меню вызывается `recreate()` — экран моргает. На современных Android 13+ (API 33) есть `AppCompatDelegate.setApplicationLocales()` — он делает это без moргания (система сама handle'ит). Fallback на старый метод для < API 33.

### 2.13 Accessibility baseline
- **contentDescription** на всех `ImageView` в layout'ах (сейчас есть только на ImageButton'ах).
- **minTouchTargetSize**: `btnFlash` — 48x48dp ✓, но `btnClose` в sheet — 36x36dp. Минимум 48x48 по Material guidelines.
- **Text contrast**: `@color/on_surface_secondary = #5F6B5E` на фоне `#F4F8F4` даёт contrast ratio ~4.3. Этого едва хватает для AA. Затемнить до `#4A5549`.
- **Тест** с TalkBack по 3 ключевым сценариям (онбординг → скан → результат). Ни один популярный plant-app из наших конкурентов нормально не работает с TalkBack — это реальная точка отстройки для защиты.

---

## БЛОК 3 — Wow-фичи, которых нет у конкурентов

Не все сразу — выбирать 1-2 из этого списка, прицельно.

### 3.1 ⭐ Real-time leaf detection в камере
PictureThis, PlantIn, Plantix — ни у одного нет. Пользователь наводит камеру — приложение в реальном времени рисует контур обнаруженного листа и меняет цвет scan-frame (красный = лист не найден, жёлтый = найден но блики, зелёный = готов к снимку).
- Реализация: CameraX `ImageAnalysis` use case, каждый 5-й кадр (200мс), даунсемпл до 160px, HSV-анализ (то же что и `hasGreenContent`, но с детекцией контура через `Canny edge detection` из OpenCV для Android).
- **Альтернатива без OpenCV**: просто HSV green ratio + centroid position. Если больше 30% зелёного и он сконцентрирован в центре — зелёная рамка, иначе красная.
- Это **2-3 дня работы** но в защите демонстрируется за 5 секунд и запоминается.

### 3.2 ⭐ Plant Tracker (персональный «дневник» растения)
Самый популярный feature у PictureThis и PlantIn — «My Garden». Но у конкурентов это замаскировано под pay-wall. У нас — бесплатно.
- **Что**: пользователь может назвать растение («Папоротник в спальне»), привязать к нему сканы. На экране растения — timeline сканов с графиком confidence и отметками «было больно → вылечили».
- **Новая Room-entity**: `Plant(id, name, species, createdAt, iconEmoji)`. `ScanEntity` получает nullable `plantId`.
- **Новая вкладка или раздел в Profile**: «Мои растения».
- **Это даёт**: долгую retention (пользователь возвращается смотреть историю) + эмоциональная привязка (удачно вылеченное растение → позитив).

### 3.3 ⭐ Напоминания о поливе и проверке
Без них приложение — одноразовый инструмент. С ними — часть рутины.
- После успешного лечения: «Проверить через 7 дней?» → WorkManager + notification.
- Для растений в трекере (3.2): настраиваемый график полива. Интервал по умолчанию — из `DISEASES_DATABASE` в зависимости от состояния.
- **Not** полноценный care-assistant как у PictureThis — это overkill. Только 2-3 типа напоминаний.

### 3.4 Symptom checklist — гибридный диагноз
Что если AI не уверен (confidence < 50%)? Сейчас показываем warning и всё. А можно дать пользователю продолжить диагностику вручную.
- **UI**: chip-row с симптомами («Белый налёт», «Жёлтые пятна», «Паутина», «Мягкие стебли», ...).
- Каждый выбранный симптом → байесовский пересчёт вероятностей из `all_probs` + матрица «симптом × болезнь».
- Матрицу захардкодить в `symptoms_matrix.json` — 15 болезней × 10 симптомов.
- **Это**: хороший argumentation moment на защите — «у нас не чёрный ящик, мы даём пользователю control».

### 3.5 Voice summary
На экране результата маленькая кнопка «🔊» → TTS читает диагноз и первые 2 шага лечения.
- Android TTS встроенный, русский и английский голоса есть из коробки.
- **Полезно**: для визуально impaired пользователей, для мам-на-кухне, для атмосферы демо.
- **30 строк кода**.

### 3.6 Offline-диагноз через TFLite
Это **большой** блок, но это единственная технически сложная wow-фича, которая реально выделит проект.
- Конвертация EfficientNet-B0 → TFLite (float16 quantization → ~5МБ модель).
- `org.tensorflow:tensorflow-lite` + `org.tensorflow:tensorflow-lite-gpu`.
- **Работа**: 3-5 дней на реализацию + тестирование парити с сервером.
- **Защита**: «Приложение работает без интернета» — аргумент уровня production.
- Если не хватает времени — **оставить как "future work"** в презентации, но упомянуть что архитектура это поддерживает.

### 3.7 AR-оверлей результата на фото (мини)
Не полноценный AR, но: на сохранённой фотографии растения поверх heatmap показываются **пульсирующие точки-маркеры** с tooltip'ами при тапе («Здесь начало некроза», «Здесь конкретно грибок»). Генерируется из `all_probs` — топ-3 признака.
- Больше для wow, чем для пользы.

### 3.8 Тёмный «ботанический» easter egg
Долгое удержание логотипа на экране Profile → экран анимации: летят листья, опадают на «компост», из компоста прорастает цветочек, показывает версию приложения. 1 Lottie-файл, 5 строк кода. Заставит жюри улыбнуться.

---

## БЛОК 4 — Production-grade

Что отделяет проект от статуса «игрушка».

### 4.1 Crash reporting
Нет ничего. В первый же раз, как приложение упадёт у стороннего пользователя, мы не узнаем.
- **Firebase Crashlytics** — бесплатно, 15 минут на настройку.
- **Альтернатива**: Sentry (self-hosted).
- Логировать custom events: «server_timeout», «low_confidence_result», «blurry_photo_warn_shown».

### 4.2 Интеграционные тесты на Room миграции
Сейчас `MIGRATION_1_2` и `MIGRATION_2_3` написаны — но они не тестируются. Если в v4 добавим поле и сломаем миграцию — все существующие пользователи крэшнутся.
- `androidx.room:room-testing` + `MigrationTestHelper`.
- Тесты для каждой миграции: создать DB с предыдущей схемой, наполнить, мигрировать, проверить что данные целы.

### 4.3 ProGuard — верификация release APK
`minifyEnabled = true` включено, но release APK никто не запускал. Типичные баги:
- `Gson.TypeToken` — правила есть в proguard, но надо проверить что `parseAllProbs` реально работает в release.
- Retrofit service interface — Kotlin metadata может быть stripped.
- **Надо**: собрать `./gradlew assembleRelease`, установить на устройство, прогнать все основные сценарии.
- Включить `R8 full mode` (`android.enableR8.fullMode=true`) — даёт +2-3МБ экономии на APK.

### 4.4 Сервер: production-checklist
- ✗ `CORS_ORIGINS=*` по умолчанию — в `docker-compose.yml` ставить реальный origin (или публично `"*"` допустим для публичного API, но не для internal).
- ✗ `RateLimitMiddleware` использует in-memory dict — сбрасывается при рестарте. Для single-instance OK, для production нужен Redis.
- ✗ Нет health-probe у Docker — добавить `HEALTHCHECK CMD curl -f http://localhost:8000/api/health || exit 1`.
- ✗ Логи ошибок без traceback — в `main.py` при HTTPException 500 логировать `exc_info=True`.
- ✗ `/api/analyze` не проверяет magic bytes — пользователь может отправить `.jpg` с расширением но PDF-содержимым. `PIL.Image.open` защищает от крэша, но хорошо бы явный check.

### 4.5 Метрики на сервере
- `/api/metrics` endpoint в Prometheus-формате: `requests_total`, `inference_duration_seconds`, `classes_distribution`.
- На защите можно показать Grafana-дешборд с live-трафиком = 👀.

### 4.6 CI / релиз-пайплайн
GitHub Actions:
1. На push в `main` → `./gradlew :app:lint :app:testDebug :app:assembleDebug`.
2. На тег `v*` → билд release APK + auto-upload в GitHub Releases.
3. Для сервера — `docker build` и push в ghcr.io.

### 4.7 Приватность и permissions
Сейчас в `AndroidManifest.xml` запрашивается `VIBRATE` — но Android >= 26 это «normal permission» и grant автоматом, ОК. Но:
- Добавить экран «Privacy» в Profile: что мы собираем (ничего), куда шлём (только на указанный сервер), хранится ли на сервере (нет).
- Это 5 минут работы, но важно для Google Play (если когда-нибудь будет).

---

## БЛОК 5 — Code quality

Плохой код, который работает, но раздражает тех кто будет смотреть репозиторий.

### 5.1 `ResultActivity` — god-class (500+ строк)
Одна активити делает: skeleton, heatmap rendering, Top-3 alternatives, treatment/prevention steps, share logic, delete confirmation, retry, pinch-to-zoom, animation orchestration.
- **Рефакторинг**: извлечь `AlternativesRenderer`, `StepsRenderer`, `ShareHelper` как отдельные классы.
- Ввести `ResultViewModel` с `StateFlow<ResultUiState>` — Activity только подписывается и рендерит.
- Это **не блокер** для защиты, но ревьюер который откроет файл скажет «вот вот, классика студенческого кода».

### 5.2 `GuideDataProvider` — 3000 строк хардкод-контента в `.kt`
Это главный раздутый файл в проекте.
- Перенести в `assets/guide_en.json` + `guide_ru.json`.
- Парсить при старте, кешировать.
- Выигрываем: APK уменьшается, контент можно обновлять без ребилда (в будущем можно пулить с сервера).

### 5.3 `DISEASES_DATABASE` в `server/diseases_data.py` — дублирование с Guide
Сейчас текст болезней есть и на сервере (для ответа `/api/analyze`), и на клиенте (в Guide). Если поправил на сервере — на клиенте старая версия.
- Клиент должен получать описания из ответа сервера (они уже есть в `AnalysisResponse`) и **не иметь** дублирующихся в Guide.
- Guide в клиенте — только общая справочная информация (полив, свет, вредители).

### 5.4 Отсутствие ViewModel / Repository pattern
Весь state управляется из Activity/Fragment через `lifecycleScope.launch`. Рабоче, но:
- При смене конфигурации (rotation) — загрузка начинается заново. Сейчас экраны locked на portrait, поэтому не видно, но это костыль.
- Тестирование невозможно — бизнес-логика прибита гвоздями к UI.
- **Рефакторинг**: ViewModel для каждого экрана, StateFlow для состояний, бизнес-логика в use-case классах.
- Тяжело внести сейчас — **оставить как future work**, но в презентации упомянуть что осознаём ограничение.

### 5.5 Dependency injection
Сейчас `PlantDiseasesApp` — service locator (`by lazy` свойства). Для проекта такого размера допустимо, но для production обычно Hilt.
- **Не трогать**, если нет переизбытка времени. Hilt-миграция на существующий код — минимум день.

### 5.6 `LocaleHelper` — deprecated Locale constructor
`Locale(language)` — deprecated с API 21, в Android 14 warning. Нужно `Locale.forLanguageTag(language)` (или `Locale.Builder`).

### 5.7 Magic numbers и hardcoded strings
- `HEALTHY_SKIP_THRESHOLD = 0.65` в `pipeline.py` — почему 0.65? Нигде не задокументировано.
- `blurScore < 100.0` в `AnalysisActivity` — почему 100? Константа должна быть в `ImageUtils` с комментарием.
- `0.5f` для low-confidence warning в `ResultActivity` — в `Config.kt` / companion object.

### 5.8 `server/main.py` — глобальные переменные `SERVER_START_TIME`, `TOTAL_REQUESTS`
Мутабельный глобальный state. Для single-worker OK, но для multi-worker (gunicorn с несколькими воркерами) статистика будет неточной. Для student проекта допустимо — **не трогать**, только добавить комментарий «single-worker only».

---

## БЛОК 6 — Мелкие полировки

Не влияют на архитектуру, но каждая — «ещё один штрих».

### 6.1 Камера
- **Длинное удержание shutter** → серия из 3 фото с небольшим интервалом. Пользователь потом выбирает лучший.
- **Audio cue** на shutter — системный звук камеры (можно отключить в настройках).
- **Last-photo-preview** в углу кнопки shutter (как в стоковом Android Camera) — крошечный круглый thumbnail последнего скана.

### 6.2 Gallery
- **Bulk selection mode**: длинное удержание → чекбокс, можно выбрать несколько и удалить.
- **Sort**: по дате (default), по болезни, по confidence.
- **Grid density toggle** — 2 или 3 колонки.

### 6.3 Guide
- **«Была полезна эта статья?»** — thumbs up/down в конце каждой статьи. Сохранять локально, по аналитике понимать какие статьи работают.
- **Related articles** внизу каждой статьи — 2-3 связанные.

### 6.4 Result
- **Copy diagnosis text** — long-press на карточке с описанием копирует в clipboard с toast «Скопировано».
- **Print as PDF** — Android `PrintManager` → PDF, готовый для отправки ветеринару… то есть агроному.

### 6.5 Profile
- **Export history** — кнопка «Экспортировать историю в CSV», сохраняется в Downloads через `MediaStore` API.
- **App icon variants** (Android 13+ `<adaptive-icon>` с monochrome layer — иконка меняется в теме Material You).

### 6.6 Onboarding
- **Skip-to-camera** на последнем слайде — вместо «Начать» кнопка «Разрешить доступ к камере и начать», которая запросит permission сразу.
- **Progress dots** с haptic на свайп.

---

## Порядок работ (приблизительный, неделями)

```
Неделя 1 — Основа
├─ 0.1-0.12 Критические баги  (2-3 дня)
├─ 1.1     Обучить модели    (1 день на Colab)
└─ 1.2     Настраиваемый API URL (2 часа)

Неделя 2 — UI rebuild, главная неделя
├─ 2.1     Палитра (включая Material You)
├─ 2.2     Skeleton screens для Gallery/Profile
├─ 2.3     Камера: pinch-zoom + shutter flash + focus feedback
├─ 2.4     Result: sticky header + объединить карточки + tabs
└─ 2.5     Shared element transition Gallery → Result

Неделя 3 — Отличительное
├─ 3.2     Plant Tracker
├─ 3.3     Care reminders (базовая версия)
└─ 2.7-2.8 Empty states + search UX

Неделя 4 — Polish и production
├─ 2.6, 2.9-2.13  Haptic, accessibility, edge-to-edge
├─ 4.1-4.3        Crashlytics, Room migration tests, release APK
└─ 5.1-5.2        Refactor ResultActivity + guide to JSON

Опционально (если время есть):
├─ 3.1     Real-time leaf detection     (3 дня)
├─ 3.6     TFLite on-device             (5 дней)
└─ 3.4     Symptom checklist            (2 дня)
```

---

## Метрики готовности к защите

| Метрика | Сейчас | Минимум | Цель |
|---------|--------|---------|------|
| Detector accuracy | — | 92% | 95% |
| Classifier accuracy | — | 78% | 85% |
| Время анализа на CPU | <1с демо | <3с | <2с |
| Release APK собирается | ⚠️ не проверено | ✓ | ✓ |
| Размер APK | ~25МБ (оценка) | <30МБ | <20МБ |
| Crash-free sessions (24ч тест) | — | 100% | 100% |
| Accessibility (TalkBack) | ✗ | базовый | полный |
| Smooth 60fps при скролле Gallery | ⚠️ не проверено | 60fps | 60fps |
| Дополнительный wow-feature | ✗ | 1 | 2+ |
| Работа без интернета | ✗ (только справка) | — | TFLite |

---

## Что брать из конкурентов, что игнорировать

**PictureThis** (лидер рынка, 70M+ пользователей) позиционируется как identifier + disease diagnosis + care tool. Стоит взять: **care reminders**, **light meter** через сенсоры телефона (датчик освещённости — есть в Android Sensor.TYPE_LIGHT), **my garden** как persistent-коллекция. Игнорировать: expert consultation (нереально для студ-проекта), toxicity warnings (наш датасет — про болезни, не таксономию), paywall-модель.

**PlantIn** делает ставку на care-календарь и более «приветливый» UI. Стоит взять: **свободный care tier** как конкурентное преимущество (у конкурента большинство фич — за paywall), **personalized care plans**.

**Plantix** — для фермеров, community-based. Игнорировать почти всё — это другая ниша (outdoor crops). Взять только: **offline capabilities** как долгосрочную цель.

**Agrio** — серьёзный сельхоз-инструмент, weather-integration. Для нашей домашней ниши не подходит.

**Наше отличие** формулируется в одной фразе: *«Единственное приложение с объяснимой AI-диагностикой (Grad-CAM + top-3 alternatives) и настоящим уважением к пользователю (без paywall, без навязчивых подписок, работает быстро и плавно)»*. Это и есть главный аргумент для защиты.

---

## Красная черта: что реально критично, а что декорация

Если придётся выбирать между всеми пунктами, минимальный must-have:

1. **Блок 0** — весь. Баги исправить.
2. **1.1** — обучить модели.
3. **2.1** — новая палитра. (1 час, огромный эффект.)
4. **2.2** — skeleton везде. (3 часа, ощутимо ощутимее.)
5. **2.4** — упростить Result-экран. (4 часа.)
6. **2.5** — shared element transition. (30 минут.)
7. **3.2** — Plant Tracker базовый. (1-2 дня.)
8. **4.3** — release APK проверить.

Всё остальное — плюсы сверху. Но именно эти 8 пунктов превращают проект из «OK для сдачи» в «помню этот проект, это был хороший проект».
