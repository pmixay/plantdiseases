# PlantDiseases Community Rules and Moderation

Effective: 16 August 2026
Moderation contact: <mailto:gavel8980@gmail.com>

These rules cover community scan photos, diagnoses, nicknames, and forum posts and answers. The same criteria are shown inside the RuStore release under **Community scans → Community Rules & safety**.

## Content rules

Community content must be plant-related and intended to help identify or care for a plant. Do not publish:

- photos of people or content containing personal, contact, precise-location, credential, or financial information;
- sexual content, sexual exploitation, or nudity;
- graphic violence, threats, encouragement of self-harm, hate, harassment, or targeted abuse;
- illegal goods or activity, or instructions likely to cause physical, animal, plant, or environmental harm;
- impersonation, fraud, spam, advertising, malware links, or knowingly misleading diagnoses;
- material that infringes another person's privacy, intellectual property, or other rights;
- repeated off-topic or disruptive content.

PlantDiseases may remove content that violates these rules. Repeated or severe cases can be referred for an account-level review. Plant diagnoses remain community opinions, not professional agricultural advice.

## User controls

- **Shared scans:** shown without an author, and a tap only expands the photo. Neither the app nor the API has a Report or Block control for a scan; a scan is flagged through **Contact moderation**.
- **Report:** available on every forum post and answer. Reporting requires a signed account. The app sends an opaque content token and one fixed violation category; it never accepts a client-supplied account identifier or free-form report text.
- **Block author:** available beside Report. The server resolves the verified author from the content token. For signed-in community use, blocking is mutual for feed visibility and interaction, applies across all devices signed into the account, and immediately hides that author's scans, posts and answers from the blocker.
- **Unblock:** **Community scans → Community Rules & safety → Blocked authors**. The list contains only an opaque block ID and the last displayed community nickname.
- **Contact moderation:** available in the same in-app safety dialog and links to the issue tracker above. Do not place access tokens, passwords, private photos, or other secrets in a moderation issue.

## Moderation workflow

Shared scans are moderated locally on the application server; the public API exposes no moderation-list or moderation-action endpoint for them. A scan flagged to the moderation contact is taken down by its public id (`cs…`, the start of its thumbnail filename):

```bash
python server/tools/community_moderation.py remove-scan PUBLIC_ID --confirm-remove
```

Scan reports that older app versions filed stay in a private queue (no new ones arrive) until an operator resolves them:

```bash
python server/tools/community_moderation.py list --limit 100
python server/tools/community_moderation.py resolve REPORT_ID --action dismiss
python server/tools/community_moderation.py resolve REPORT_ID \
  --action remove_content --confirm-remove
```

The target and action must be checked before removing a scan or resolving a report. Removing a scan also removes its thumbnail and view records. Related pending reports are resolved consistently. The destructive action requires the explicit `--confirm-remove` flag.

Suggested operating targets:

- credible threats, sexual exploitation, doxxing, or dangerous/illegal material: triage as soon as received and within 24 hours;
- other reports: review within 72 hours;
- ambiguous cases: dismiss the report without removing content, or escalate through the project owner before acting.

Report records are retained for at most 180 days. Blocks remain until unblocked or either linked account is deleted. Account deletion removes the actor's community content, reports, and both sides of any block relationship through the existing durable cleanup queue.

## Forum moderation (posts and answers)

The forum (Community tab) is moderated in the app by people with a role, not only from the server shell.

- **Roles** live in the account service: *expert* (verifies diagnoses, reviews AI disagreements), *moderator* (all spaces, or one space) and *admin*. The first admin can only be created on the server (the account image does not contain the script, so it is piped in from the checkout):
  `docker compose exec -T account-api python -B - grant --nickname NICK --role admin < account-server/manage_roles.py`.
  The production VDS form is in `deploy/production-vds/README.md`.
  Admins then grant and revoke expert and moderator roles from the app; the app API checks roles with the account service on every staff action and refuses with 503 when it cannot.
- **Actions** need a fixed reason code (spam, harassment, dangerous advice, off topic, inappropriate photo, privacy, duplicate, other) that the member sees. Removal is reversible for 30 days, then the forum purge job erases text and photos. Moderators can also close answers, pin, and move posts between spaces; admins can exclude a post from training and ban a photo so look-alike uploads are refused.
- **Profile photos** are opt-in (the app asks before publishing one) and follow the same content rules as forum photos; banned photos cannot be uploaded as one. A global moderator can take one down with `POST /api/community/admin/actors/{author_token}/avatar/remove` (no in-app control yet), which by default also bans the picture.
- **Automatic hiding:** a post or answer reported by three different members at level 2 or above is hidden until a moderator keeps or removes it.
- **Sanctions** escalate warn → mute 24 h → mute 7 days → ban. Muted members can still like and report; banned members cannot write. Global moderators warn and mute; only admins ban.
- **Appeals:** every decision affecting a member pops up once in Community and stays in Community notifications (the bell), where it opens with an Appeal button, usable once per decision for 30 days. A different moderator decides.
- **Audit:** every action is written to an append-only log (who, what, reason, previous state), readable by admins in the app. Account deletion removes the member's sanctions and appeals and unlinks them from the log.
- **Reward freezes:** members who mostly accept or like each other's answers have their achievement discount paused automatically and listed under Moderation → Rewards for an admin to review.
- New members cannot post links for their first week, and admins can set `FORUM_BLOCKED_TERMS` (comma-separated) on the app server.

---

# Правила сообщества и модерация PlantDiseases

Действуют с 16 августа 2026 г.
Связь с модерацией: <mailto:gavel8980@gmail.com>

Правила распространяются на фотографии сканов, диагнозы, псевдонимы и ответы со вторым мнением. Эти же критерии показаны в версии для RuStore: **Сканы сообщества → Правила и безопасность**.

## Правила публикации

Материал должен относиться к растениям и помогать определить растение или ухаживать за ним. Запрещено публиковать:

- фотографии людей, персональные или контактные данные, точное местоположение, учётные или финансовые данные;
- материалы сексуального характера, сексуальную эксплуатацию и обнажённые изображения;
- жестокое насилие, угрозы, призывы к самоповреждению, ненависть, травлю и адресные оскорбления;
- незаконные товары и действия, а также инструкции, способные навредить людям, животным, растениям или окружающей среде;
- выдачу себя за другого, мошенничество, спам, рекламу, вредоносные ссылки и заведомо ложные диагнозы;
- материалы, нарушающие приватность, авторские и иные права;
- повторяющиеся материалы не по теме и намеренные помехи работе сообщества.

PlantDiseases вправе удалить нарушающий правила материал. Повторные и тяжёлые случаи могут быть переданы на проверку учётной записи. Мнения сообщества о диагнозе не заменяют консультацию агронома.

## Возможности пользователя

- **Пожаловаться:** доступно на каждом опубликованном скане и ответе. Нужен вход в аккаунт. Приложение отправляет непрозрачный токен материала и одну категорию нарушения; идентификатор аккаунта цели и свободный текст от клиента не принимаются.
- **Заблокировать автора:** сервер сам определяет подтверждённого автора по токену материала. При использовании сообщества с выполненным входом блокировка взаимно скрывает материалы в лентах и запрещает взаимодействие; настройка действует на всех устройствах аккаунта.
- **Разблокировать:** **Сканы сообщества → Правила и безопасность → Заблокированные авторы**. В списке есть только непрозрачный идентификатор блокировки и последний отображавшийся псевдоним.
- **Связаться с модерацией:** кнопка находится в том же окне и открывает указанный выше трекер. Не публикуйте там токены, пароли, приватные фотографии и другие секреты.

Старый материал без подтверждённого автора аккаунта можно отправить на модерацию, но нельзя использовать для блокировки пользователя.

## Работа модерации

Жалобы попадают в закрытую очередь, связанную с аккаунтом заявителя. Повторная жалоба того же аккаунта на тот же материал не создаёт дубликат; используются только фиксированные категории; действует лимит 20 новых жалоб за скользящие 24 часа. Публичных API для просмотра очереди или принятия решения нет.

Модератор запускает локальный инструмент на сервере командами из англоязычного раздела выше. Решение — отклонить жалобу либо удалить конкретный материал. Удаление скана также удаляет миниатюру, ответы, запрос второго мнения и просмотры; удаление ответа затрагивает только этот ответ. Для удаления обязательно явное подтверждение `--confirm-remove`.

Рекомендуемый срок первичной проверки: до 24 часов для угроз, сексуальной эксплуатации, разглашения персональных данных и опасных или незаконных материалов; до 72 часов для остальных жалоб. Неоднозначный случай следует отклонить без удаления либо передать владельцу проекта.

Записи о жалобах хранятся не более 180 дней. Блокировка хранится до отмены или удаления одного из аккаунтов. При удалении аккаунта существующая надёжная очередь очистки удаляет материалы автора, его жалобы и обе стороны связанных блокировок.

## Модерация форума (публикации и ответы)

Форум (вкладка «Сообщество») модерируют прямо в приложении люди с ролью, а не только с сервера.

- **Роли** хранятся в сервисе аккаунтов: *эксперт* (подтверждает диагнозы, разбирает расхождения с ИИ), *модератор* (все разделы или один раздел) и *администратор*. Первого администратора можно назначить только на сервере (скрипта нет в образе сервиса аккаунтов, поэтому он передаётся из копии репозитория):
  `docker compose exec -T account-api python -B - grant --nickname NICK --role admin < account-server/manage_roles.py`.
  Команда для боевого VDS — в `deploy/production-vds/README.md`.
  Дальше администраторы назначают и снимают экспертов и модераторов в приложении; API приложения проверяет роль в сервисе аккаунтов при каждом действии и отвечает 503, если проверить не может.
- **Действия** требуют фиксированной причины (спам, травля, опасный совет, не по теме, неподходящее фото, личные данные, повтор, другое), которую видит участник. Удаление можно отменить в течение 30 дней, затем текст и фото стираются. Модераторы также закрывают ответы, закрепляют и переносят публикации; администраторы исключают публикацию из обучения и запрещают фото, чтобы похожие загрузки отклонялись.
- **Автоскрытие:** публикация или ответ, на которые пожаловались три разных участника 2-го уровня и выше, скрываются до решения модератора.
- **Санкции** нарастают: предупреждение → ограничение на 24 часа → на 7 дней → блокировка. Ограниченный участник может ставить отметки и жаловаться; заблокированный не может писать. Предупреждают и ограничивают модераторы всех разделов, блокируют только администраторы.
- **Апелляции:** каждое решение об участнике один раз всплывает в Сообществе и остаётся в уведомлениях сообщества (колокольчик), откуда открывается с кнопкой «Обжаловать» — один раз на решение, в течение 30 дней. Решает другой модератор.
- **Журнал:** каждое действие записывается в журнал без изменения задним числом (кто, что, причина, прежнее состояние); администраторы читают его в приложении. При удалении аккаунта санкции и апелляции участника удаляются, а журнал перестаёт на него указывать.
- **Паузы наград:** если участники в основном принимают или отмечают ответы друг друга, их скидка за достижения автоматически приостанавливается и попадает в «Модерация → Награды» на проверку администратору.
- Новые участники не могут добавлять ссылки первую неделю; администратор может задать `FORUM_BLOCKED_TERMS` (через запятую) на сервере приложения.

