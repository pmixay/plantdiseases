# PlantDiseases — Roadmap v4

> Актуальный план: что реально сделано, где проект слаб, и что нужно до production-grade + wow-эффекта на защите.

---

## Аудит текущего состояния

### Реально работает

**Android-клиент**
- 4 вкладки (Scan / Gallery / Guide / Profile), Material 3, Navigation, CameraX, Room, Retrofit
- Сканер: камера + вспышка + рамка + hint + tap-to-focus + вибрация на capture + offline-banner
- HSV-проверка «это растение?» перед отправкой на сервер
- Экран анализа: Lottie-анимация сканирования, retry, кастомные сообщения для timeout / network error
- Экран результата: heatmap-overlay, анимированные карточки, нумерованные шаги лечения/профилактики, share, delete, low-confidence warning
- Галерея: Grid, swipe-refresh, long-press delete, фильтры (все / здоровые / больные), Lottie empty state, относительные даты («Сегодня», «Вчера», «3 дн. назад»), Glide DiskCacheStrategy.ALL
- Справочник: 22 статьи в 5 категориях, поиск по всем категориям, уникальные цвета иконок по категории, bottom-sheet с детальным контентом
- Профиль: статистика, server health indicator, переключатель темы (Light / Dark / System), версия, clear history
- Splash screen (Android 12+ API), Onboarding (3 страницы с Lottie)
- Тёмная тема (values-night/colors.xml + themes.xml)
- RU/EN в тулбаре, все экраны локализованы
- RetryInterceptor (exponential backoff 1s/2s/4s), ServerTimeoutException, NoNetworkException
- ProGuard-правила для Retrofit/OkHttp/Gson/Room/Glide/Lottie
- Room с миграцией v1→v2 (индексы на timestamp и is_healthy)

**Сервер**
- FastAPI, two-stage pipeline, Grad-CAM для ROI
- Demo-режим с HSV-эвристиками работает без обученной модели
- Rate limiting (1 req/sec per IP), RotatingFileHandler logging, CORS через env
- `/api/health` отдаёт uptime и total_requests
- `Dockerfile` + `docker-compose.yml` есть
- Динамическая загрузка классов из `models/classes.json`
- Training script (`train.py`) + Colab-ноутбук готовы
- Startup-скрипты для Linux и Windows с CPU-only PyTorch

---

## Критичные проблемы (блокеры)

| # | Проблема | Где | Почему важно |
|---|----------|-----|--------------|
| 1 | **Модели НЕ обучены** | `server/models/` пусто | Без моделей весь ML-пайплайн — фейк. Demo-режим даёт случайные результаты |
| 2 | **Heatmap-overlay рисуется по хардкоженным координатам** | `ResultActivity.kt`: `setDetectionRegion(0.5f, 0.45f, 0.35f)` | Сервер возвращает реальный `detection.region`, но Android его игнорирует. На защите это обман — жюри увидит, что рамка всегда в одном и том же месте |
| 3 | **`server/model.py` сломан** | `from classifier import CLASS_NAMES` | `CLASS_NAMES` теперь instance-атрибут внутри `DiseaseClassifier`, а не модульная переменная. Любой код, импортирующий старый модуль, упадёт |
| 4 | **`detection.region` не сохраняется в Room** | `ScanEntity` | После переоткрытия результата рамка не восстанавливается — даже если бы использовались реальные данные |
| 5 | **`all_probs` не доходят до Android** | `AnalysisResponse` | Сервер отдаёт все 15 вероятностей, `ScanRepository` их теряет. Top-3 альтернативных диагноза невозможно показать |
| 6 | **LICENSE vs README расхождение** | Корень проекта | `LICENSE` = Unlicense, README говорит MIT |

---

## Блок 1 — Обучение и целостность ML-пайплайна

Без этого всё остальное теряет смысл.

### 1.1 Обучение моделей
- [ ] Скачать PlantVillage dataset, прогнать Colab-ноутбук на T4 GPU
- [ ] Detector (бинарный) — target accuracy ≥ 92%
- [ ] Classifier (15 классов) — target accuracy ≥ 80%
- [ ] Положить `detector.pth` + `classifier.pth` + `classes.json` в `server/models/`
- [ ] Убедиться что `/api/health` возвращает `pipeline_mode: "full"`
- [ ] Протестировать на 20+ реальных фото комнатных растений (не из датасета)

### 1.2 Валидация качества
- [ ] Построить confusion matrix, найти классы с accuracy < 70%
- [ ] Замерить реальную latency на CPU (target < 2 сек end-to-end)
- [ ] Если accuracy слабая — расширить датасет (iNaturalist для слабых классов) и переобучить
- [ ] Добавить калибровку вероятностей (temperature scaling) — модели после обучения часто переуверены

### 1.3 Фикс сломанного кода
- [x] Удалить `server/model.py` целиком или переписать импорт
- [x] Синхронизировать LICENSE и README (Unlicense — проще и честнее)
- [ ] Проверить что `server/__init__.py` не мешает импортам

---

## Блок 2 — Wow-эффект для жюри

Здесь проект сейчас слаб — нет того, что запоминается.

### 2.1 Настоящий Grad-CAM (не декорация) — TOP приоритет
- [x] Расширить `AnalysisResponse`: добавить поля `regionX`, `regionY`, `regionWidth`, `regionHeight`, `imageWidth`, `imageHeight`
- [x] Сохранять их в `ScanEntity` (нужна миграция Room v2→v3)
- [x] `HeatmapOverlayView.setDetectionRegion` должна принимать пиксельные координаты из сервера, нормализовать к размеру ImageView и только тогда рисовать overlay
- [x] Учесть `scaleType="centerCrop"` — координаты сервера в координатах оригинального фото, ImageView его кропает. Нужна матрица трансформации
- [x] Для здоровых растений overlay скрывается — это уже работает
- [ ] Добавить toggle «Показать/скрыть область» — жюри сможет сравнить «до» и «после»

### 2.2 Объяснимость: top-3 альтернативных диагноза
- [x] Прокинуть `all_probs` через `AnalysisResponse` → `ScanEntity` (JSON-строка)
- [x] На экране результата карточка «Почему мы так решили?»:
  - Основной диагноз + 2-3 альтернативы
  - Горизонтальные confidence-bar'ы для каждого
  - Кликабельные — ведут в справочник по этой болезни
- [x] Это главный аргумент для жюри про «explainable AI»

### 2.3 Экран «О модели»
- [x] В Profile добавить пункт «О модели и точности»
- [x] Показать: архитектура (MobileNetV3 + EfficientNet), количество параметров (~8M), количество классов, accuracy на валидации (хардкодом из результатов обучения), краткое описание two-stage пайплайна
- [ ] Можно добавить скриншот confusion matrix (из ноутбука) как ассет

### 2.4 Share с изображением
- [x] Сейчас share только текст. Добавить картинку через `FileProvider` и `Intent.EXTRA_STREAM`
- [x] Если реализован Grad-CAM — шарить фото с наложенной heatmap (потребует `Bitmap` композиция)

---

## Блок 3 — Production-grade надёжность

### 3.1 Android — устойчивость сети и данных
- [x] Обработка 429 Too Many Requests — отдельное сообщение «сервер загружен, подождите»
- [ ] Проверка размера изображения перед отправкой (max 10 MB) — сервер и так отклонит, но лучше не делать лишний запрос
- [x] Проверка качества фото (Laplacian variance → если < threshold, предупредить «фото размытое»)
- [x] Состояние загрузки в `ResultActivity` пока `getScanById` возвращает результат (сейчас briefly шот пустой layout)

### 3.2 Room миграции и данные
- [x] Миграция v2→v3 для новых полей region + `all_probs` (объединены в одну миграцию)
- [x] Добавить `fallbackToDestructiveMigration()` ТОЛЬКО как крайний вариант — сейчас его нет, и это правильно, но миграции нужно писать аккуратно

### 3.3 Сервер — безопасность и надёжность
- [x] Валидация разрешения изображения (не принимать 4096+ — OOM)
- [x] Таймаут на инференс (`asyncio.wait_for` 30s) чтобы один «плохой» запрос не блокировал пул
- [ ] В production убрать `allow_origins=["*"]` — заставить явно задавать через `CORS_ORIGINS`
- [ ] Логирование ошибок в отдельный файл (`error.log`) с полным traceback

### 3.4 Release-сборка
- [x] Добавить signing config в `build.gradle.kts` (keystore вне VCS, путь через gradle properties)
- [x] ProGuard-правила обновлены для Retrofit/Room/Lottie/Gson TypeToken
- [x] `minifyEnabled = true` + `isShrinkResources = true` включены

### 3.5 Docker — проверка
- [ ] Собрать образ локально (`docker build`) и убедиться что стартует с обученными моделями (volume mount)
- [ ] Проверить что `/api/health` доступен из host-а

---

## Блок 4 — UX polish и отзывчивость

### 4.1 Камера
- [ ] Pinch-to-zoom на preview (CameraX поддерживает через `CameraControl.setZoomRatio`)
- [ ] Grid overlay (правило третей) — toggle в настройках
- [ ] Индикатор обработки кадра после capture (blur детекция) — если фото размыто, показать toast «сделайте резче»
- [ ] Анимация shutter — затемнение экрана на 50мс при нажатии capture

### 4.2 Галерея
- [ ] Transition animation при переходе в ResultActivity (shared element — фото растения плавно «растёт» в новый экран)
- [ ] Контекстное меню (3 точки на карточке) — share, delete, без long-press
- [ ] Поиск по истории (если > 20 сканов)
- [ ] Bulk select для удаления нескольких

### 4.3 Экран результата
- [x] Pinch-to-zoom на фото растения — ScaleGestureDetector на карточке с фото
- [x] Skeleton loader пока грузится scan из Room
- [x] Кнопка «Повторить анализ» (re-analyze) — отправить то же фото ещё раз (при low confidence)
- [ ] При диагнозе disease — кликабельная ссылка «Подробнее в справочнике»

### 4.4 Общее UI
- [x] Edge-to-edge display (прозрачный status bar, контент под системными барами)
- [ ] Accessibility — content descriptions на всех ImageButton, тестирование с TalkBack
- [ ] Landscape режим хотя бы для ResultActivity и GuideDetailSheet (сейчас все залочены на portrait)
- [ ] Ripple effect на всех кликабельных карточках — не везде есть

### 4.5 Haptics
- [ ] Лёгкая вибрация при переключении вкладки (опционально, через настройку)
- [ ] Вибрация при успешном анализе (результат появился)

### 4.6 Первое впечатление
- [ ] Animated splash (Android 12+ API поддерживает AnimatedVectorDrawable) — сейчас статичный вектор
- [x] Skip онбординга работает, но после первого запуска нет способа его пересмотреть — добавить пункт в Profile «Как пользоваться приложением»

---

## Блок 5 — Слабые места для защиты

Это то, что жюри заметит и на чём можно «посыпаться».

| Слабость | Риск | Что делать |
|----------|------|-----------|
| ~~Heatmap в одном и том же месте каждый раз~~ | ~~Жюри заметит за 10 секунд~~ | **Готово** — реальные Grad-CAM координаты |
| ~~Нет метрик точности модели нигде в UI~~ | ~~«А ваш ИИ вообще работает?»~~ | **Готово** — экран «О модели» в Profile |
| Demo-режим выдаёт случайные результаты | Если сервер упал до live-demo — будут рандомные диагнозы | Обучить модели + иметь backup видео демо |
| ~~Нет объяснения «почему именно эта болезнь»~~ | ~~«А что если он ошибся?»~~ | **Готово** — top-3 + confidence bars |
| Нет режима offline / standalone APK | «А если нет интернета?» | Упомянуть как future work, либо попробовать TFLite конверсию |
| ~~model.py сломан~~ | ~~Если жюри попросит показать код — баг~~ | **Готово** — удалён |

---

## Блок 6 — Фичи «nice-to-have» (если останется время)

- [ ] **On-device inference через TFLite** — конверсия EfficientNet-B0 в Lite формат, работа без сервера. Это отдельный крупный блок работы, но именно это превратит проект из «студенческого демо» в «реальный продукт»
- [ ] **Экспорт scan как PDF-отчёт** — с фото, диагнозом, лечением (через Android `PdfDocument`)
- [ ] **Подписки на растения** (user добавляет свой папоротник → периодические напоминания проверить)
- [ ] **Сравнение «до/после»** — выбрать два скана одного растения с разницей в датах
- [ ] **Фидбек от пользователя** («диагноз правильный?» да/нет) → сбор на сервере для дообучения
- [ ] **Интеграция с календарём** — напоминания о поливе на основе статистики
- [ ] **Виджет на главный экран** — последний скан + быстрый shortcut на сканер

---

## Порядок приоритетов

```
Приоритет 1 — БЛОКЕРЫ (без них защита невозможна)
   ├─ 1.1 Обучить модели
   ├─ 1.3 Починить model.py + LICENSE
   └─ 2.1 Настоящий Grad-CAM (не хардкод)

Приоритет 2 — WOW-ЭФФЕКТ (то, что запомнится жюри)
   ├─ 2.2 Top-3 альтернативных диагноза
   ├─ 2.3 Экран «О модели»
   └─ 2.4 Share с изображением

Приоритет 3 — PRODUCTION-GRADE (стабильность демо)
   ├─ 3.1 Обработка 429, blur detection
   ├─ 3.3 Таймауты и валидация на сервере
   ├─ 3.4 Release APK подписанный
   └─ 3.5 Docker проверить

Приоритет 4 — UX POLISH (ощущение премиум)
   ├─ 4.1 Pinch-to-zoom, grid overlay
   ├─ 4.3 Skeleton loaders, re-analyze
   ├─ 4.4 Edge-to-edge, accessibility
   └─ 4.6 Доступ к онбордингу из Profile

Приоритет 5 — NICE-TO-HAVE (если останется время)
   └─ Блок 6
```

---

## Метрики готовности

| Метрика | Текущее | Минимум для защиты | Цель |
|---------|---------|--------------------|------|
| Accuracy детектора | Не обучен | ≥ 90% | ≥ 95% |
| Accuracy классификатора | Не обучен | ≥ 75% | ≥ 85% |
| Latency на CPU | < 1 сек (demo) | < 3 сек | < 2 сек |
| Grad-CAM реальный | **Да** (пиксельные координаты) | Да | Да + toggle |
| Top-3 диагнозов в UI | **Да** (confidence-bars) | Да | Да + с confidence-bars |
| Crash rate в release | Не проверен | 0 | 0 |
| Обе темы работают | Да | Да | Да |
| Accessibility (TalkBack) | Нет | Базовый | Полный |

---

## Стек (актуальный)

| Компонент | Технология | Статус |
|-----------|-----------|--------|
| Android | Kotlin, CameraX 1.3, Room 2.6, Retrofit 2.9, Navigation, Material 3, Lottie 6.3, Glide 4.16, ViewPager2, DataStore | ✅ |
| Сервер | Python 3.9+, FastAPI 0.109, Uvicorn, Starlette middleware | ✅ |
| ML | PyTorch, MobileNetV3-Small (2.5M), EfficientNet-B0 (5.3M), Grad-CAM | ⚠️ не обучено |
| Обучение | Google Colab (T4 GPU), PlantVillage dataset | ⚠️ ноутбук готов, не запускался |
| Инфра | Docker, docker-compose, Git | ✅ |
