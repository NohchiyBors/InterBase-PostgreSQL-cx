# ASUSS.GDB: live-валидация M3/M4

Дата: `2026-07-14`

## Окружение

- Ветка: `codex/m3-convert`.
- Хост: Linux, Python 3.12.3, Docker 29.1.3.
- Исходник: отдельная read-only копия `ASUSS.GDB`, `508715008` байт,
  mode `0440`, ODS 15.0.
- PostgreSQL: отдельный `postgres:16-alpine`, контейнер
  `gdb2pg-cx-postgres`, loopback `127.0.0.1:55432`, отдельный volume.
- Целевая схема последнего прогона: `legacy_asuss_cx_v3`.

Исходный GDB не изменялся. Все артефакты прогона хранятся в
`~/InterBase-PostgreSQL-cx/validation/out/` на validation-хосте.

## Проверки

- Windows/Linux pytest: `36 passed`.
- Ruff: `All checks passed`.
- Полный COPY-прогон: `90` секунд, exit code `2` только из-за известных
  bad-page ссылок.
- Manifest: status `done`, `465/465` таблиц в status `done`.
- Manifest/PostgreSQL: `1152301` / `1152301` строк.
- Сверка `COUNT(*)` каждой таблицы: `0` расхождений.
- PostgreSQL: `465` data-таблиц, `0` staging-таблиц.
- PostgreSQL manifest: `465` строк, все `table_status=done`, агрегаты совпадают
  с локальным JSON.
- Пропущено `943938` физических `RHD_CHAIN` back-версий.
- Decode errors: `0`.
- Многобайтный текст (кириллица) подтверждён в загруженных `varchar`.

## BLOB

InterBase ODS 15 использует отдельные BLOB pointer/data pages типов `11/12` и
table-specific `RDB$BLOB_BLOCKING_FACTOR`. Раскладка `blh`, адресация и
сегменты откалиброваны на read-only копии ASUSS.

- Прочитано `3811/3811` BLOB-ссылок пользовательских таблиц, ошибок `0`.
- PostgreSQL содержит `3811` ненулевых BLOB-значений общим объёмом
  `50974` байта.
- `3379` значений являются корректными пустыми BLOB, а не `NULL`.
- Проверены inline segmented/stream BLOB и код level 1/2 page chains.
- В схеме ASUSS задействовано `44` BLOB-колонки в `11` таблицах; все имеют
  subtype `0` и перенесены в `bytea`.

## Bad pages

Счётчик `7` относится к семи уникальным повреждённым ссылкам:

- `F38`: `62110`, `62114`, `62115`.
- `F38_DUBL`: `62099`, `62100`, `62103`, `62113`.

В файле `62099` страниц, допустимый диапазон `0..62098`. Все семь значений
являются pointer entries за EOF; существующие страницы при этом не теряются.

Номера сохранены и в локальном JSON, и в
`legacy_asuss_cx_v3.gdb2pg_manifest.bad_page_numbers`.

## PostgreSQL manifest

`<schema>.gdb2pg_manifest` создаётся конвертором и обновляется после каждой
таблицы. Он хранит source/target names, статусы запуска и таблицы, row counts,
bad-page номера, back-version/decode/BLOB счётчики и ошибку. DSN и пароль в
manifest не записываются.

## Ограничение независимой сверки

`gstat -r` XE7 прочитал header и подтвердил ODS 15.0, но relation-level
статистика недоступна без работающего лицензированного XE7-сервера на
`localhost:3050`. Это не влияет на офлайн-чтение и PostgreSQL-сверку, но
остаётся внешним ограничением независимой проверки.

## Артефакты

- `validation/out/asuss-v3-manifest.json`
- `validation/out/asuss-v3-report.md`
- `validation/out/asuss-v3-staging.sql`
- `validation/out/asuss-v3-convert.log`
- `validation/out/asuss-gstat-r.txt`
