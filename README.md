# Codex Telegram Gateway

Telegram-обвязка для `Codex CLI`, где каждый Telegram chat/topic привязывается к отдельному workspace и использует свою отдельную Codex-сессию.

## Что это

- Telegram-бот с long polling.
- `chat_id + topic/thread_id -> workspace`.
- отдельная Codex-сессия на workspace через `codex exec` и `codex exec resume`.
- SQLite для mapping и session state.
- безопасная валидация путей по whitelist roots.
- базовые inline-кнопки: `Status`, `Where`, `Reset Session`.
- настраиваемый `default workspace` для непривязанных чатов.
- поддержка Telegram forum topics: `/use` в forum-группе создаёт новую тему с отдельной Codex-сессией.

## Почему Python

Выбран `Python 3.12+` и stdlib-only подход:

- уже есть на сервере;
- не нужен отдельный build step;
- меньше внешних зависимостей и проще systemd;
- надёжный `subprocess`-адаптер к существующему `Codex CLI`.

## Требования

- Debian 13
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
- `/approvals` разрешает только `never` и `untrusted`;
- `/execmode` разрешает только `read-only` и `workspace-write`;
- режим `dangerously-bypass-approvals-and-sandbox` не используется;
- секреты лежат в `.env`, в логи не попадают.
- если `OPENAI_API_KEY` не задан, сервис копирует `auth.json` из `codex_auth_source_home` в свой runtime-home и использует существующий ChatGPT/Codex login.

## Команды

- `/start`
- `/help`
- `/status`
- `/where`
- `/workspaces`
- `/bind <name> <path>`
- `/use <name>`
- `/newsession`
- `/resetsession`
- `/stop`
- `/pwd`
- `/execmode [readonly|workspace-write]`
- `/approvals [never|untrusted]`
- `/model [name]`
- `/debugstatus`

## Telegram bot setup

1. Создать бота через `@BotFather`.
2. Получить token.
3. Для групп включить privacy mode так, как вам нужно.
4. Добавить бота в группу и при необходимости дать право читать сообщения.
5. Для topic-based работы использовать forum topics в supergroup.

## Установка

```bash
cd /srv/projects
python3 -m venv /srv/projects/codex-telegram-gateway/.venv
/srv/projects/codex-telegram-gateway/.venv/bin/pip install -e /srv/projects/codex-telegram-gateway
cp /srv/projects/codex-telegram-gateway/.env.example /srv/projects/codex-telegram-gateway/.env
cp /srv/projects/codex-telegram-gateway/config.example.toml /srv/projects/codex-telegram-gateway/config.toml
sudo mkdir -p /var/lib/codex-telegram-gateway /var/log/codex-telegram-gateway
```

Под `service_user` ниже имеется в виду пользователь, от имени которого будет запускаться сервис.

После создания каталогов выдать права:

```bash
sudo chown -R <service_user>:<service_group> /var/lib/codex-telegram-gateway /var/log/codex-telegram-gateway
```

Заполнить `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_IDS=111111111,222222222
```

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
- значение `codex_auth_source_home` должно быть настроено под ваш сервер и выбранного пользователя.

Важно: пользователь сервиса должен иметь рабочий `codex login` в `codex_auth_source_home` или API key в `.env`.

Поведение непривязанных чатов:

- если задан `default_workspace_name`, новый чат или topic без явного `/use` работает в этом workspace;
- это удобно для отдельного server-ops чата, где агент может проверять `docker ps`, `systemctl`, `journalctl`, загрузку сервера и общую диагностику;
- для рабочих чатов и проектов можно потом явно переключиться через `/use <name>`.

## Запуск

```bash
cd /srv/projects/codex-telegram-gateway
PYTHONPATH=src .venv/bin/python3 -m codex_telegram_gateway --config config.toml --env-file .env
```

## Systemd

Перед установкой unit:

- откройте `systemd/codex-telegram-gateway.service`;
- замените `User=` и `Group=` на вашего сервисного пользователя;
- при необходимости проверьте `ReadWritePaths=` и пути к `codex_bin` и `codex_auth_source_home`.

```bash
sudo cp /srv/projects/codex-telegram-gateway/systemd/codex-telegram-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now codex-telegram-gateway
sudo systemctl status codex-telegram-gateway
```

Важно: systemd unit запускает сервис через `/srv/projects/codex-telegram-gateway/.venv/bin/python3`, поэтому перед `enable --now` нужно создать `.venv` и выполнить `.venv/bin/pip install -e .`.

Также `User=` в unit должен совпадать с владельцем `/var/lib/codex-telegram-gateway` и `/var/log/codex-telegram-gateway`.

## Как добавить workspace

В Telegram:

```text
/workspaces
/use infra
/bind myproj /srv/projects/myproj
/use project:myproj
```

`/workspaces` показывает как явно зарегистрированные workspace, так и авто-алиасы для директории первого уровня внутри `/srv/projects`.

Пример server-ops workspace по умолчанию:

```toml
default_workspace_name = "server-ops"

[workspace_defaults]
server-ops = "/srv/projects"
infra = "/srv/infra"
openclaw = "/srv/openclaw"
```

Практически это значит, что новый непривязанный чат сразу сможет задавать вопросы про состояние сервера. Если нужен другой корень, поменяйте путь у `server-ops`.

Поведение `/use` в chats with topics:

- если команда `/use <workspace>` вызывается в Telegram forum supergroup или в private chat с включённым topic mode, бот создаёт новый topic;
- новый topic привязывается к отдельной Codex-сессии, даже если путь workspace совпадает с уже существующим;
- это позволяет держать независимые обсуждения и контекст по одному и тому же проекту в разных темах.

## Изоляция

- binding хранится на уровне `chat_id + thread_id`;
- session state хранится на уровне `workspace_name`;
- одновременные запросы в один workspace сериализуются через `asyncio.Lock`;
- для каждого запроса создаётся отдельный `codex` subprocess;
- активный процесс можно прервать через `/stop`;
- `session_id` можно сбросить через `/resetsession`.

## Логи и диагностика

- structured JSON logs: `/var/log/codex-telegram-gateway/gateway.log`
- входящие Telegram updates логируются
- запуск/завершение Codex runs логируется
- `/status` и `/debugstatus` помогают понять состояние

## Тесты

```bash
cd /srv/projects/codex-telegram-gateway
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Troubleshooting

- Если бот не отвечает, проверить `systemctl status` и `journalctl -u codex-telegram-gateway -f`.
- Если сервис падает сразу с `PermissionError`, проверить владельца и права `runtime_dir` и `log_dir`.
- Если Codex падает сразу, проверить `codex_bin`, credentials и права на `runtime_dir`.
- Если используется ChatGPT login без API key, проверить наличие `${codex_auth_source_home}/auth.json` и права на чтение этого файла.
- Если `/bind` отклоняется, проверить canonical path и `allowed_roots`.
- Если в группе не видны сообщения, проверить privacy mode бота.

## Ограничения MVP

- long polling, а не webhook;
- стриминг best-effort и зависит от JSON events `codex exec --json`;
- health endpoint пока не поднят, роль health выполняют `/status` и `/debugstatus`;
- systemd unit в репозитории содержит примерные значения `User=`, `Group=` и путей; перед production-запуском их нужно привести в соответствие с вашей схемой пользователей и прав.
