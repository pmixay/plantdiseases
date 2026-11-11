# PlantDiseases Community Rules and Moderation

Effective: 16 August 2026
Moderation contact: <mailto:gavel8980@gmail.com>

These rules cover community scan photos, diagnoses, nicknames, and second-opinion replies. The same criteria are shown inside the RuStore release under **Community scans → Community Rules & safety**.

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

- **Report:** available on every shared scan and individual reply. Reporting requires a signed account. The app sends an opaque content token and one fixed violation category; it never accepts a client-supplied account identifier or free-form report text.
- **Block author:** available beside Report. The server resolves the verified author from the content token. For signed-in community use, blocking is mutual for feed visibility and interaction, applies across all devices signed into the account, and immediately hides that author's scans and replies from the blocker.
- **Unblock:** **Community scans → Community Rules & safety → Blocked authors**. The list contains only an opaque block ID and the last displayed community nickname.
- **Contact moderation:** available in the same in-app safety dialog and links to the issue tracker above. Do not place access tokens, passwords, private photos, or other secrets in a moderation issue.

Legacy content without a verified account author can be reported but cannot be used to create a block. This prevents an old installation identifier from being treated as a verified person.

## Moderation workflow

The API writes reports to a private, account-bound queue. Duplicate reports from the same account for the same content are idempotent, the report taxonomy is fixed, and each account is limited to 20 new reports per rolling 24 hours. The public API exposes no moderation-list or moderation-action endpoint.

An operator reviews the oldest pending reports locally on the application server:

```bash
python server/tools/community_moderation.py list --limit 100
python server/tools/community_moderation.py resolve REPORT_ID --action dismiss
python server/tools/community_moderation.py resolve REPORT_ID \
  --action remove_content --confirm-remove
```

The target and action must be checked before resolving a report. Removing a scan also removes its thumbnail, replies, opinion request, and view records. Removing a reply affects only that reply. Related pending reports are resolved consistently. The destructive action requires the explicit `--confirm-remove` flag.

Suggested operating targets:

- credible threats, sexual exploitation, doxxing, or dangerous/illegal material: triage as soon as received and within 24 hours;
- other reports: review within 72 hours;
- ambiguous cases: dismiss the report without removing content, or escalate through the project owner before acting.

Report records are retained for at most 180 days. Blocks remain until unblocked or either linked account is deleted. Account deletion removes the actor's community content, reports, and both sides of any block relationship through the existing durable cleanup queue.

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
