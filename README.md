# Codex Telegram Gateway

Telegram-обвязка для `Codex CLI`, где каждый Telegram chat/topic привязывается к отдельному workspace и использует свою отдельную Codex-сессию.

## Что это

- Telegram-бот с long polling.
- `chat_id + topic/thread_id -> workspace`.
- отдельная Codex-сессия на workspace через `codex exec` и `codex exec resume`.
- SQLite для mapping и session state.
- execution profiles и workspace-scoped policy overrides для sandbox, approvals и network mode.
- безопасная валидация путей по whitelist roots.
- preflight-проверка workspace перед запуском или restart сессии.
- базовые inline-кнопки: `Status`, `Where`, `Reset Session`.
- настраиваемый `default workspace` для непривязанных чатов.
- поддержка Telegram forum topics: `/use` в forum-группе создаёт новую тему с отдельной Codex-сессией.

## Почему Python

Выбран `Python 3.12+` и stdlib-only подход:

- обычно уже доступен в современных Linux-окружениях;
- не нужен отдельный build step;
- меньше внешних зависимостей и проще systemd;
- надёжный `subprocess`-адаптер к существующему `Codex CLI`.

## Требования

- Linux-сервер с `systemd`
- Python 3.12+
- установленный `Codex CLI`
- действующий Telegram bot token
- OpenAI/Codex credentials для пользователя, под которым запускается сервис

## Архитектура

1. Telegram update приходит в long polling loop.
2. Бот определяет `chat_id` и `message_thread_id`.
3. По связке ищется binding в SQLite.
4. Для workspace берётся свой `session_id`.
5. Запускается отдельный процесс `codex exec` в нужной директории.
6. Если `session_id` уже есть, используется `codex exec resume <session_id>`.
7. JSONL events читаются со stdout, бот обновляет одно Telegram-сообщение по мере стриминга.
8. После завершения `session_id` сохраняется обратно в SQLite.

Это даёт изоляцию контекста без переключения директорий внутри общей сессии.

## Безопасность

- сервис не запускается от `root`;
- systemd unit должен запускаться от выделенного сервисного пользователя или от того пользователя, чью авторизацию Codex вы хотите использовать;
- bind path проходит `resolve(strict=True)` и проверку на выход за разрешённые roots;
- разрешённые roots задаются в `config.toml`;
- `/bind` ограничен admin user ids;
- execution profiles задают sandbox, approvals, network mode и command rule group на workspace;
- `/approvals`, `/execmode` и privileged profile transitions централизованно проверяются policy layer;
- gateway сам отклоняет команды, которые нарушают command rule group для текущего profile;
- режим `dangerously-bypass-approvals-and-sandbox` не используется;
- `break-glass` существует как временный профиль с TTL, а не как постоянный default;
- network mode и approvals рассматриваются как gateway-owned policy, даже если Codex CLI не умеет выразить их отдельными флагами;
- секреты лежат в `.env`, в логи не попадают.
- если `OPENAI_API_KEY` не задан, сервис копирует `auth.json` из `codex_auth_source_home` в свой runtime-home и использует существующий ChatGPT/Codex login.

## Execution Profiles

Поддерживаются три базовых profile:

- `default` : безопасный профиль по умолчанию для обычных project/workspace запросов.
- `ops` : более привилегированный профиль для controlled operational workspaces вроде `/srv/infra`.
- `break-glass` : временный аварийный профиль с TTL для исключительных случаев.

Порядок применения policy детерминированный:

1. profile default
2. workspace profile default
3. durable workspace override
4. temporary break-glass override

Что хранится в SQLite:

- `profile_name`
- `sandbox_mode`
- `approval_policy`
- `network_mode`
- `command_rule_set_version`
- `break_glass_expires_at`
- busy/idle state, `last_stop_reason`, `last_restart_at`, `last_used_at`

Что вычисляется на лету:

- effective command rule group
- effective admin eligibility для requested transition
- active break-glass overlay при ещё не истёкшем TTL

## Workspace Preflight

Перед запуском или restart сессии gateway делает preflight:

- проверяет canonical path и выход за `allowed_roots`
- отклоняет symlink escape и traversal
- проверяет read/write доступ для service user
- создаёт `.codex` внутри workspace при необходимости
- выполняет create/delete probe и возвращает диагностический reason в Telegram и logs

## Команды

- `/start` : показывает стартовое сообщение, текущий workspace и базовые подсказки.
- `/help` : показывает список доступных команд.
- `/status` : показывает текущий workspace, session id, режим sandbox, approvals и занятость сессии.
- `/session show` : показывает profile, session id, sandbox, approvals, network mode, rule set, busy status и uptime.
- `/where` : показывает, какой workspace привязан к текущему chat/topic.
- `/workspaces` : показывает доступные workspace aliases и авто-алиасы `project:<name>`.
- `/bind <name> <path>` : создаёт или обновляет alias workspace на абсолютный путь и привязывает его к текущему chat/topic. Команда доступна только admin user.
- `/use <name>` : переключает текущий chat/topic на выбранный workspace. В chats with topics создаёт новый topic с отдельной Codex-сессией.
- `/session profile <name>` : меняет execution profile для текущего workspace и переводит следующий run на новую fresh session.
- `/session restart` : завершает текущую session state и готовит fresh session для следующего run.
- `/session reset` : алиас на reset текущей session state.
- `/newsession` : сбрасывает текущий session id для workspace и начинает новый контекст.
- `/resetsession` : то же, что `/newsession`.
- `/stop` : останавливает текущий активный run Codex для этого workspace.
- `/pwd` : краткий алиас для `/where`.
- `/execmode [readonly|workspace-write]` : legacy alias для sandbox override. После смены следующая session будет fresh.
- `/approvals [never|untrusted]` : legacy alias для approval override. После смены следующая session будет fresh.
- `/model [name]` : показывает текущую модель или задаёт новую для workspace.
- `/debugstatus` : показывает внутреннюю диагностику gateway и активные runtime. Команда доступна только admin user.

## Telegram bot setup

1. Создать бота через `@BotFather`.
2. Получить token.
3. Для групп включить privacy mode так, как вам нужно.
4. Для personal chats with topics включить `Threaded mode` в настройках бота через `@BotFather`.
5. Добавить бота в группу и при необходимости дать право читать сообщения.
6. Для topic-based работы использовать forum topics в supergroup или threaded mode в personal chat.

## Установка

В примерах ниже:

- `<project_dir>` : каталог, куда установлен проект;
- `<service_user>` / `<service_group>` : пользователь и группа, под которыми будет работать сервис;
- `<state_dir>` : каталог runtime/state данных;
- `<log_dir>` : каталог логов.

```bash
cd <project_dir>
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
cp config.example.toml config.toml
sudo mkdir -p <state_dir> <log_dir>
```

После создания каталогов выдать права:

```bash
sudo chown -R <service_user>:<service_group> <state_dir> <log_dir>
```

Заполнить `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_IDS=111111111,222222222
OPENAI_API_KEY=...   # optional
```

Если используется API key, его нужно прописывать именно в `.env` рядом с `TELEGRAM_BOT_TOKEN`.

Заполнить `config.toml`:

- `codex_bin`
- `codex_auth_source_home`
- `sqlite_path`
- `runtime_dir`
- `log_dir`
- `allowed_roots`
- `workspace_defaults`
- `default_workspace_name`

Проверьте соответствие путей выбранному сервисному пользователю:

- `codex_bin` должен указывать на доступный этому пользователю `codex`;
- `codex_auth_source_home` должен указывать на каталог `.codex` того пользователя, чью авторизацию нужно использовать;
- сервис должен иметь запись в `runtime_dir` и `log_dir`;
- сервис должен иметь чтение `codex_auth_source_home/auth.json`, если `OPENAI_API_KEY` не используется.

Авторизация работает так:

- если задан `OPENAI_API_KEY`, `codex` использует его;
- если `OPENAI_API_KEY` не задан, gateway копирует `${codex_auth_source_home}/auth.json` в свой runtime-home и использует существующий `codex login`;
- значение `codex_auth_source_home` должно быть настроено под ваше окружение и выбранного пользователя.

Важно: пользователь сервиса должен иметь рабочий `codex login` в `codex_auth_source_home` или API key в `.env`.

Поведение непривязанных чатов:

- если задан `default_workspace_name`, новый чат или topic без явного `/use` работает в этом workspace;
- это удобно для отдельного server-ops чата, где агент может проверять `docker ps`, `systemctl`, `journalctl`, загрузку сервера и общую диагностику;
- для рабочих чатов и проектов можно потом явно переключиться через `/use <name>`.

## Запуск

```bash
cd <project_dir>
PYTHONPATH=src .venv/bin/python3 -m codex_telegram_gateway --config config.toml --env-file .env
```

## Systemd

Перед установкой unit:

- откройте `systemd/codex-telegram-gateway.service`;
- замените `User=` и `Group=` на вашего сервисного пользователя;
- при необходимости проверьте `ReadWritePaths=` и пути к `codex_bin` и `codex_auth_source_home`.

```bash
sudo cp <project_dir>/systemd/codex-telegram-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now codex-telegram-gateway
sudo systemctl status codex-telegram-gateway
```

Важно: systemd unit запускает сервис через `<project_dir>/.venv/bin/python3`, поэтому перед `enable --now` нужно создать `.venv` и выполнить `.venv/bin/pip install -e .`.

Также `User=` в unit должен совпадать с владельцем `runtime_dir` и `log_dir`.

## Как добавить workspace

В Telegram:

```text
/workspaces
/use infra
/bind myproj /absolute/path/to/myproj
/use project:myproj
```

`/workspaces` показывает как явно зарегистрированные workspace, так и авто-алиасы для директории первого уровня внутри `project_alias_roots`.

Пример server-ops workspace по умолчанию:

```toml
default_workspace_name = "server-ops"

[workspace_defaults]
server-ops = "/absolute/path/for/general-ops"
infra = "/absolute/path/to/infra"
openclaw = "/absolute/path/to/openclaw"
```

Практически это значит, что новый непривязанный чат сразу сможет задавать вопросы про состояние сервера. Если нужен другой корень, поменяйте путь у `server-ops`.

Поведение `/use` в chats with topics:

- если команда `/use <workspace>` вызывается в Telegram forum supergroup или в private chat с включённым topic mode, бот создаёт новый topic;
- новый topic привязывается к отдельной Codex-сессии, даже если путь workspace совпадает с уже существующим;
- это позволяет держать независимые обсуждения и контекст по одному и тому же проекту в разных темах.
- для personal chat этот режим требует, чтобы у бота был включён `Threaded mode` через `@BotFather`.

## Изоляция

- binding хранится на уровне `chat_id + thread_id`;
- execution policy и session state хранятся на уровне `workspace_name`;
- одновременные запросы в один workspace сериализуются через `asyncio.Lock`;
- для каждого запроса создаётся отдельный `codex` subprocess;
- активный процесс можно прервать через `/stop`;
- `session_id` нормализуется к `NULL`, а не к пустой строке;
- profile change, `/session restart`, `/session reset`, `/execmode` и `/approvals` приводят к fresh session для следующего run.

## Логи и диагностика

- structured JSON logs: `<log_dir>/gateway.log`
- входящие Telegram updates логируются
- запуск/завершение Codex runs логируется
- `/status` и `/debugstatus` помогают понять состояние

Основные audit events:

- `config_validation_started`, `config_validation_succeeded`
- `workspace_store_migration_started`, `workspace_store_migration_finished`
- `policy_services_bootstrapped`
- `admin_denied`, `privileged_transition_allowed`
- `session_start`, `session_stop`, `session_restart`
- `workspace_busy_conflict`
- `break_glass_active`, `break_glass_expired`
- `preflight_failed`
- `command_rule_violation`

Как их интерпретировать:

- `admin_denied` значит, что Telegram command был отклонён policy layer до изменения state.
- `workspace_busy_conflict` значит, что profile/policy transition запросили во время активного run.
- `preflight_failed` значит, что workspace не прошёл filesystem safety/readiness checks.
- `command_rule_violation` значит, что gateway отклонил prompt до запуска `codex exec`.
- `break_glass_expired` значит, что временный аварийный профиль снят и effective policy вернулась к менее привилегированному baseline.

## Тесты

```bash
cd <project_dir>
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Troubleshooting

- Если бот не отвечает, проверить `systemctl status` и `journalctl -u codex-telegram-gateway -f`.
- Если сервис падает сразу с `PermissionError`, проверить владельца и права `runtime_dir` и `log_dir`.
- Если Codex падает сразу, проверить `codex_bin`, credentials и права на `runtime_dir`.
- Если используется ChatGPT login без API key, проверить наличие `${codex_auth_source_home}/auth.json` и права на чтение этого файла.
- Если `/bind` отклоняется, проверить canonical path и `allowed_roots`.
- Если run не стартует и бот пишет про preflight, проверить права service user на workspace, `.codex` и create/delete probe.
- Если `/session profile break-glass` или другие privileged transitions отклоняются, проверить `TELEGRAM_ADMIN_IDS` и секцию `admin_only` в `config.toml`.
- Если ops workspace ожидаемо требует другой policy, проверить `workspace_profile_defaults`, `execution_profiles` и `command_rule_groups`.
- Если в группе не видны сообщения, проверить privacy mode бота.

## Ограничения MVP

- long polling, а не webhook;
- стриминг best-effort и зависит от JSON events `codex exec --json`;
- health endpoint пока не поднят, роль health выполняют `/status` и `/debugstatus`;
- systemd unit в репозитории содержит примерные значения `User=`, `Group=` и путей; перед production-запуском их нужно привести в соответствие с вашей схемой пользователей и прав.
