# API и Swagger

Единый gateway принимает запросы с префиксом `/api/v1`. После запуска backend [Swagger UI](http://127.0.0.1:8080/docs/) позволяет переключаться между тремя сервисами. FastAPI генерирует схемы по маршрутам и моделям сервисов.

| Сервис | Спецификация | Основные возможности |
| --- | --- | --- |
| Catalog | [catalog.json](openapi/catalog.json) | Рестораны, меню, поиск, источники, импорт и публикация |
| Users & Nutrition | [users.json](openapi/users.json) | Регистрация, профиль, дневник, КБЖУ и обратная связь |
| Recommendations | [recommendations.json](openapi/recommendations.json) | Создание/чтение задания, трассы и персональная лента |

В `openapi/` сохранены актуальные снимки для просмотра без запущенного backend. Работающий Swagger получает схемы непосредственно с `/openapi/{service}.json`; для обновления снимков выполните `python scripts/export_openapi.py` в окружении с Python-зависимостями.

## Авторизация

Публичный gateway требует общий токен команды в заголовке `X-API-Key`, в том числе для регистрации и входа. В iOS токен задаётся рядом с адресом сервера. Запрос с отсутствующим или неверным токеном получает `403` до обращения к backend.

Для открытия Swagger в браузере используйте HTTP Basic: username `team`, пароль — общий токен. В **Authorize → TeamAPIKey** укажите токен для API-запросов. Локальный Docker gateway не требует общего токена.

Токен создаётся на VPS вне репозитория: `sudo python3 scripts/configure-public-access.py`. После настройки TLS используется `docker compose -f compose.yaml -f compose.public.yaml up -d --build`. Повторный запуск генератора сохраняет ключ; `--rotate` меняет его и требует перезагрузки edge. Секрет не входит в образы или Git.

`POST /auth/register` и `POST /auth/login` возвращают `access_token`. В Swagger нажмите **Authorize** и вставьте JWT; HTTP-клиент передаёт `Authorization: Bearer <token>`.

Импорт меню, управление каталогом и чтение трасс доступны администратору. Его email и сгенерированный пароль находятся в вашем `.env`. Изменение пароля в `.env` не меняет уже созданный аккаунт. Внутренние межсервисные методы закрыты на gateway.

## Подбор и дневник

```http
POST /api/v1/recommendations
X-API-Key: <team-api-token>
Authorization: Bearer <token>
Idempotency-Key: <unique-request-key>
Content-Type: application/json

{"mode":"dish_search","query":"Хочу хинкали, не очень острые","constraints":{"budget_minor":90000}}
```

Ответ `202` содержит ID задания. Читайте `GET /recommendations/{id}` до `completed` или `failed`, учитывая `Retry-After`. Результат имеет `outcome=ready`, `needs_clarification` или `no_matches`. Для составления приёма пищи используйте `mode=meal_plan`.

`POST /me/diary` получает существующий `menu_item_id`, `portions` (например, `0.5`) и время с часовым смещением. Создание задания и записи дневника требует `Idempotency-Key`: повтор одинакового запроса возвращает прежний объект, другой запрос с тем же ключом — `409`.

Цены передаются в целых копейках: 900 рублей — `90000`. КБЖУ относятся к одной стандартной порции; `null` означает отсутствие сведений. Профильные аллергии обязательны даже при пустом списке исключений в запросе.

## Импорт меню

Администратор загружает файл через `POST /menu-imports` (`restaurant_id`, `source_kind=html|pdf|image`, `file`). Затем читает статус → проверяет `/draft` и `/evidence` → исправляет черновик через `PUT /draft` → публикует через `POST /publish`. Изменение и публикация требуют текущий `expected_revision`; устаревший номер возвращает `409`. Исходные фрагменты сохраняются, версии меню неизменяемы.

Ошибки имеют единую схему `{code, message, request_id, details}`. `401` означает отсутствие действующего JWT, `403` — неверный общий API-токен или недостаточные права, `503` — неготовую модель или недоступную зависимость.
