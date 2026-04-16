# Codex Telegram Gateway

Telegram-обвязка для `Codex CLI`, где каждый Telegram chat/topic привязывается к отдельному workspace и использует свою отдельную Codex-сессию.

## Что это

- Telegram-бот с long polling.
- `chat_id + topic/thread_id -> workspace`.
- отдельная Codex-сессия на workspace через `codex exec` и `codex exec resume`.
- SQLite для mapping и session state.
- безопасная валидация путей по whitelist roots.
- базовые inline-кнопки: `Status`, `Where`, `Reset Session`.

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
- по умолчанию systemd unit использует `deploy`;
- bind path проходит `resolve(strict=True)` и проверку на выход за разрешённые roots;
- разрешённые roots задаются в `config.toml`;
- `/bind` ограничен admin user ids;
- `/approvals` разрешает только `never` и `untrusted`;
- `/execmode` разрешает только `read-only` и `workspace-write`;
- режим `dangerously-bypass-approvals-and-sandbox` не используется;
- секреты лежат в `.env`, в логи не попадают.

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
mkdir -p /var/lib/codex-telegram-gateway /var/log/codex-telegram-gateway
```

Заполнить `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_IDS=111111111,222222222
OPENAI_API_KEY=...
```

Заполнить `config.toml`:

- `codex_bin`
- `sqlite_path`
- `runtime_dir`
- `log_dir`
- `allowed_roots`
- `workspace_defaults`

Важно: пользователь сервиса должен иметь рабочий `codex login` или нужные env credentials.

## Запуск

```bash
cd /srv/projects/codex-telegram-gateway
PYTHONPATH=src python3 -m codex_telegram_gateway --config config.toml --env-file .env
```

## Systemd

```bash
sudo cp /srv/projects/codex-telegram-gateway/systemd/codex-telegram-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now codex-telegram-gateway
sudo systemctl status codex-telegram-gateway
```

## Как добавить workspace

В Telegram:

```text
/workspaces
/use infra
/bind myproj /srv/projects/myproj
/use project:myproj
```

`/workspaces` показывает как явно зарегистрированные workspace, так и авто-алиасы для директории первого уровня внутри `/srv/projects`.

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
- Если Codex падает сразу, проверить `codex_bin`, credentials и права на `runtime_dir`.
- Если `/bind` отклоняется, проверить canonical path и `allowed_roots`.
- Если в группе не видны сообщения, проверить privacy mode бота.

## Ограничения MVP

- long polling, а не webhook;
- стриминг best-effort и зависит от JSON events `codex exec --json`;
- health endpoint пока не поднят, роль health выполняют `/status` и `/debugstatus`;
- unit по умолчанию использует `deploy`; для более жёсткой изоляции можно перевести на отдельного service user после настройки прав и отдельного `codex login`.
