# VPS Control — Living Project

VPS Control делает проект на VPS «живым» для ChatGPT: код заранее индексируется на сервере, а ChatGPT получает не отдельные файлы по одному, а готовый рабочий контекст задачи.

## Как работает

`openProject(task)` одним вызовом возвращает:

- обязательную инженерную конституцию;
- карту проекта;
- Git-состояние;
- код, наиболее связанный с задачей;
- хэши выбранных файлов для проверки свежести.

После записи, patch или shell-команды индекс автоматически помечается устаревшим и обновляется перед следующим чтением.

Конституция встроена в сервер и требует: чистую архитектуру, удаление старой реализации при замене, отсутствие секретов/костылей/дублирования, тесты, поиск остатков старого решения и финальный review Git diff.

## Два входа

### ChatGPT Plus — GPT Actions

Это вариант без платного OpenAI API на VPS. Custom GPT вызывает наш собственный HTTPS API на VPS. Нужен только секрет доступа к VPS Control — это НЕ OpenAI API key и за него нет платы.

Локальный Action endpoint после установки:

```text
http://127.0.0.1:8766
```

OpenAPI schema:

```text
https://YOUR-PUBLIC-HTTPS-HOST/openapi.json
```

В GPT Builder добавьте Action по этой схеме, а в Authentication выберите API key / custom header:

```text
X-VPS-Control-Key
```

Инструкции для Custom GPT лежат в `GPT_INSTRUCTIONS.md`.

### Business / Enterprise / Edu — MCP

Полный MCP endpoint:

```text
http://127.0.0.1:8765/mcp
```

Его можно подключить через поддерживаемый безопасный туннель.

## Установка WQPU одной командой

Пока изменения находятся в ветке:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/agent/vps-control-mcp/install-vps-control.sh | sudo bash -s -- agent/vps-control-mcp
```

По умолчанию WQPU будет жить постоянно в:

```text
/srv/wqpu
```

Чтобы подключить другой уже существующий Git-проект:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/agent/vps-control-mcp/install-vps-control.sh | sudo VPS_CONTROL_ROOT=/path/to/project bash -s -- agent/vps-control-mcp
```

Установщик выводит секрет `VPS_CONTROL_ACTION_TOKEN`. Храните его приватно; копия лежит в `/etc/vps-control.env` с правами root-only.

## Главные инструменты

- `openProject` / `open_project` — главное начало любой задачи;
- `applyProjectPatch` / `apply_patch` — точные изменения кода;
- `writeProjectFile` / `write_file` — полная замена файла;
- `runProjectCommand` / `run_command` — тесты, build, lint, Git и проектные команды;
- `getGitDiff` / `git_diff` — обязательная финальная проверка;
- targeted search/read — только как запасной вариант.

## Безопасность

Сервис не запускается от root по умолчанию. Файловые операции не выходят за `VPS_CONTROL_ROOT`. Action API требует секретный заголовок. Сервисы слушают только localhost; наружу их нужно публиковать через защищённый HTTPS-туннель/reverse proxy.
