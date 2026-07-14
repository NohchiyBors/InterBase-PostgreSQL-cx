# ТЗ: gdb2pg — универсальный оффлайн-конвертор InterBase GDB → PostgreSQL

**Дата:** 2026-07-14 · **Статус:** draft · **Версия:** 0.1

## 1. Назначение

Утилита `gdb2pg` читает файл базы данных InterBase (`.gdb` / `.ib`) напрямую,
без InterBase-сервера и без лицензий, разбирает его on-disk structure (ODS)
и переносит схему и данные в PostgreSQL прямыми INSERT/COPY.

Мотивация: пять исходных GDB проекта имеют ODS 15.0 (InterBase XE),
InterBase 15 их не открывает, а запуск XE7/IB2020 упирается в регистрацию
(см. `db/COMPATIBILITY_AUDIT.md`). Оффлайн-парсер снимает лицензионную
зависимость полностью и остаётся переиспользуемым инструментом.

## 2. Исходные данные (первичная цель)

| Файл | Размер | ODS |
|---|---:|---|
| `ASUSS.GDB` | 485 MB | 15.0 |
| `arhiv.gdb` | 2.5 GB | 15.0 |
| `ARHIV38.GDB` | 0.8 GB | 15.0 |
| `ARHIV39.GDB` | 2.4 GB | 15.0 |
| `ARHIV40.GDB` | 3.1 GB | 15.0 |

Файлы лежат на `10.10.10.50` в `~/mns-analysis/gdb/`. Ожидаемая кодировка
текстовых полей — `WIN1251` (кириллица), подтвердить по `RDB$CHARACTER_SETS`.

## 3. Область применения

- Универсальный инструмент: вход — любой одиночный GDB-файл, выход —
  указанная схема PostgreSQL. Проектных знаний о вагонах внутри нет.
- Первичная цель — ODS 15.0; архитектура закладывается на диапазон ODS 10–15
  (структуры ODS 10 документированы открытым исходником InterBase 6.0).
- Конвертируются все пользовательские таблицы. Системные (`RDB$*`, `TMP$*`)
  не переносятся, но каталог выгружается в отчёт о схеме.

## 4. Жёсткие ограничения

1. **Файл-источник открывается строго read-only** (`O_RDONLY` + mmap).
   Утилита физически не имеет кода записи в GDB.
2. Работа ведётся только с копией GDB; оригиналы на Storage Box неизменны.
3. База должна быть в состоянии после корректного shutdown
   (нет активных транзакций). Утилита проверяет TIP и предупреждает,
   если есть limbo/active транзакции.
4. Однофайловые базы. Multi-file GDB — вне scope v1 (при обнаружении
   secondary files — ошибка с понятным сообщением).
5. Если в header выставлены флаги шифрования страниц (InterBase XE umeet
   database encryption) — остановка с диагностикой; расшифровка вне scope.

## 5. Архитектура

Python ≥ 3.11, зависимости: `psycopg[binary]` ≥ 3.1. Всё остальное — stdlib
(`mmap`, `struct`, `codecs`). Пакет живёт в `db/scripts/gdb2pg/`.

```
gdb2pg/
  pager.py      # доступ к страницам: page_size из header, mmap, кэш
  ods.py        # структуры страниц ODS: header, PIP, TIP, pointer,
                # data, index root/btree, blob, generator; констан��ы типов
  catalog.py    # чтение системного каталога: RDB$PAGES, RDB$RELATIONS,
                # RDB$RELATION_FIELDS, RDB$FIELDS, RDB$FORMATS,
                # RDB$CHARACTER_SETS, RDB$COLLATIONS
  records.py    # обход pointer pages -> data pages, распаковка записей:
                # RLE-декомпрессия, null bitmap, формат по RDB$FORMATS,
                # склейка фрагментов (rhdf), выбор актуальной версии записи
  blobs.py      # сборка сегментированных BLOB по blob id (level 0/1/2)
  types.py      # декодирование значений и маппинг типов в PostgreSQL
  ddl.py        # генерация DDL: CREATE SCHEMA/TABLE, PK/UNIQUE (опц.),
                # комментарии с исходными именами
  writer.py     # psycopg3 COPY (text) батчами, транзакция на таблицу
  manifest.py   # manifest.json: статус, счётчики, checkpoint per table
  cli.py        # CLI-входная точка
```

### 5.1 Порядок работы

1. `header`: прочитать page 0, проверить сигнатуру, ODS-версию, page_size,
   флаги (shutdown state, encryption). ODS вне 10–15 — ошибка.
2. `catalog`: bootstrap через известные page numbers системных таблиц
   (RDB$PAGES имеет фиксированный relation_id 0 и стартовую страницу,
   известную из header/ods.h), затем полное чтение каталога.
3. `schema`: построить модель таблиц/полей/форматов; сгенерировать DDL;
   в режиме `--schema-only` — остановиться, выдав отчёт.
4. `data`: для каждой таблицы обойти pointer pages → data pages,
   декодировать записи, стримить в PostgreSQL через COPY батчами
   (по умолчанию 10 000 строк), обновлять manifest.
5. `verify`: после каждой таблицы сравнить количество перенесённых строк
   с количеством прочитанных primary records; итоговый отчёт.

### 5.2 Версии записей и транзакции

- Для каждой записи берётся её primary version, если транзакция-создатель
  committed по TIP; delta/back versions игнорируются.
- Записи от rolled-back транзакций пропускаются с подсчётом в отчёте.
- Fragmented records (флаг incomplete) собираются по цепочке фрагментов.

## 6. Маппинг типов

| InterBase (dtype/subtype) | PostgreSQL | Примечания |
|---|---|---|
| SMALLINT | `smallint` | scale<0 → `numeric(p,s)` (scaled int) |
| INTEGER | `integer` | scale<0 → `numeric(p,s)` |
| INT64 / NUMERIC / DECIMAL | `bigint` / `numeric(p,s)` | по RDB$FIELD_SCALE |
| FLOAT | `real` | |
| DOUBLE PRECISION | `double precision` | |
| DATE | `date` | дни от 17.11.1858 (Modified JD) |
| TIME | `time` | тики 1/10000 с |
| TIMESTAMP | `timestamp` | без TZ; TZ данных фиксируется в отчёте |
| CHAR(n) / VARCHAR(n) | `varchar(n)` / `text` | перекодировка charset → UTF-8 |
| BLOB subtype 1 (text) | `text` | перекодировка по charset поля |
| BLOB subtype 0/прочие | `bytea` | as-is |
| BOOLEAN (IB7+) | `boolean` | |
| ARRAY | — | v1: пропуск поля + warning в отчёте |

Перекодировка: таблица соответствий InterBase charset → Python codec
(`WIN1251` → `cp1251`, `UNICODE_FSS` → `utf-8` с fallback, `NONE`/`OCTETS` →
`cp1251` по конфигу `--default-charset`). Некодируемые байты — `replace`
с подсчётом в отчёте.

## 7. Именование и размещение в PostgreSQL

- Каждый GDB → отдельная схема: `legacy_asuss`, `legacy_arhiv`,
  `legacy_arhiv38`, `legacy_arhiv39`, `legacy_arhiv40` (задаётся `--schema`).
- Имена таблиц/колонок: trim, lowercase, не-ASCII и спецсимволы → `_`,
  коллизии разрешаются суффиксом `_2`; исходное имя сохраняется в
  `COMMENT ON`.
- PK/UNIQUE переносятся по каталогу (`--constraints`, по умолчанию on),
  FK — опционально после загрузки данных (`--fk`), индексы не переносятся
  (генерируется файл `indexes.sql` для ручного применения).
- Триггеры, процедуры, generators, views: данные не переносятся; исходники
  (`RDB$VIEW_SOURCE`, метаданные generators) выгружаются в `schema-report`.

## 8. CLI-контракт

```bash
# только схема + отчёт, без записи в PostgreSQL
gdb2pg --gdb /work/asuss_copy.gdb --schema-only --report out/asuss-schema.md

# полный перенос
gdb2pg --gdb /work/asuss_copy.gdb \
       --dsn postgresql://user:pass@10.10.10.50:5432/mns_legacy \
       --schema legacy_asuss \
       --batch-size 10000 \
       --report out/asuss-report.md

# докачка после сбоя
gdb2pg ... --resume

# выборочно
gdb2pg ... --include 'OPER*,VAGON*' --exclude 'LOG*'
```

Коды возврата: `0` успех, `2` схема прочитана с warnings, `3` частичный
перенос (см. manifest), `4` фатально (шифрование, неподдерживаемый ODS,
повреждённый header).

## 9. Идемпотентность и устойчивость

- Manifest (`<schema>.gdb2pg_manifest` в PostgreSQL + локальный JSON):
  per-table статус, счётчики строк, последний checkpoint (номер pointer page).
- Загрузка таблицы идёт в `<table>__staging`, по завершении — атомарный
  `ALTER TABLE RENAME`. Повторный запуск без `--resume` пере-создаёт staging.
- Повреждённые data pages: пропуск страницы, подсчёт потерянных записей,
  список bad pages в отчёте; конвертация не прерывается (`--strict` — прерывается).

## 10. Валидация и приёмка

Критерии приёмки v1 (на копии `ASUSS.GDB`):

1. `--schema-only` выдаёт список таблиц/полей, совпадающий с выводом
   `gstat -a` XE7 (количество и имена таблиц).
2. Количество строк по каждой таблице совпадает с `gstat -r`
   (primary record versions) в пределах записей rolled-back транзакций.
3. Кириллица в текстовых полях читаема (ручная проверка выборки).
4. Контрольная сверка: когда станет доступен любой живой InterBase
   (IB2020/XE7), выборка `ORDER BY PK LIMIT 1000` из 3 таблиц побайтово
   сравнивается с результатом конвертора.
5. Прогон на всех пяти GDB завершается с кодом 0/2, суммарный отчёт
   сохраняется в `db/analysis/gdb2pg/`.

## 11. Нефункциональные требования

- Потоковая обработка: память O(кэш страниц + батч), без загрузки таблицы
  целиком; 3.1 GB `ARHIV40.GDB` обрабатывается на хосте с 4 GB RAM.
- Ориентир производительности: ≥ 20 MB/с чтения GDB на локальном диске.
- Логи: структурированные, уровень настраиваемый, прогресс per-table.
- Код покрыт unit-тестами на синтетических страницах (RLE, null bitmap,
  типы, фрагменты); интеграционный тест — мини-GDB фикстура.

## 12. Риски

| Риск | Митигация |
|---|---|
| Дельты ODS 11–15 против открытого ods.h IB6 (ODS 10) не документированы | M1/M2 — reverse-engineering на реальном ASUSS.GDB со сверкой по gstat; IBSurgeon-статья о физической структуре как вторичный источник |
| Включённое шифрование XE | ранняя проверка header, fail-fast |
| Некорректный shutdown источника (активные транзакции) | анализ TIP, предупреждение, счётчики пропущенных версий |
| BLOB level 2 (большие blob) редки и сложны | отдельный milestone, до него — подсчёт и список пропущенных |
| Сроки reverse-engineering непредсказуемы | fallback-путь через IB2020 (ODS 13–17 attach) остаётся в силе и не блокируется этой разработкой |

## 13. Этапы

| # | Milestone | Результат |
|---|---|---|
| M1 | Header + каталог | `--schema-only` на ASUSS.GDB: полный список таблиц/полей |
| M2 | Данные одной таблицы | выгрузка выбранной таблицы в CSV-debug, сверка с gstat -r |
| M3 | Полный перенос ASUSS.GDB | схема `legacy_asuss` в PostgreSQL, отчёт |
| M4 | Остальные 4 GDB | схемы `legacy_arhiv*`, суммарный отчёт |
| M5 | Валидация против живого InterBase | побайтовая сверка выборок, приёмка |

После M4 фильтрация по 495 вагонам делается обычным SQL внутри PostgreSQL
(`legacy_*` → filtered выборки → `raw_movements` → `movements`), существующий
контракт `db/DATA_MODEL.md` не меняется.

## 14. Ссылки

- Открытый исходник InterBase 6.0 / Firebird: `src/jrd/ods.h` — базовые
  структуры страниц и записей (ODS 10, отправная точка).
- [IBSurgeon: физическая структура БД InterBase/Firebird](https://ib-aid.com/ru/articles/database-physical-structure-interbase-and-firebird/)
- [InterBase 2020: On-disk Structure](https://docwiki.embarcadero.com/InterBase/2020/en/On-disk_Structure_(ODS))
- [InterBase 15: On-disk Structure](https://docwiki.embarcadero.com/InterBase/15/en/On-disk_Structure_(ODS))
- `db/COMPATIBILITY_AUDIT.md`, `db/DATA_MODEL.md`, `db/ANALYSIS_PLAN.md`
