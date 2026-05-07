# PlantDiseases — Roadmap

Состояние: PlantVision 2.1, 7 классов, модель ещё не обучена.
Класс-таксономия и архитектура зафиксированы — см. `MODEL_CARD.md`
и `server/train_notebook.ipynb`.

## Server / ML

### Блокеры релиза

- [ ] **Обучить веса v2.1.** Прогон `server/train_notebook.ipynb`
      end-to-end (A100-40 ~60 мин или T4 BATCH=64 ~3×).
      **Чек:** ship-gate проходит — disease macro-F1 ≥ 0.70 на
      `val_realworld`, ни одного класса с recall=0, healthy-bias < 1.5;
      артефакты `classifier_int8.onnx`, `classifier.onnx`,
      `classifier.pth`, `classes.json`, `temperature.json`,
      `conformal.json`, `model_card.json` лежат в `server/models/`.

- [ ] **Дофич датасета комнатными растениями.** PlantVillage в трейне
      содержит томаты/картошку/яблоки — целевой домен (фикус, монстера,
      антуриум, драцена) не покрыт. Добавить ≥200 размеченных
      real-world фото на каждый из 7 классов: iNaturalist (filtered),
      r/houseplants, Roboflow Universe, ручная разметка.
      **Чек:** macro-F1 на val_realworld растёт минимум на +0.05
      против baseline без новой выборки.

- [ ] **Accuracy smoke-тест в CI.** Создать `server/tests/test_accuracy_smoke.py`
      с golden-набором 30+ эталонных фото и порогом macro-F1 ≥ 0.65.
      **Чек:** PR с регрессией точности фейлится автоматически.

- [ ] **Boot-тест против реальных артефактов.** После обучения —
      `pytest server/tests/`.
      **Чек:** все тесты зелёные, `/api/version` = `2.1`,
      `/api/classes` возвращает 7 классов.

### Конкурентный паритет

- [ ] **`POST /api/feedback`.** Body: `scan_id`, `correct: bool`,
      `corrected_class?`, free-text. Append-only SQLite ledger
      `server/feedback.db`. Rate-limit 1/s per IP.
      **Чек:** Android thumbs-press долетает и пишется в БД.

- [ ] **Toxicity-to-pets.** Добавить поле `toxic_to_pets: bool` +
      `species_list` в `server/diseases_data.py` и в ответ
      `/api/analyze`.
      **Чек:** ответ содержит флаг для всех 7 классов.

- [ ] **Историческое расширение `/api/progression`.** Принимать
      `history: list[dict]`, возвращать 5-точечный sparkline severity.
      Backwards-compatible с парным сравнением.
      **Чек:** 3 unit-теста в `tests/test_endpoints.py`.

### Эксплуатация

- [ ] **`make ship-bundle` target.** ZIP всех артефактов из
      `server/models/` + `MANIFEST.json` с SHA-256.
      **Чек:** скрипт деплоя умеет распаковать и проверить хэши.

- [ ] **Rollback path.** `models/current` ↔ `models/previous`
      symlink-indirection; fallback на previous при битом current.
      **Чек:** удаление файла из `current/` поднимает `previous/`
      с warning-логом.

- [ ] **Negative-sample аудит `not_a_plant`.** Из прод/staging логов
      собрать low-MaxLogit true-positives (тёмные листья, суккуленты),
      разметить как доп. `not_a_plant` для v2.2.

- [ ] **MODEL_CARD post-train refresh.** Скопировать реальные
      `train_counts`, `val_realworld_counts`, INT8 drift, best KD epoch,
      `T_value`, `q_hat` из `model_card.json` в `MODEL_CARD.md`.
      **Чек:** per-class counts совпадают byte-for-byte.

### v2.2 prep

- [ ] **Cold-start данных для 4 deferred классов.**
      `data_v2_pending/` с ≥100 indoor фото на класс
      (`edema`, `leaf_scorch_underwatering`, `nutrient_deficiency`,
      `sunburn_or_scorch`).
      **Чек:** 400 net images с sidecar JSON; loader smoke-test
      их подхватывает.

## Android

### Блокеры релиза

- [ ] **HTTPS-only release variant.** Снять `cleartextTrafficPermitted="true"`
      из `app/src/main/res/xml/network_security_config.xml` для release.
      Заменить дефолт `API_BASE_URL` в `app/build.gradle.kts:26`
      на HTTPS прод-URL. Debug-вариант оставить с HTTP для LAN.
      Развести через `manifestPlaceholders`.
      **Чек:** release APK отказывается слать cleartext на любой
      non-debug хост; debug на LAN продолжает работать.

- [ ] **Включить lint в CI.** `app/build.gradle.kts:70` —
      `abortOnError = true`.
      **Чек:** PR с новыми lint-ошибками фейлится.

- [ ] **Минимальные unit-тесты.** Создать `app/src/test/`. Покрыть:
      `ApiService` retry-логику, парсинг `AnalyzeResponse`,
      `ScanRepository` транзакции.
      **Чек:** в CI запускается `./gradlew test`, ≥10 тестов.

- [ ] **Перебилд после Models.kt + DoctorChatSheet рерайта.**
      Старые call-sites на `ChatContext`, `suggestedQuestions` сломаны.
      **Чек:** debug build устанавливается на устройство;
      AI Doctor sheet шлёт в `/api/chat` и получает реальный ответ.

### Конкурентный паритет

- [ ] **Photo-coach onboarding.** Один раз при первом открытии
      камеры — 3 слайда «как сфоткать лист правильно» (расстояние,
      освещение, фон).
      **Чек:** retry-rate из-за blur падает (логировать в метрику).

- [ ] **Result-screen feedback prompt.** При `low_confidence=true`
      или непустых warnings — chip «Did this match?» 👍/👎.
      Persist локально в Room `feedback`. Когда `/api/feedback`
      готов — replace на POST с очередью на ретрай.
      **Чек:** thumbs-press пишет строку в Room; после Server.feedback
      — отправляет на сервер.

- [ ] **AI Doctor session budget UI.** Прогресс-чип «3 / 12 messages
      left» при remaining ≤ 20% от cap. Источник —
      `ChatResponse.session.turns_used / turns_remaining`.
      **Чек:** чип появляется до удара лимита.

- [ ] **`available=false` actionable copy.** Свериться по
      `server/ai_services.py` после live-обучения, добавить EN/RU
      строки для всех `reason` веток (`session_turn_cap`,
      `session_token_cap`, `no_api_key`, `message_too_long`,
      `empty_message`, `empty_reply`, `api_error`).

- [ ] **`/api/progression` в PlantDetailActivity.** Для растений с
      ≥2 сканами показать chip `improving / stable / worsening /
      baseline` над таймлайном. Helper — `scanToProgressionPayload(scan)`.
      Тап по chip — half-sheet с before/after фото и `delta_severity`.
      **Чек:** chip меняется при свежем скане, перевернувшем тренд.

- [ ] **Toxicity warning UI.** Бейдж «toxic to pets» рядом с species
      suggestion и в Result после ответа сервера.

- [ ] **`IdentifyResponse.cached` UI.** Маленький pill «served from
      cache» рядом с species suggestions. Поле уже на проводе,
      нужен только UI-хук.

### Архитектурный долг

- [ ] **Split `ResultActivity.kt` (1189 строк).** Извлечь
      `ResultHeaderView`, `ResultTreatmentView`, `ResultCarePlanView`,
      `ResultDoctorView`. Activity — тонкий координатор.
      **Чек:** каждый новый файл ≤ 350 строк.

- [ ] **Coroutine `delay` вместо `Thread.sleep`.**
      `app/src/main/java/com/plantdiseases/app/data/remote/ApiService.kt:110`.
      **Чек:** ретрай не блокирует поток.

- [ ] **`ResolutionSelector` вместо `setTargetResolution`.**
      `app/src/main/java/com/plantdiseases/app/ui/camera/CameraFragment.kt:269`.
      Снять `@Suppress("DEPRECATION")`.

- [ ] **Cold-start UX toast.** При 503 + retry — один toast
      «Server is warming up…» на сессию.
      **Чек:** toast виден ровно один раз даже при 5+ ретраях.

### Релиз

- [ ] **Подписанный release APK через CI на tag.** Workflow на
      `v*.*.*` push: `./gradlew assembleRelease` + sign из secrets,
      upload в release assets.
      **Чек:** tag → CI зелёный → APK скачиваемый.

## Out of scope

- Pruning, distillation поверх KD, Compose-rewrite, Hilt.
- Pests-таксономия, outdoor crops.
- Marketplace, social feed, expert consultations.
- Per-session chat lock (только если метрики покажут lock-wait > 5 ms).

## Eval contract

| Slice | Роль | Pass criterion |
|---|---|---|
| `val_realworld` | **ship-gate** | macro-F1 ≥ 0.70; recall>0 для всех; healthy-bias < 1.5 |
| `val_indomain` | early stopping | best EMA checkpoint |
| `val_studio` | transfer monitor | tracked, never blocking |
| `temperature_cal` | INT8 + temperature | held out |
| `conformal_cal` | RAPS | held out; coverage ≈ 1−α |
