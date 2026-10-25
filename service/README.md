# ghreport — сводка по открытым issue репозиториев GitHub (Go)

Только стандартная библиотека Go (`net/http`, `encoding/json`); клиентских SDK GitHub и сторонних модулей нет. Требуется Go ≥ 1.22.

## Запуск

```sh
go build -o ghreport .
GITHUB_TOKEN=... ./ghreport --repos cli/cli,cli/go-gh --limit 20 --since 2026-01-01 --out report.json --trace trace.jsonl

docker build -t ghreport service/
docker run --rm -e GITHUB_TOKEN=... ghreport --repos cli/cli --limit 20 --out -
docker run --rm -e GITHUB_API_BASE_URL=http://host.docker.internal:8080 ghreport --repos stub/alpha --out -
```

Параметры: `--repos` (обязателен, `owner/repo[,...]`, дубликаты удаляются с сохранением первого), `--limit` (по умолчанию 100, ≥ 1), `--since` (`YYYY-MM-DD`, 00:00:00 UTC), `--out` (по умолчанию `-`), `--trace`, `--cache-dir` (по умолчанию `./.ghreport-cache`).

Переменные окружения: `GITHUB_TOKEN` (необязательна), `GITHUB_API_BASE_URL` (по умолчанию `https://api.github.com`, завершающий `/` допускается). Они читаются при каждом запуске и нигде не сохраняются; в образ не вшиваются.

Коды возврата: 0 — все репозитории обработаны; 1 — часть завершилась ошибкой (поле `error`); 2 — некорректные аргументы (сообщение в stderr, stdout пуст); 3 — фатально: ответ 401 → `{"error": "unauthorized"}`, либо все репозитории с ошибками → `{"error": "all_failed"}` (в stdout). Дополнительно: если отчёт не удалось записать — stdout `{"error": "output_failed"}`, код 3.

## Архитектура

| Файл | Содержание |
|---|---|
| `main.go` | `run(args, getenv, stdout, stderr)`: обход репозиториев, коды возврата, запись отчёта |
| `config.go` | разбор и валидация аргументов (сообщения на русском в stderr) |
| `client.go` | HTTP: заголовки, ручные перенаправления, повторы, лимиты, `ETag`, `Link` |
| `report.go` | типы отчёта, разбор элементов, фильтры, дедупликация, сортировка |
| `cache.go`, `trace.go` | дисковый кэш и журнал JSON Lines |

Запросы строго последовательные. Первый адрес: `GET {base}/repos/{owner}/{repo}/issues?state=open&sort=created&direction=asc&per_page=100`; следующие страницы берутся только из `Link: rel="next"` (относительные адреса разрешаются относительно текущего). Заголовки: `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2026-03-10`, `User-Agent: ghreport/1.0 (+https://git.miem.hse.ru/; GitHub open issues summary tool)`, `Authorization: Bearer …` (только если задан токен и хост запроса совпадает с хостом `GITHUB_API_BASE_URL`, поэтому токен не уходит на чужие хосты при редиректах и в `Link`). Таймаут запроса 30 с.

Порядок обработки элемента страницы: pull request (ключ `pull_request` присутствует) → пропуск; `created_at < --since` → пропуск; номер уже встречался → пропуск; иначе добавить, и если набрано `--limit` — остановиться, следующие страницы не запрашиваются. В конце отчёт сортируется по `created_at` (как момент времени), затем по `number`. `--limit` считается от подходящих элементов (PR, отфильтрованные по дате и дубликаты в счёт не идут).

### Перенаправления
301/302/307/308 обрабатываются вручную (`Location`, до 5 переходов); каждый переход — отдельная запись в журнале с тем же номером попытки.

### Повторы и лимиты
Максимум 4 попытки на запрос. Сетевые ошибки и 5xx: паузы 1, 2, 4 с. Исчерпание лимита: 429, либо 403 с `Retry-After` / `X-RateLimit-Remaining: 0` / текстом `rate limit` в теле. Пауза: `Retry-After` (секунды или HTTP-дата) → иначе до `X-RateLimit-Reset` (epoch, UTC) → иначе 60 с (верхняя граница паузы — 24 ч). Не исчерпав попытки, служба повторяет запрос; после исчерпания репозиторий получает `rate_limited`/`request_failed`, обработка идёт дальше. 401 — фатально сразу. 404 → `not_found`. Прочие 4xx (в т. ч. «простой» 403, 410) → `request_failed` без повторов. Ответ 200 с некорректным JSON/не массивом/элементом без `number` или `created_at` → `invalid_response` (не повторяется, в кэш не пишется). При ошибке на любой странице у репозитория `count = 0` и `issues = []`.

### Кэш
Каталог `--cache-dir` (создаётся автоматически; если не получилось — предупреждение и работа без кэша). Один файл на адрес запроса: `sha256(url).json`:

```json
{"url": "…", "etag": "\"abc\"", "link": ["<…>; rel=\"next\""], "body": [ … ]}
```

Хранятся `ETag`, заголовки `Link` (чтобы после `304`, где `Link` может отсутствовать, продолжить постраничную выдачу) и тело успешного ответа. Заголовки запроса (в том числе токен) не сохраняются. Запись атомарная (временный файл + rename), повреждённый файл считается промахом. `If-None-Match` отправляется только при наличии записи; `304` без записи — `invalid_response`. В журнале для `304` из кэша `from_cache: true`.

### Журнал `--trace`
JSON Lines, одна строка на HTTP-попытку: `ts, method, url, status (0 без ответа), attempt, from_cache, error` (`null`, `timeout` или `network`). Запись сразу на диск. Заголовок `Authorization` не пишется. Токен не попадает в stdout, stderr, журнал и кэш.

## Тесты
`go test ./...`, `gofmt -l .`, `go vet ./...` (юнит-тесты на `httptest`: разбор аргументов, `Link`, пагинация/лимит/дубликаты/PR, `--since`, условные запросы и `from_cache`, лимиты, backoff 1/2/4 с через подменяемые `sleep`/`now`, ошибки, редиректы, утечки токена).

## Пример вывода

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "repos": [
    {"repo": "cli/cli", "error": null, "count": 1, "issues": [
      {"number": 1, "title": "Первый выпуск", "author": "octocat", "labels": ["bug"],
       "created_at": "2026-01-05T10:00:00Z", "url": "https://github.com/cli/cli/issues/1"}]},
    {"repo": "example/missing", "error": "not_found", "count": 0, "issues": []}
  ]
}
```
(в реальном выводе отчёт форматируется с отступами).
