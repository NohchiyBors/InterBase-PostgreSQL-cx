# ASUSS.GDB: live-валидация M3

Дата: `2026-07-14`

## Окружение

- Код: `d5f0be5` (`codex/m3-convert`).
- Хост: Linux, Python 3.12.3, Docker 29.1.3.
- Исходник: отдельная read-only копия `ASUSS.GDB`, `508715008` байт,
  ODS 15.0.
- PostgreSQL: отдельный `postgres:16-alpine`, контейнер
  `gdb2pg-cx-postgres`, loopback `127.0.0.1:55432`, отдельный volume.
- Целевая схема: `legacy_asuss_cx_v2`.

Исходный GDB не изменялся. Все артефакты прогона хранятся в
`~/InterBase-PostgreSQL-cx/validation/out/` на validation-хосте.

## Проверки

- Linux pytest: `31 passed`.
- Ruff: `All checks passed`.
- Dry-run: `465` таблиц выбрано, `45` пропущено, DDL `833586` байт,
  `0.8` секунды.
- Полный COPY-прогон: `90` секунд, exit code `2` (завершён с warnings).
- Manifest: status `done`, `465/465` таблиц в status `done`.
- Manifest/PostgreSQL: `1152301` / `1152301` строк.
- Сверка каждой таблицы: `0` расхождений.
- PostgreSQL: `465` таблиц, `0` staging-таблиц, `0` лишних таблиц.
- Многобайтный текст (кириллица) подтверждён в загруженных `varchar`.

## Найденная ошибка

Первый прогон завершил `462` таблицы и оставил `3` в status `failed` из-за NUL в
текстовых полях. Все проблемные записи имели `RHD_CHAIN`: конвертор ошибочно
выдавал back-версии как primary records.

Исправление `d5f0be5` фильтрует `RHD_CHAIN` до transaction-check и декодирования.
Повторный прогон пропустил `943938` back-версий, получил `0` decode errors и
завершил все `465` таблиц.

## Остаточные warnings

- `7` bad-page ссылок: `F38` (`3`) и `F38_DUBL` (`4`).
- `3811` BLOB значений записаны как NULL со счётчиком; `3402` из них в
  `CH_NACLAD_VAG`.
- PostgreSQL-manifest ещё не реализован; источником состояния остаётся локальный JSON.
- `gstat -r` XE7 прочитал header и подтвердил ODS 15.0, но relation-level статистика
  недоступна без работающего XE7-сервера на `localhost:3050`.

## Артефакты

- `validation/out/asuss-v2-manifest.json`
- `validation/out/asuss-v2-report.md`
- `validation/out/asuss-v2-staging.sql`
- `validation/out/asuss-v2-convert.log`
- `validation/out/asuss-gstat-r.txt`
