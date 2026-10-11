# Проверка MVP — 11 октября 2026

## Backend

**74 теста прошли** внутри финального Docker образа с настоящей multilingual MiniLM ONNX моделью и Tesseract `rus+eng`; пропусков нет. Тестовые базы — отдельные SQLite in-memory, данные работающих сервисов не изменяются.

```sh
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests:ro" \
  -e APP_ENV=test \
  -e CATALOG_DATABASE_URL=sqlite+pysqlite:///:memory: \
  -e USERS_DATABASE_URL=sqlite+pysqlite:///:memory: \
  -e RECOMMENDATIONS_DATABASE_URL=sqlite+pysqlite:///:memory: \
  -e CATALOG_RUN_MODEL_TESTS=1 \
  catalog python -m pytest -q /app/tests
```

Проверены права доступа и владение объектами, неизменяемые версии меню и источники, импорт HTML/PDF/фото, OCR, обязательные ограничения профиля, десятичные расчёты дневника и часовые пояса, idempotency, восстановление заданий, шаги LangGraph и валидация вариантов. Семь тестов провайдера используют настоящий ChatOpenAI/OpenAI SDK с явно внедрённым MockTransport: это проверка HTTP-контракта GLM/OpenRouter и графа, а не вызов внешней модели.

## Живые сервисы

Smoke прошёл против локального Docker gateway с PostgreSQL и против **VPS `90.156.213.217` через приватный SSH-туннель**. Проверены регистрация и JWT, профиль, каталог и версии меню, evidence, настоящий поиск MiniLM/pgvector, обязательная аллергия из профиля, половина порции, повтор без дубля, дневные итоги и удаление записи. По запросу «хочу хинкали» первые два результата — хинкали.

Локально также прошёл `--imports`: загрузка HTML, фоновая обработка, review черновика, публикация новой версии с HTTP 201 и чтение evidence. На VPS этот административный smoke отдельно не запускался.

```sh
.venv/bin/python scripts/smoke.py --imports
.venv/bin/python scripts/smoke.py --url http://127.0.0.1:55080
```

На VPS три backend сервиса и PostgreSQL healthy. API слушает только `127.0.0.1:8080`; наружу открыт SSH. Проверка сетевых слушателей подтвердила отсутствие публичного HTTP bind. Модель сохранена в Docker volume. GLM Coding Plan с `glm-5.3-flash` подключён настоящим ключом.

Живые `dish_search` и `meal_plan` завершились `completed/ready`: источники, текущие версии меню, цены, порции и суммарные КБЖУ сверены с Catalog. Meal plan выдержал ограничения 1000 рублей и 900 ккал. Admin login и чтение обеих trace через настоящий gateway вернули HTTP 200. В обоих запусках завершились coordinator, search, nutrition, planner и validator; выполнялись `search_menu` и `calculate_meal`. Эти результаты получены через настоящий внешний GLM API, без внедрённых fake моделей.

Полный `scripts/smoke.py --url http://127.0.0.1:55080 --agents --proactive` завершился с exit 0 и `LIVE API SMOKE PASSED`. Проактивную рекомендацию создал штатный scheduler с интервалом 900 секунд: запуск `5577dbdf-8a46-4cff-bb8f-ad1fe587a46d` создан 19:59:41 UTC, завершён `completed/ready` в 20:01:18 UTC. Проверены её происхождение, связь с персональной лентой, источники и точные суммы по актуальному меню. Интервал scheduler не менялся, задание вручную не создавалось.

Admin trace этого проактивного запуска вернула HTTP 200: все 12 шагов coordinator/search/nutrition/planner/validator завершены, инструменты `search_menu` и `calculate_meal` выполнялись. Четыре итоговых evidence из двух источников независимо прочитаны с HTTP 200. После smoke подтверждено `proactive_enabled=false` у тестового профиля; существующие пользовательские профили не менялись.

## Контракты и диаграммы

OpenAPI экспортирован из текущих FastAPI приложений: 20 операций Catalog, 15 Users & Nutrition и 7 Recommendations, всего **42**. Все внутренние `$ref` разрешаются. В браузере проверен Swagger с переключением трёх сервисов и общей схемой `ApiError`; первоначальная проектная спецификация лежит отдельно в `design-openapi.yaml`.

Runtime ER отражает **16 таблиц** в трёх сервисных базах, JSON поля и два вектора по 384 измерения. Mermaid и SVG созданы по SQLAlchemy моделям; связь между сервисными базами — логическая, не PostgreSQL foreign key.

## iOS

Переносимый Foundation API пакет собирается; **10 групп** самостоятельных проверок моделей и сетевых запросов прошли. Живой Swift `GastroSmoke` прошёл против локального PostgreSQL backend и независимо против VPS: регистрация, профиль, меню, evidence, дробная порция и idempotency, дневник, итоги, лента и удаление. SwiftUI исходники прошли синтаксический разбор, Xcode project и Info.plist — `plutil -lint`.

Финальный просмотр SwiftUI обнаружил и исправил гонку отмены подбора: предыдущий Task теперь не изменяет результат, ошибку и индикатор следующего запроса. При отмене до ответа POST повтор сохраняет запрос и Idempotency-Key. Изменённый `RecommendationView.swift` прошёл `swiftc -frontend -parse` и `git diff --check`; поведение экрана в Simulator ещё не проверено.

Полный Xcode и iOS SDK на машине отсутствуют. Полная сборка приложения, работа экранов в Simulator и Keychain **не проверены**. Foundation smoke не доказывает работоспособность SwiftUI приложения.

Workflow `.github/workflows/ios-build.yml` подготовлен для Foundation тестов и сборки iOS Simulator. По решению пользователя код остаётся в локальной ветке `feat/gastrogid-mvp`; push и запуск GitHub Actions не выполнялись.

## Оставшиеся проверки

- В полном Xcode: сборка, запуск и пользовательский сценарий iOS, включая Keychain, отмену подбора и повтор без дублирования задания.
- После выбора публичного доступа: TLS и адрес gateway для физического телефона. Текущий SSH-туннель доступен на Mac и подходит Simulator; loopback Mac недоступен физическому iPhone.

Стартовые рестораны, блюда и отзывы вымышленные; их КБЖУ явно обозначены как оценки. Эти проверки подтверждают учебный MVP, а не эксплуатацию реального ресторанного каталога.
