# DB-InterBase-PostgreSQL (gdb2pg)

Универсальный оффлайн-конвертор InterBase GDB → PostgreSQL.
Читает `.gdb`/`.ib` файл напрямую (mmap, строго read-only), без
InterBase-сервера и лицензий, разбирает on-disk structure (ODS) и переносит
схему и данные в PostgreSQL.

ТЗ: [`SPEC.md`](SPEC.md) (каноническая копия: `../db/GDB2PG_SPEC.md`).

## Статус

- M1: header, страницы и каталог откалиброваны на ASUSS.GDB ODS 15.
- M2: колонки и строки пользовательских таблиц декодируются.
- M3 (в работе): реализованы conversion plan, staging DDL, COPY батчами,
  фильтрация back-версий, локальный manifest/resume и отчёт. До завершения нужны live-проверка на
  PostgreSQL и полном ASUSS.GDB, PostgreSQL-manifest и обработка BLOB.

Результат live-прогона ASUSS.GDB: [`docs/ASUSS_M3_VALIDATION.md`](docs/ASUSS_M3_VALIDATION.md).

## Быстрый старт

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest

# осмотр файла: header, ODS-версия, флаги, перепись типов страниц
gdb2pg inspect /path/to/copy.gdb

# hex-дамп распакованных записей таблицы (калибровка форматов)
gdb2pg probe /path/to/copy.gdb --pointer-page N --limit 5

# схема (после калибровки каталога)
gdb2pg schema /path/to/copy.gdb --report out/schema.md

# полный перенос (M3+)
gdb2pg convert /path/to/copy.gdb \
  --dsn postgresql://... \
  --schema legacy_asuss \
  --manifest out/legacy_asuss.manifest.json \
  --report out/legacy_asuss.md

# безопасная проверка плана и DDL без подключения к PostgreSQL
gdb2pg convert /path/to/copy.gdb \
  --schema legacy_asuss \
  --dry-run \
  --ddl-out out/legacy_asuss.sql
```

## Принципы

- Файл-источник открывается read-only; кода записи в GDB в пакете нет.
- Работать только с копией; оригиналы неизменны.
- Все «магические» смещения ODS собраны в `src/gdb2pg/ods.py` и
  `src/gdb2pg/sysformats.py` — единственные места калибровки под ODS 15.

## Структура

```
src/gdb2pg/
  pager.py       # mmap-доступ к страницам
  ods.py         # структуры и константы ODS (header, PIP/TIP, pointer, data)
  rle.py         # RLE-распаковка записей
  records.py     # обход data pages, версии записей, сборка фрагментов
  sysformats.py  # захардкоженные форматы системных таблиц (калибровка)
  catalog.py     # чтение RDB$-каталога -> модель схемы
  types.py       # декодирование значений, маппинг типов в PostgreSQL
  ddl.py         # генерация DDL
  writer.py      # COPY в PostgreSQL (psycopg3)
  manifest.py    # manifest / resume
  cli.py         # команды: inspect, probe, schema, convert
tests/           # unit-тесты на синтетических данных
```
