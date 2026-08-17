# VPS Control MCP

Минимальный MCP-сервер: ChatGPT получает терминал и файлы VPS почти как Codex CLI.

## Инструменты

- `status`
- `list_dir`
- `read_file`
- `search_text`
- `write_file`
- `apply_patch`
- `git_status`
- `git_diff`
- `run_command`

## Установка на VPS

```bash
git clone -b agent/vps-control-mcp https://github.com/eav021107-debug/WQPU.git
cd WQPU
sudo bash tools/vps-control/scripts/install.sh
```

По умолчанию рабочая папка:

```text
/srv/vps-control-workspace
```

Можно выбрать другую:

```bash
sudo VPS_CONTROL_ROOT=/путь/к/проекту bash tools/vps-control/scripts/install.sh
```

MCP слушает только VPS локально:

```text
http://127.0.0.1:8765/mcp
```

## Подключение ChatGPT

1. ChatGPT → `Settings → Security and login → Developer mode`.
2. OpenAI Platform → создать Secure MCP Tunnel.
3. На VPS запустить `tunnel-client` и направить его на `http://127.0.0.1:8765/mcp`.
4. ChatGPT Plugins → `+` → Connection: `Tunnel` → выбрать tunnel.
5. В чате включить Developer mode и выбрать `VPS Control`.

После этого можно написать:

```text
Проверь проект на VPS, найди ошибку, исправь её, запусти тесты и перезапусти сервис.
```

## Безопасность

Сервис по умолчанию работает от отдельного пользователя `vpscontrol`, не от `root`. Файловые инструменты не выходят за `VPS_CONTROL_ROOT`.

`run_command` намеренно мощный: команда выполняется с системными правами пользователя `vpscontrol`. ChatGPT также показывает подтверждение для write-действий в Developer mode.
