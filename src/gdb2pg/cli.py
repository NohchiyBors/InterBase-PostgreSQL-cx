"""CLI: gdb2pg inspect | probe | schema | convert."""

from __future__ import annotations

import argparse
import sys

from . import __version__, ods
from .pager import Pager


def cmd_inspect(args: argparse.Namespace) -> int:
    with Pager(args.gdb) as p:
        h = p.header
        print(f"file            : {args.gdb}")
        print(f"file size       : {p.file_size:,} bytes")
        print(f"page size       : {p.page_size}")
        print(f"pages           : {p.page_count:,}")
        print(f"ODS             : {h.ods}")
        print(f"next transaction: {h.next_transaction}")
        print(f"oldest active   : {h.oldest_active}")
        print(f"hdr flags       : 0x{h.flags:04x}")
        print(f"implementation  : {h.implementation}")
        print(f"RDB$PAGES page  : {h.pages_page}")
        if h.clumplets:
            print("clumplets:")
            for ctype, data in h.clumplets:
                name = ods.CLUMPLET_NAMES.get(ctype, f"type_{ctype}")
                shown = data[:40].hex()
                print(f"  {ctype:3d} {name:20s} len={len(data):3d} {shown}")
                if ctype == ods.HDR_FILE:
                    print("  !! multi-file database — вне scope v1", file=sys.stderr)
        print("page census:")
        for ptype, count in sorted(p.census().items()):
            name = ods.PAGE_TYPE_NAMES.get(ptype, f"unknown_{ptype}")
            print(f"  {ptype:3d} {name:22s} {count:>10,}")
        if h.next_transaction and h.oldest_active < h.next_transaction:
            active = h.next_transaction - h.oldest_active
            print(f"note: oldest_active отстаёт от next_transaction на {active}; "
                  "проверьте, что база корректно остановлена")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Дамп распакованных записей для калибровки форматов."""
    from . import records

    with Pager(args.gdb) as p:
        if args.pointer_page:
            first = args.pointer_page
        elif args.relation_id is not None:
            rel_map = records.find_pointer_pages(p)
            if args.relation_id not in rel_map:
                print(f"relation_id {args.relation_id} не найден; "
                      f"есть: {sorted(rel_map)}", file=sys.stderr)
                return 4
            first = rel_map[args.relation_id][0]
        else:
            first = p.header.pages_page  # RDB$PAGES
        st = records.WalkStats()
        shown = 0
        for rec in records.walk_relation(p, first, check_tx=not args.no_tx_check,
                                         stats=st):
            print(f"-- page={rec.page} slot={rec.slot} tx={rec.header.transaction} "
                  f"fmt={rec.header.format} flags=0x{rec.header.flags:04x} "
                  f"len={len(rec.data)}")
            if args.hex:
                data = rec.data[: args.width]
                for off in range(0, len(data), 16):
                    chunk = data[off: off + 16]
                    hx = " ".join(f"{b:02x}" for b in chunk)
                    asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                    print(f"   {off:04x}  {hx:<47s}  {asc}")
            shown += 1
            if shown >= args.limit:
                break
        print(f"-- stats: {st}")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from . import catalog

    with Pager(args.gdb) as p:
        schema = catalog.build_schema(p, deep=args.deep)
        lines = [f"# Schema report: {args.gdb}", "",
                 f"- ODS: {schema.ods}", f"- page size: {schema.page_size}", ""]
        for w in schema.warnings:
            lines.append(f"> WARNING: {w}")
        if schema.warnings:
            lines.append("")
        lines.append("| relation_id | name | pointer pages | system |")
        lines.append("|---:|---|---:|---|")
        for rel_id, t in sorted(schema.tables.items()):
            lines.append(f"| {rel_id} | {t.name} | {len(t.pointer_pages)} "
                         f"| {'yes' if t.is_system else ''} |")
        text = "\n".join(lines) + "\n"
        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"report -> {args.report}")
        else:
            print(text)
        return 2 if schema.warnings else 0


def cmd_convert(args: argparse.Namespace) -> int:
    print("convert: этап M3 — доступен после калибровки каталога (M1/M2). "
          "Сейчас используйте inspect/probe/schema.", file=sys.stderr)
    return 4


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gdb2pg",
                                 description="Offline InterBase GDB -> PostgreSQL")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_i = sub.add_parser("inspect", help="header, ODS, перепись страниц")
    p_i.add_argument("gdb")
    p_i.set_defaults(func=cmd_inspect)

    p_p = sub.add_parser("probe", help="hex-дамп записей для калибровки")
    p_p.add_argument("gdb")
    p_p.add_argument("--pointer-page", type=int, default=0)
    p_p.add_argument("--relation-id", type=int, default=None)
    p_p.add_argument("--limit", type=int, default=5)
    p_p.add_argument("--width", type=int, default=256)
    p_p.add_argument("--hex", action="store_true", default=True)
    p_p.add_argument("--no-tx-check", action="store_true")
    p_p.set_defaults(func=cmd_probe)

    p_s = sub.add_parser("schema", help="отчёт о схеме")
    p_s.add_argument("gdb")
    p_s.add_argument("--report")
    p_s.add_argument("--deep", action="store_true")
    p_s.set_defaults(func=cmd_schema)

    p_c = sub.add_parser("convert", help="перенос в PostgreSQL (M3)")
    p_c.add_argument("gdb")
    p_c.add_argument("--dsn")
    p_c.add_argument("--schema")
    p_c.set_defaults(func=cmd_convert)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
