# ГастроГид

Мобильное приложение для поиска блюд в нескольких ресторанах с учётом вкусов, аллергий, бюджета и калорийности. Пользователь пишет «Хочу хинкали, не очень острые, до 900 рублей», а система подбирает блюда из актуальных меню, показывает цену, КБЖУ и источники. Съеденное можно записать в дневник питания.

Меню публикуются в разных форматах, а запросы людей редко совпадают с названиями блюд. Проект объединяет импорт меню, семантический поиск и мультиагентный подбор, чтобы учитывать несколько ограничений в одном запросе.

## Команда

| Участник | GitHub |
| --- | --- |
| Михаил Рословец | [D9veth](https://github.com/D9veth) |
| Захар Урванцев | [z-urvancev](https://github.com/z-urvancev) |
| Карина Самсонова | [karina-samsonova](https://github.com/karina-samsonova) |
| Григорий Пустовалов | [gp8stvlv](https://github.com/gp8stvlv) |

## Что реализовано

- Поиск блюда или составление приёма пищи с учётом профиля, цены и КБЖУ.
- Импорт HTML, PDF и фото меню: OCR, проверка черновика и публикация новой версии.
- Рекомендации с проверяемыми источниками и трассировкой работы агентов.
- Проактивная персональная лента для пользователей, которые её включили.
- Дневник с дробными порциями, дневными итогами и сохранёнными данными блюда.
- Нативный SwiftUI клиент: вход, профиль, рестораны, подбор и дневник.

## LLM и RAG

Провайдер — **Z.AI**, модель — **`glm-5.3-flash`**. Через LangChain она разбирает запрос, координирует специалистов, выбирает поисковые запросы и составляет варианты из найденных блюд. LangGraph управляет переходами между координатором, поиском, проверкой питания и планировщиком.

LLM нужна для понимания естественного языка и выбора сочетаний блюд с несколькими ограничениями. Цены, КБЖУ и аллергены проверяются по данным сервисов; численные итоги считает код с `Decimal`. Модель выбирает существующие ID блюд, а программный валидатор проверяет результат перед выдачей.

Для RAG используется локальная multilingual MiniLM через FastEmbed: эмбеддинги размерности 384 и поиск в PostgreSQL/pgvector. API-ключ LLM хранится только на backend. Провайдер, endpoint и модель настраиваются в `.env`.

## Запуск через Docker

Нужны Docker Engine/Desktop с Compose v2, Python 3 для генерации конфигурации и доступ в интернет для сборки образов и первой загрузки embedding-модели. Backend Python 3.12 и Tesseract `rus+eng` устанавливаются внутри образа.

```sh
git clone https://github.com/gp8stvlv/multiTeam.git
cd multiTeam
python3 scripts/configure.py
# В созданном .env заполните LLM_API_KEY; проверьте LLM_BASE_URL и LLM_MODEL.
docker compose up --build -d
```

Генератор создаёт случайные секреты, пароль администратора и приватный `.env`. Существующий `.env` не перезаписывается. Все переменные перечислены в [`.env.example`](.env.example). Без LLM-ключа каталог и дневник работают, а подбор возвращает `llm_not_configured`.

- Swagger: [http://127.0.0.1:8080/docs/](http://127.0.0.1:8080/docs/).
- Базовый адрес API: `http://127.0.0.1:8080/api/v1`.
- Готовность RAG: `curl http://127.0.0.1:8080/ready/catalog`. До окончания первой индексации поиск возвращает `503`.
- Остановка с сохранением данных: `docker compose stop`.

В PostgreSQL и Docker volumes сохраняются данные и кеш модели. После изменения LLM-настроек выполните `docker compose up -d --no-deps --force-recreate recommendations`.

## Мобильное приложение

Откройте `ios/GastroGuide/GastroGuide.xcodeproj` в Xcode 15+ и запустите схему **GastroGuide** на iPhone Simulator. В настройках подключения укажите адрес API и общий API-токен команды. Для локального backend используйте `http://127.0.0.1:8080/api/v1` без общего токена. API-токен и JWT сохраняются в Keychain отдельно для каждого сервера.

Внешний адрес — `https://90.156.213.217/api/v1`. Публичный gateway требует `X-API-Key` для всех API-запросов, включая регистрацию и вход. Swagger доступен на `https://90.156.213.217/docs/`: HTTP Basic username — `team`, пароль — общий API-токен; для вызовов API укажите его также в **Authorize → TeamAPIKey**.

## Тестирование

Backend-тесты запускаются в образе с изолированными SQLite-базами:

```sh
docker compose run --rm --no-deps \
  -v "$PWD/tests:/app/tests:ro" -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/.env.example:/app/.env.example:ro" \
  -e APP_ENV=test -e LLM_API_KEY= \
  -e CATALOG_DATABASE_URL=sqlite+pysqlite:///:memory: \
  -e USERS_DATABASE_URL=sqlite+pysqlite:///:memory: \
  -e RECOMMENDATIONS_DATABASE_URL=sqlite+pysqlite:///:memory: \
  -e CATALOG_RUN_MODEL_TESTS=1 catalog python -m pytest -q /app/tests
```

Проверка API с вызовом LLM после запуска backend:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/smoke.py --agents
```

Для внешнего сервера задайте `GASTRO_API_KEY` в окружении и добавьте `--url https://90.156.213.217 --public`. Флаг `--imports` проверяет HTML-импорт, `--proactive` вместе с `--agents` ждёт scheduler персональной ленты. Unit-тесты используют модели и HTTP mocks; smoke с `--agents` вызывает LLM-провайдер.

## Структура и документация

```text
services/       catalog, users, recommendations и общий код
ios/            SwiftUI приложение и Foundation API-пакет
docs/           архитектура, агенты, ER и API
tests/          проверки backend и конфигурации
scripts/        конфигурация, smoke, экспорт OpenAPI и ER, deployment
deploy/         Nginx, Swagger UI и инициализация PostgreSQL
infra/public/   необязательный HTTPS edge и обновление сертификата
```

- [Архитектура системы и RAG](docs/architecture.md).
- [Мультиагентная архитектура](docs/agents.md).
- [ER-диаграмма и владение данными](docs/er.md).
- [Swagger, OpenAPI и примеры запросов](docs/api.md).
