# ГастроГид — iOS MVP

Нативный SwiftUI клиент (iOS 16+) с настоящими запросами URLSession к gateway. Токены сохраняются в Keychain, пароль не сохраняется. Ключ LLM остаётся исключительно на backend.

## Запуск

1. Откройте `GastroGuide/GastroGuide.xcodeproj` в полном Xcode 15 или новее.
2. Выберите общую схему **GastroGuide** и iPhone Simulator. Локальный Swift Package `../GastroAPI` подключён проектом, внешних Swift-зависимостей нет.
3. Для запуска на физическом iPhone задайте свой Development Team в Signing & Capabilities. Bundle ID при необходимости измените.
4. По умолчанию новая установка подключается к публичному API **`https://90.156.213.217/api/v1`**. Локальный backend и SSH-туннель для этого адреса не нужны. Если приложение уже сохраняло другой адрес, в экране входа откройте **Изменить адрес API** и укажите новый HTTPS URL с `/api/v1`.
5. Зарегистрируйте аккаунт либо войдите в существующий. В **Профиле** задайте вкусы, аллергии, дневной ориентир и бюджет.

Готовый `.xcodeproj` включён в репозиторий; xcodegen не требуется. Для Simulator используется локальная ad-hoc подпись без сертификата; для физического iPhone нужен ваш Development Team.

В `App/Info.plist` удалено общее ATS исключение `NSAllowsArbitraryLoads`; публичное подключение использует HTTPS с обычной проверкой TLS. Оставлено `NSAllowsLocalNetworking` для локальной разработки. Исключения проверки сертификата и доверие любому серверу в клиенте не используются.

## Адреса API

Основной адрес для приложения — **`https://90.156.213.217/api/v1`**, доступный из внешней сети. JWT сохраняется в Keychain отдельно для выбранного адреса API. Смена адреса завершает текущую сессию; аккаунты локального и удалённого backend могут различаться.

При запуске локального Docker gateway укажите в Simulator на том же Mac `http://127.0.0.1:8080/api/v1`. `127.0.0.1` на физическом iPhone означает сам телефон, поэтому для него используйте публичный HTTPS адрес.

Токен GLM настраивается только на backend, по инструкции [GLM и OpenRouter](../README.md#glm-и-openrouter). В приложение его вводить не требуется. Без настроенного провайдера каталог и дневник работают, а подбор показывает серверную ошибку `llm_not_configured`. Проверка Foundation клиента ниже не вызывает LLM; статус отдельной проверки настоящих агентов указан в корневом README.

## Реализованные экраны

- Регистрация и вход; восстановление сессии из Keychain; выход при 401.
- Редактируемый адрес API. Смена адреса завершает сессию, JWT одного сервера не отправляется другому.
- Список ресторанов, актуальное меню, цены в целых копейках, состав, аллергены, источник КБЖУ и фрагменты evidence.
- Подбор блюда или приёма пищи по тексту, бюджету и необязательному лимиту калорий; создание фонового запуска, поллинг с `Retry-After`, остановка ожидания и повторная проверка той же задачи. Повтор отправки использует прежний Idempotency-Key и тот же запрос.
- Результат и уточняющие вопросы, карточки вариантов, причины и предупреждения; чтение персональной ленты.
- Подтверждённое добавление съеденного в дневник с дробной порцией, временем и заметкой; защита повторной отправки через Idempotency-Key.
- Дневник по дате в часовом поясе профиля, дневные КБЖУ, ориентир и остаток; удаление записи после подтверждения.
- Базовый профиль: кухни, нежелательные ингредиенты, острота, коды аллергий, калории, бюджет, часовой пояс, включение персональной ленты.

`null` в КБЖУ отображается как «нет данных». Известная сумма при неполных данных показана отдельно и не выдаётся за полный итог. Пищевая ценность на карточке блюда относится к одной стандартной порции; дневник получает рассчитанный сервером итог съеденного.

Отмена останавливает ожидание клиента; серверное задание продолжает работать. Если его ID уже получен, доступна повторная проверка результата. Если ответ на создание ещё не получен, повтор неизменённого запроса сохраняет Idempotency-Key. Отменённая задача не меняет состояние следующего подбора.

## Проверка переносимого API слоя

`GastroAPI` использует Foundation, без SwiftUI или Security. Запросы не содержат `user_id`: идентичность определяется JWT. Decimal нутриенты декодируются из чисел и строк.

Из корня репозитория, используя установленный полный Xcode:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun swift test --package-path ios/GastroAPI
```

На текущем Mac `xcode-select` по умолчанию указывает на Command Line Tools. `DEVELOPER_DIR` выбирает полный Xcode только для команды и не меняет системную настройку.

Проверка компиляции SwiftUI приложения без подписи:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun xcodebuild \
  -project ios/GastroGuide/GastroGuide.xcodeproj \
  -scheme GastroGuide \
  -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath ../../work/ios-derived-data \
  CODE_SIGNING_ALLOWED=NO build
```

Такой build подтверждает компиляцию, но не подходит для проверки Keychain. Для запуска используйте обычную ad-hoc подпись Simulator:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun xcodebuild \
  -project ios/GastroGuide/GastroGuide.xcodeproj \
  -scheme GastroGuide \
  -configuration Debug \
  -destination 'platform=iOS Simulator,id=64BFBE1B-0A42-41C9-986E-9C86AF4955EF' \
  -derivedDataPath ../../work/ios-runtime-derived-data \
  CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- build
```

Это ID установленного на текущем Mac iPhone 17 с iOS 27.0. На другой машине выберите свой Simulator в Xcode или узнайте его ID через `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun simctl list devices available`. В Debug задано `ONLY_ACTIVE_ARCH=YES`, чтобы приложение и локальный Swift Package собирались для архитектуры выбранного Simulator. Release не ограничен активной архитектурой.

После загрузки выбранного Simulator установите и запустите собранное приложение:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun simctl install 64BFBE1B-0A42-41C9-986E-9C86AF4955EF \
  ../../work/ios-runtime-derived-data/Build/Products/Debug-iphonesimulator/GastroGuide.app

DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcrun simctl launch 64BFBE1B-0A42-41C9-986E-9C86AF4955EF ru.gastrogid.mvp
```

DerivedData сохраняется в рабочую папку `work` рядом с `outputs`, вне репозитория. Эти команды не используют ключ SSH, токен LLM, сертификаты или provisioning.

В macOS окружении только с Command Line Tools без XCTest доступны самостоятельные проверки:

```sh
CLANG_MODULE_CACHE_PATH=/tmp/gastrogid-swift-module-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/gastrogid-swift-module-cache \
swift run --package-path ios/GastroAPI --disable-sandbox \
  --cache-path /tmp/gastrogid-swift-cache GastroAPIChecks
```

Это 10 групп проверок модели и сетевых запросов через URLProtocol: ограничения URL, null/Decimal, точные копейки, очистка целей профиля, отсутствие JWT в login, дробные порции и idempotency, 202/Retry-After, окончание поллинга при failed, 401 и DELETE 204.

Для интеграции с реально запущенным backend (создаёт тестовый аккаунт и запись, затем удаляет запись):

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
GASTRO_API_URL=https://90.156.213.217/api/v1 \
xcrun swift run --package-path ios/GastroAPI GastroSmoke
```

Добавьте `GASTRO_SMOKE_RECOMMENDATIONS=1`, чтобы также проверить настоящий запуск и результат агентов. Токены и случайный пароль в вывод не попадают. Успех этой проверки требует реально работающих backend и LLM при включённых рекомендациях.

## Статус проверки

На машине разработки обнаружен полный `/Applications/Xcode.app`: Xcode 27.0 (build 27A266a), SDK iPhoneSimulator 27.0 и iPhoneOS 27.0. По умолчанию `xcode-select` всё ещё выбирает Command Line Tools; проверки полного Xcode выполнены с `DEVELOPER_DIR`, как в командах выше.

Полная сборка схемы `GastroGuide` для `generic/platform=iOS Simulator` завершилась с exit 0 и `BUILD SUCCEEDED`: SwiftUI приложение собрано для arm64 и x86_64 с настоящим SDK iPhoneSimulator 27.0, подпись отключена. Foundation package и оба executable также собираются.

Настоящий `swift test` полного Xcode завершился с exit 0: выполнены **10 XCTest, 0 failures**. Toolchain — Apple Swift 6.4 (`swiftlang-6.4.0.34.1`, `clang-2100.3.34.1`). Это проверки переносимого API слоя на macOS через реальные Foundation/XCTest; они не заменяют взаимодействие с экранами приложения. Ранее самостоятельные 10 групп API проверок и синтаксический разбор SwiftUI тоже прошли; проект и Info.plist прошли `plutil -lint`.

Живой `GastroSmoke` прошёл против локального gateway `http://127.0.0.1:8080/api/v1` с PostgreSQL: регистрация, JWT профиль, каталог и меню, evidence, добавление половины порции, повтор с тем же ключом без дубля, дневник, дневные итоги, чтение ленты и удаление записи. Контракт сетевого слоя совпал с фактическими ответами backend.

Независимый `GastroSmoke` также прошёл против приватного VPS через действующий SSH-туннель `http://127.0.0.1:55080/api/v1` с теми же проверками. Команда завершилась с exit 0, без вывода JWT, пароля или ключа GLM. `GASTRO_SMOKE_RECOMMENDATIONS` не включался: эта проверка не создавала рекомендаций и не проверяла вызов модели.

Установлен настоящий runtime iOS 27.0 (24A434), загружен iPhone 17 Simulator (`64BFBE1B-0A42-41C9-986E-9C86AF4955EF`). Сборка для этого Simulator с `CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=-` в отдельном `work/ios-runtime-derived-data` завершилась с exit 0 и `BUILD SUCCEEDED`. Первый unsigned build позволял открыть приложение, но Keychain возвращал `-34018`; обычная ad-hoc подпись Simulator исправила эту ошибку без изменения хранилища токена.

Через реальный UI Simulator проверен публичный `https://90.156.213.217/api/v1` при остановленных локальном Docker backend и SSH-туннеле: вход, чтение и сохранение профиля, каталог пяти ресторанов, меню и источники, добавление половины порции в дневник и дневные итоги. Половина порции лимонада дала 60 ккал, дневной остаток — 1940 ккал при ориентире 2000; значения подтверждены серверной БД.

Настоящий подбор GLM из UI завершился `completed/ready`: варианты хинкалей показаны с ценами и КБЖУ, источники открылись. Остановка ожидания, повторная проверка и смена вкладки сохранили одно серверное задание без дублей. После завершения процесса и повторного запуска приложения восстановились сессия Keychain для публичного адреса и сохранённый профиль. Проверка физического iPhone остаётся отдельной.

Для проверки компиляции подготовлен [GitHub Actions workflow](../.github/workflows/ios-build.yml): на `macos-15` он запускает `swift test --package-path ios/GastroAPI`, затем собирает схему `GastroGuide` для `generic/platform=iOS Simulator` с `CODE_SIGNING_ALLOWED=NO`. Workflow использует только чтение репозитория, не сохраняет checkout credentials и не обращается к backend или LLM; ключи, сертификаты и provisioning не требуются. Он запускается при push изменений `ios/**` или самого workflow и вручную через `workflow_dispatch`.

По решению пользователя код и workflow остаются локально в ветке `feat/gastrogid-mvp`: push и запуск CI не выполнялись. YAML, пути и команды проверены локально, но результата сборки на GitHub runner ещё нет. CI build проверяет компиляцию и Foundation тесты; запуск приложения и Keychain проверяются отдельно в подписанной сборке Simulator.

Сборка Simulator, 10 XCTest, основные экраны через публичный HTTPS API, настоящий подбор GLM и восстановление сессии из Keychain подтверждены. Отдельный PASS переносимого Foundation клиента на Mac относится к сетевому API и не заменяет проверку физического iPhone.

Минимальный клиент показывает список ресторанов; карта, геолокация, пуши, OAuth и доставка находятся за пределами этого мобильного MVP.
