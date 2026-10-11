# ГастроГид

Учебный MVP мобильного приложения: поиск блюд в нескольких ресторанах, рекомендации с учётом предпочтений и аллергий, дневник питания и КБЖУ. Основная реализация находится на backend; iOS — минимальный нативный SwiftUI клиент.

Работающий API: **https://90.156.213.217/api/v1**, Swagger: **https://90.156.213.217/docs/**. Backend, база, RAG и GLM работают на VPS; держать Docker или SSH-туннель на Mac не требуется. Этот HTTPS адрес уже выбран по умолчанию в iOS. В существующей установке сохранённый адрес можно изменить в «Профиль → Адрес API».

## Запуск локально

Нужны Docker с Compose. Из корня репозитория:

```sh
python3 scripts/configure.py
# Отредактируйте .env: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY.
docker compose up --build -d
```

`configure.py` создаёт приватный `.env` со случайными JWT, внутренним токеном, паролем PostgreSQL и паролем демонстрационного администратора. Существующий файл не перезаписывается. `.env` исключён из Git. Если он уже создан, первый шаг пропускается.

Локальный API и Swagger: **http://127.0.0.1:8080/docs/**. Вверху переключаются три актуальные спецификации, автоматически сформированные из реализованных маршрутов. Для локальной разработки укажите в настройках iOS Simulator **http://127.0.0.1:8080/api/v1**. Для мобильного клиента откройте `ios/GastroGuide/GastroGuide.xcodeproj`, инструкции — [ios/README.md](ios/README.md).

Первая индексация скачивает реальную multilingual MiniLM ONNX модель (384 измерения). До её завершения `/ready/catalog` и поиск возвращают `503`; просмотр каталога уже доступен. Кеш модели и базы сохраняются в Docker volumes.

```sh
docker compose ps
curl http://127.0.0.1:8080/ready/catalog
curl http://127.0.0.1:8080/ready/users
curl http://127.0.0.1:8080/ready/recommendations
docker compose logs --tail=60 catalog recommendations
```

Без ключа LLM каталог и дневник работают, а создание рекомендаций возвращает явный `llm_not_configured`. Тестовая модель или поиск по ключевым словам не подменяют производственные вызовы.

## GLM и OpenRouter

Модель вызывается через LangChain `ChatOpenAI` с настраиваемым совместимым chat/tools API; использование этого адаптера не означает выбор OpenAI как провайдера.

Для обычного GLM API:

```dotenv
LLM_BASE_URL=https://api.z.ai/api/paas/v4
LLM_MODEL=<доступная модель с tool calling>
LLM_API_KEY=<ваш ключ>
```

Для этого проекта пользователь подтвердил отдельное разрешение на GLM Coding Plan. По умолчанию `.env.example` использует `https://api.z.ai/api/coding/paas/v4` и `glm-5.3-flash`; можно явно заменить модель на `glm-5.3`. Для OpenRouter используйте `https://openrouter.ai/api/v1` и актуальный ID модели с поддержкой инструментов. Бесплатный вариант выбирается явно через ID `:free`, если он доступен. [Z.AI API](https://docs.z.ai/api-reference/introduction), [Coding Plan endpoint](https://docs.z.ai/devpack/faq), [OpenRouter free variants](https://openrouter.ai/docs/guides/routing/model-variants/free).

После изменения переменных:

```sh
docker compose up -d --force-recreate recommendations
```

Эмбеддинги для RAG выполняются локально через FastEmbed, без расходования GLM/OpenRouter токенов. Ключ LLM хранится только на сервере, в приложение не передаётся.

Адаптер Z.AI сохраняет параметр `max_tokens`, который стандартный ChatOpenAI переименовывает в `max_completion_tokens`. Для GLM 5.3 используется `reasoning_effort=low`, лимит 6000 токенов и поддерживаемый `tool_choice=auto`; эти параметры проверены на фактических HTTP-запросах SDK. Контрактные тесты не заменяют вызов API с настоящим ключом.

## Развёрнутый VPS

Backend установлен на `ubuntu@90.156.213.217` в `/home/ubuntu/gastrogid`. Публичный Nginx принимает HTTPS на 443 с доверенным сертификатом Let’s Encrypt для IP; HTTP на 80 перенаправляет на HTTPS и обслуживает ACME challenge. Gateway слушает только `127.0.0.1:8080`, три сервиса и PostgreSQL доступны внутри Docker. Внутренние методы и service health/ready снаружи закрыты. Профиль, дневник и задания требуют JWT.

Сертификат автоматически обновляется systemd timer каждые шесть часов; реальная проверка renewal с ACME staging и перезагрузка Nginx прошли. TLS ключи и ACME state хранятся в `/var/lib/gastrogid/tls` вне репозитория. Контейнеры имеют `restart: unless-stopped`, Docker и timer включены в автозапуск. Локальные контейнеры и SSH-туннель остановлены; внешний API и iOS проверены после их остановки.

Секреты VPS созданы отдельно в `/home/ubuntu/gastrogid/.env`; загрузчик `scripts/deploy.sh` сохраняет этот файл и не копирует локальный `.env`. GLM Coding Plan с `glm-5.3-flash` подключён на VPS. Живые `dish_search`, `meal_plan` и проактивная рекомендация штатного scheduler прошли проверку цен, КБЖУ и источников. Admin trace подтверждает реальные вызовы специалистов. После изменения настроек провайдера нужно пересоздать recommendations.

```sh
# На VPS, из /home/ubuntu/gastrogid, после изменения настроек LLM:
sudo docker compose -f compose.yaml -f compose.public.yaml \
  up -d --no-deps --force-recreate recommendations
# На Mac, напрямую через внешний HTTPS:
.venv/bin/python scripts/smoke.py --url https://90.156.213.217 --public --agents
# Полная проверка с настоящим scheduler персональной ленты:
.venv/bin/python scripts/smoke.py --url https://90.156.213.217 --public --agents --proactive
```

Для повторного развёртывания HTTPS на этом IP после загрузки исходников, настройки `.env` и запуска backend на VPS:

```sh
cd /home/ubuntu/gastrogid
sudo install -d -m 700 /var/lib/gastrogid/tls/letsencrypt \
  /var/lib/gastrogid/tls/work /var/lib/gastrogid/tls/logs
sudo install -d -m 755 /var/lib/gastrogid/tls/webroot/.well-known/acme-challenge
sudo env PUBLIC_NGINX_CONFIG=./infra/public/nginx-http.conf \
  docker compose -f compose.yaml -f compose.public.yaml \
  up -d --no-deps --force-recreate edge
sudo docker compose -f compose.yaml -f compose.public.yaml pull certbot
sudo docker compose -f compose.yaml -f compose.public.yaml \
  run --rm --no-deps certbot certonly \
  --non-interactive --agree-tos --register-unsafely-without-email \
  --required-profile shortlived --webroot --webroot-path /var/www/certbot \
  --ip-address 90.156.213.217 --cert-name gastrogid-public
sudo docker compose -f compose.yaml -f compose.public.yaml \
  up -d --no-deps --force-recreate gateway edge
sudo install -d /usr/local/libexec
sudo install -m 755 scripts/renew-public-tls.sh /usr/local/libexec/gastrogid-renew-public-tls
sudo install -m 644 infra/public/gastrogid-tls-renew.service /etc/systemd/system/
sudo install -m 644 infra/public/gastrogid-tls-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gastrogid-tls-renew.timer
```

Первичное получение сертификата требует доступа к порту 80. Bootstrap конфигурация обслуживает только ACME; HTTPS конфигурация включается последней командой Compose. Для другого IP замените адрес в конфигурациях Nginx, Certbot и iOS. При обновлении bind-mounted Nginx файлов пересоздайте `gateway edge`, чтобы контейнеры получили новые файлы.

```sh
# На VPS:
sudo docker compose -f compose.yaml -f compose.public.yaml exec -T edge nginx -t
sudo systemctl is-active gastrogid-tls-renew.timer
sudo systemctl list-timers gastrogid-tls-renew.timer --no-pager
sudo /usr/local/libexec/gastrogid-renew-public-tls --dry-run --run-deploy-hooks
# Из внешней сети:
curl --fail https://90.156.213.217/health
```

## Разделение системы

| Компонент | Ответственность |
| --- | --- |
| Catalog, порт 8001 | Рестораны, версии меню, HTML/PDF/фото, OCR, проверяемые источники, семантический поиск и pgvector |
| Users & Nutrition, порт 8002 | Регистрация/JWT, предпочтения и аллергии, дневник со снимками, точная арифметика КБЖУ, обратная связь |
| Recommendations, порт 8003 | LangGraph, долговечные задания, трассы агентов, персональная лента |
| Gateway, порт 8080 | Единый REST API, Swagger, маршрутизация; внутренние методы не доступны извне |
| Public edge, порты 80/443 | Доверенный HTTPS, ACME и ограничение публичных маршрутов |
| PostgreSQL | Три базы и три сервисных пользователя; общая инфраструктура, раздельное владение данными |
| SwiftUI | Вход, профиль, каталог, подбор, персональная лента и дневник |

Координатор LangGraph разбирает запрос и выбирает следующего специалиста. Search Agent вызывает семантический поиск и читает отзывы; Nutrition Agent вызывает калькулятор КБЖУ; Planner выбирает существующие menu item IDs и целые порции. Координатор может повторить поиск или задать уточнение. Программный Validator снова читает актуальные меню, проверяет источники и ограничения, затем рассчитывает числа через Nutrition Service. Число шагов и время запуска ограничены.

В трассе сохраняются названия инструментов, источники, время, токены и краткие факты о выполнении. Скрытые рассуждения модели, JWT и API-ключи не сохраняются. Задания и лента хранятся в PostgreSQL, после остановки worker возвращаются в очередь. Проактивный scheduler создаёт максимум одну подборку в локальный день для пользователя, который включил ленту; учитывает профиль и остаток дневного ориентира. Push-уведомления не входят в MVP.

## Что можно показать на защите

1. Зарегистрироваться, задать аллергию, бюджет, кухни и дневной ориентир.
2. Найти «хочу хинкали» или «что-нибудь к пиву»: получить варианты, цену, КБЖУ и ссылки на источники. Доступны поиск блюда и составление приёма пищи.
3. Администратором загрузить меню HTML/PDF/фото, дождаться `needs_review`, исправить черновик и опубликовать новую версию. Старые версии сохраняются. Swagger содержит все административные методы.
4. Явно записать съеденную часть порции и посмотреть дневные итоги; повтор запроса не создаёт дубль. После обновления меню старая запись дневника сохраняет исходные числа.
5. Посмотреть admin-only trace с работой трёх специалистов, инструментами и evidence.

Стартовые данные: 5 **вымышленных** ресторанов, 60 блюд и 10 демонстрационных отзывов. КБЖУ fixtures помечены как `estimated` с явным методом; это не измерения настоящих ресторанов. Импорт не выдумывает отсутствующий состав и нутриенты. Неизвестные значения остаются `null`; известные подытоги показываются отдельно. Исключённый аллерген требует явного подтверждённого `declared_absent` из источника; отсутствие сведений не считается подтверждением.

## Проверки

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py
```

Локальные unit/integration tests используют явно внедряемые fakes для моделей и HTTP. Они проверяют граф, авторизацию, обязательные ограничения, источники, точность арифметики, persistence и восстановление заданий; не доказывают доступность внешнего LLM. Живой smoke сценарий проверяет реальные сервисы и эмбеддинги. `--agents` проверяет настоящий LangGraph для поиска блюда и составления приёма пищи. `--proactive` вместе с `--agents` включает ленту только для созданного smoke-аккаунта и ждёт настоящий scheduler; ожидание может занять до 30 минут. В конце лента этого аккаунта выключается. Все три сценария прошли на VPS с настоящим GLM API.

Фактические результаты и оставшиеся проверки записаны в [docs/VERIFICATION.md](docs/VERIFICATION.md).

OCR-тесты требуют Tesseract `rus+eng`; он уже установлен в Docker образе. На macOS без Tesseract эти два теста пропускаются. Для проверки внутри собранного образа:

```sh
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests:ro" catalog python -m pytest -q /app/tests/test_catalog.py
```

Полная сборка SwiftUI в Xcode 27.0 и настоящий Foundation XCTest suite прошли: 10 тестов без ошибок. В подписанном iPhone 17 Simulator через внешний HTTPS проверены вход, профиль, каталог и evidence, дробная порция и дневник, реальный подбор GLM, отмена/возобновление без дубля и восстановление сессии из Keychain после перезапуска. Команды и фактические результаты приведены в `docs/VERIFICATION.md`.

## Команда из четырёх человек

| Участник | Зона работы |
| --- | --- |
| 1 | Catalog, импорт/OCR, RAG, источники и качество поиска |
| 2 | Users & Nutrition, авторизация, профиль, дневник и расчёты |
| 3 | Recommendations, LangGraph, evals, проактивная лента |
| 4 | SwiftUI, интеграция API, пользовательский сценарий; совместно gateway/Compose |

Контракт: [docs/IMPLEMENTATION_CONTRACT.md](docs/IMPLEMENTATION_CONTRACT.md). Актуальная ER: [SVG](docs/er-runtime.svg) и [Mermaid](docs/er-runtime.mmd), архитектура: [docs/architecture.mmd](docs/architecture.mmd); обе сверены с реализацией. Первоначальная проектная ER/OpenAPI сохранена как `docs/design-*`; актуальный Swagger берётся из backend. Карта, геолокация телефона, доставка/оплата, сбор реальных внешних отзывов и production масштабирование не входят в этот MVP. Каталог запускается с одним worker/replica; одна PostgreSQL машина подходит для учебного проекта.
