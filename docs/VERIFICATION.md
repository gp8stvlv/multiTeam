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

Smoke прошёл против локального Docker gateway с PostgreSQL и против **VPS `90.156.213.217`**, сначала через приватный SSH-туннель, затем напрямую через доверенный публичный HTTPS. Проверены регистрация и JWT, профиль, каталог и версии меню, evidence, настоящий поиск MiniLM/pgvector, обязательная аллергия из профиля, половина порции, повтор без дубля, дневные итоги и удаление записи. По запросу «хочу хинкали» первые два результата — хинкали.

Локально также прошёл `--imports`: загрузка HTML, фоновая обработка, review черновика, публикация новой версии с HTTP 201 и чтение evidence. На VPS этот административный smoke отдельно не запускался.

```sh
.venv/bin/python scripts/smoke.py --imports
.venv/bin/python scripts/smoke.py --url http://127.0.0.1:55080
```

На VPS три backend сервиса и PostgreSQL healthy. Приватный gateway слушает `127.0.0.1:8080`, публичный edge — 80/443. Модель сохранена в Docker volume. GLM Coding Plan с `glm-5.3-flash` подключён настоящим ключом.

Живые `dish_search` и `meal_plan` завершились `completed/ready`: источники, текущие версии меню, цены, порции и суммарные КБЖУ сверены с Catalog. Meal plan выдержал ограничения 1000 рублей и 900 ккал. Admin login и чтение обеих trace через настоящий gateway вернули HTTP 200. В обоих запусках завершились coordinator, search, nutrition, planner и validator; выполнялись `search_menu` и `calculate_meal`. Эти результаты получены через настоящий внешний GLM API, без внедрённых fake моделей.

Полный `scripts/smoke.py --url http://127.0.0.1:55080 --agents --proactive` завершился с exit 0 и `LIVE API SMOKE PASSED`. Проактивную рекомендацию создал штатный scheduler с интервалом 900 секунд: запуск `5577dbdf-8a46-4cff-bb8f-ad1fe587a46d` создан 19:59:41 UTC, завершён `completed/ready` в 20:01:18 UTC. Проверены её происхождение, связь с персональной лентой, источники и точные суммы по актуальному меню. Интервал scheduler не менялся, задание вручную не создавалось.

Admin trace этого проактивного запуска вернула HTTP 200: все 12 шагов coordinator/search/nutrition/planner/validator завершены, инструменты `search_menu` и `calculate_meal` выполнялись. Четыре итоговых evidence из двух источников независимо прочитаны с HTTP 200. После smoke подтверждено `proactive_enabled=false` у тестового профиля; существующие пользовательские профили не менялись.

## Публичный HTTPS

API **https://90.156.213.217/api/v1** и Swagger **https://90.156.213.217/docs/** доступны напрямую из внешней сети. Проверка клиента без `insecure` подтвердила доверенную цепочку Let’s Encrypt / YE2, IP SAN `90.156.213.217` и TLS 1.3. Текущий сертификат действителен с 11 октября 19:27:49 UTC до 18 октября 11:27:48 UTC; systemd timer обновляет его каждые шесть часов.

Реальный ACME renewal dry-run с deploy hook и проверкой/перезагрузкой Nginx прошёл. Service сообщает `Result=success`, `ExecMainStatus=0`; timer и Docker — enabled/active. Все шесть контейнеров running с `restart=unless-stopped`. TLS ключи и ACME state находятся вне Git в `/var/lib/gastrogid/tls`.

После настройки edge повторный внешний acceptance прошёл: auth/profile, рестораны/меню/evidence, настоящий MiniLM RAG, дробный дневник/idempotency/удаление, Swagger и все три OpenAPI. Приватные health/ready и internal, включая encoded slash, отвечают 404; профиль без JWT — 401. Порты 8080/8001/8002/8003/5432 снаружи недоступны. HTTP 308 и перенаправление FastAPI 307 для лишнего slash сохраняют HTTPS.

В `scripts/smoke.py` добавлен `--public`: проверяет gateway, три публичных OpenAPI и authenticated `/me/feed`, затем выполняет прежний функциональный сценарий. Служебные health методы остаются закрытыми. Реальная команда завершилась exit 0 и `LIVE API SMOKE PASSED`, без новых LLM inference:

```sh
.venv/bin/python scripts/smoke.py --url https://90.156.213.217 --public
```

Локальные контейнеры GastroGuide и SSH-туннель остановлены, volumes сохранены. Последующий пользовательский сценарий iOS выполнялся исключительно через публичный HTTPS; Mac не обслуживал backend.

## Контракты и диаграммы

OpenAPI экспортирован из текущих FastAPI приложений: 20 операций Catalog, 15 Users & Nutrition и 7 Recommendations, всего **42**. Все внутренние `$ref` разрешаются. В браузере проверен Swagger с переключением трёх сервисов и общей схемой `ApiError`; первоначальная проектная спецификация лежит отдельно в `design-openapi.yaml`.

Runtime ER отражает **16 таблиц** в трёх сервисных базах, JSON поля и два вектора по 384 измерения. Mermaid и SVG созданы по SQLAlchemy моделям; связь между сервисными базами — логическая, не PostgreSQL foreign key.

## iOS

Переносимый Foundation API пакет собирается; **10 групп** самостоятельных проверок моделей и сетевых запросов прошли. Живой Swift `GastroSmoke` прошёл против локального PostgreSQL backend и независимо против VPS: регистрация, профиль, меню, evidence, дробная порция и idempotency, дневник, итоги, лента и удаление. SwiftUI исходники прошли синтаксический разбор, Xcode project и Info.plist — `plutil -lint`.

Финальный просмотр SwiftUI обнаружил и исправил гонку отмены подбора: предыдущий Task теперь не изменяет результат, ошибку и индикатор следующего запроса. При отмене до ответа POST повтор сохраняет запрос и Idempotency-Key. Изменённый `RecommendationView.swift` прошёл `swiftc -frontend -parse` и `git diff --check`; отмена и возобновление затем проверены в настоящем Simulator.

В `/Applications/Xcode.app` обнаружен полный **Xcode 27.0 (27A266a)** и iPhoneSimulator 27.0 SDK. Системный `xcode-select` пока выбирает Command Line Tools; для сборки использован `DEVELOPER_DIR`, без изменения системных настроек.

Полная сборка `GastroGuide` для `generic/platform=iOS Simulator` завершилась exit 0 и **BUILD SUCCEEDED**; собраны arm64 и x86_64, deployment target iOS 16.0. Настоящий `xcrun swift test --package-path ios/GastroAPI` с Apple Swift 6.4 завершился: **10 XCTest, 0 failures**. Это дополняет прежние самостоятельные Foundation проверки.

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcrun swift test --package-path ios/GastroAPI
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project ios/GastroGuide/GastroGuide.xcodeproj \
  -scheme GastroGuide -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath ../../work/ios-derived-data CODE_SIGNING_ALLOWED=NO build
```

Runtime **iOS 27.0 (24A434)** установлен после завершения уже выполнявшейся загрузки Xcode. Загружен настоящий iPhone 17 Simulator `64BFBE1B-0A42-41C9-986E-9C86AF4955EF`. Подписанная сборка `CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=-` для конкретного Simulator в отдельном `work/ios-runtime-derived-data` прошла. Debug использует `ONLY_ACTIVE_ARCH=YES`, чтобы архитектура Swift package совпадала с выбранным Simulator. Unsigned build подходит для компиляции, но давал Keychain `-34018`; обычная ad-hoc подпись исправила ошибку без замены хранилища.

Через UI и внешний HTTPS при остановленном локальном backend проверены:

- Вход, загрузка и сохранение профиля: кухня «грузинская», ориентир 2000 ккал, бюджет 900 рублей, `Europe/Moscow`.
- Пять ресторанов, меню с ценой/КБЖУ и исходное evidence.
- Запись 0.5 порции «Лимонад тархун»: UI показывает 60 ккал, Б 2.4 / Ж 2.1 / У 7.8, остаток 1940 ккал. Независимое чтение DB подтвердило точные значения 60 / 2.4 / 2.135 / 7.795 и корректное округление UI.
- Настоящий GLM подбор «Хочу хинкали, не очень острые»: запуск `84674565-26b9-4e7e-a6c8-737c804e9f09`, создан 20:34:28 UTC, завершён `completed/ready` в 20:35:28 UTC. UI показывает два варианта хинкали за 750 и 620 рублей, 620 и 480 ккал; источники открываются.
- Отмена ожидания, возобновление и смена вкладки с последующим чтением результата. Независимая DB проверка подтвердила ровно один запуск с прежним ID. Все восемь шагов coordinator/search/nutrition/planner/validator завершены, реальные `search_menu` и `calculate_meal` отражены в admin trace.
- Настоящие `simctl terminate` и `launch`: приложение восстановило авторизованную сессию публичного API из Keychain, GET профиля вернул сохранённые значения без повторного ввода пароля.

Скриншоты сохранены в рабочей папке `work/ios-public-recommendation.png`, `work/ios-public-evidence.png` и `work/ios-public-diary.png`. По умолчанию приложение использует публичный HTTPS; `NSAllowsArbitraryLoads` удалён, локальное networking исключение сохранено для разработки.

Workflow `.github/workflows/ios-build.yml` подготовлен для Foundation тестов и сборки iOS Simulator. По решению пользователя код остаётся в локальной ветке `feat/gastrogid-mvp`; push и запуск GitHub Actions не выполнялись.

## Границы проверки

Физический iPhone и GitHub runner отдельно не проверялись. Для установки на физический телефон потребуется Apple development signing. Основной API уже доступен ему по внешнему HTTPS; локальный Docker или SSH-туннель не нужны. Карта, геолокация, push и публикация в App Store не входят в MVP.

Стартовые рестораны, блюда и отзывы вымышленные; их КБЖУ явно обозначены как оценки. Эти проверки подтверждают учебный MVP, а не эксплуатацию реального ресторанного каталога.
