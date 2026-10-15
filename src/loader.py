#!/usr/bin/env python3
"""Deterministic local CSV to SQL Server bronze loader; no AI runtime calls."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyodbc
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets"
DB_NAME = "ai_medallion_wh"
DB_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
META = {"_source_path", "_load_timestamp_utc", "_load_id"}
RESERVED = set("add all alter and any as asc authorization backup begin between break browse bulk by cascade case check checkpoint close clustered coalesce collate column commit compute constraint contains continue convert create cross current current_date current_time current_timestamp current_user cursor database dbcc deallocate declare default delete deny desc disk distinct distributed drop dump else end errlvl escape except exec execute exists exit external fetch file fillfactor for foreign freetext from full function goto grant group having holdlock identity identity_insert identitycol if index inner insert intersect into is join key kill left like lineno load merge national nocheck nonclustered not null nullif of off offsets on open opendatasource openquery openrowset openxml option or order outer over percent pivot plan precision primary print proc procedure public raiserror read readtext reconfigure references replication restore restrict return revoke right rollback rowcount rowguidcol rule save schema select session_user set setuser shutdown some statistics system_user table tablesample textsize then to top tran transaction trigger truncate try_convert tsequal union unique unpivot update updatetext use user values varying view waitfor when where while with writetext".split())
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")


def qi(value: str) -> str:
    return "[" + value.replace("]", "]]" ) + "]"


def valid_db() -> str:
    if not DB_RE.fullmatch(DB_NAME):
        raise RuntimeError("Derived database name failed validation")
    return DB_NAME


def safe_piece(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    if not value or not value[0].isalpha():
        value = "t_" + value
    return value.lower()


def table_for(path: Path) -> str:
    rel = path.relative_to(DATA).with_suffix("")
    return "_".join(safe_piece(p) for p in rel.parts)


def decode_csv(path: Path):
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("unsupported encoding (tried UTF-8 with/without BOM and Windows-1252)")


def mapped_headers(headers: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    used: set[str] = set()
    output, changes = [], []
    for i, original in enumerate(headers, 1):
        candidate = original
        valid = bool(candidate) and not candidate.isspace() and len(candidate) <= 128
        valid = valid and not candidate.startswith(" ") and not candidate.endswith(" ")
        valid = valid and not any(ord(c) < 32 for c in candidate)
        valid = valid and candidate.casefold() not in RESERVED and candidate.casefold() not in META
        key = candidate.casefold()
        if not valid or key in used:
            base = safe_piece(candidate) if candidate.strip() else "column"
            candidate = f"{base}_{i}"
            while candidate.casefold() in used or candidate.casefold() in META:
                candidate += "_x"
            changes.append((original, candidate))
        used.add(candidate.casefold())
        output.append(candidate)
    return output, changes


@dataclass
class Source:
    path: Path
    rel: str
    size: int
    digest: str
    encoding: str
    headers: list[str]
    mapped: list[str]
    changes: list[tuple[str, str]]
    rows: int
    table: str


def scan() -> list[Source]:
    if not DATA.exists():
        return []
    files = sorted((p for p in DATA.rglob("*") if p.is_file() and p.suffix.lower() == ".csv"), key=lambda p: p.as_posix().casefold())
    found = []
    for path in files:
        text, encoding = decode_csv(path)
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError(f"{path.relative_to(ROOT)}: empty file has no header")
        mapped, changes = mapped_headers(headers)
        if not headers or any(h == "" for h in headers):
            # Empty names are deterministically mapped by mapped_headers.
            pass
        rows = 0
        for line, row in enumerate(reader, 2):
            if len(row) != len(headers):
                raise ValueError(f"{path.relative_to(ROOT)}:{line}: expected {len(headers)} fields, got {len(row)}")
            rows += 1
        rel = path.relative_to(DATA).as_posix()
        found.append(Source(path, rel, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest(), encoding, headers, mapped, changes, rows, table_for(path)))
    names: dict[str, str] = {}
    errors = []
    for src in found:
        key = src.table.casefold()
        if key in names:
            errors.append(f"table name collision: {names[key]} and {src.rel} both map to bronze.{src.table}")
        names[key] = src.rel
        if len(src.table) > 128:
            errors.append(f"table name exceeds SQL Server's 128-character limit: {src.rel} -> {src.table}")
    if errors:
        raise ValueError("\n".join(errors))
    return found


def preflight(sources: list[Source]) -> None:
    print("CSV preflight (no SQL connection)")
    if not sources:
        print("No CSV files found under datasets/.")
    for s in sources:
        print(f"\n{s.rel} | {s.size:,} bytes | {s.rows:,} data rows | {s.encoding} | SHA-256 {s.digest}")
        print(f"  proposed table: bronze.{s.table}")
        print("  headers: " + ", ".join(repr(h) for h in s.headers))
        if s.changes:
            print("  header mappings: " + "; ".join(f"{a!r} -> {b!r}" for a, b in s.changes))
        else:
            print("  header mappings: unchanged")
        print("  conflicts: none")


def connection_string(database: str) -> str:
    host = os.getenv("MSSQL_HOST", "localhost")
    port = os.getenv("MSSQL_PORT", "1433")
    user = os.getenv("MSSQL_USER", "sa")
    password = os.getenv("MSSQL_PASSWORD", "")
    driver = os.getenv("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    if not password:
        raise RuntimeError("MSSQL_PASSWORD is not available")
    return f"DRIVER={{{driver}}};SERVER={host},{port};DATABASE={database};UID={user};PWD={password};Encrypt=no;TrustServerCertificate=yes;"


def connect(database: str):
    try:
        return pyodbc.connect(connection_string(database), autocommit=True)
    except Exception as exc:
        # Preserve driver diagnostics while guarding against accidental password echoes.
        msg = str(exc).replace(os.getenv("MSSQL_PASSWORD", "\0"), "[redacted]")
        raise RuntimeError(msg) from None


def prepare_database() -> None:
    db = valid_db()
    master = connect("master")
    try:
        version = master.cursor().execute("SELECT @@VERSION").fetchone()[0]
        print("Connected using configured ODBC driver; SQL Server version verified:")
        print(str(version).splitlines()[0])
        cur = master.cursor()
        cur.execute("SELECT DB_ID(?)", db)
        if cur.fetchone()[0] is not None:
            raise RuntimeError(f"Database conflict: [{db}] already exists. No database objects were changed.")
        cur.execute(f"CREATE DATABASE {qi(db)}")
    finally:
        master.close()
    conn = connect(db)
    try:
        cur = conn.cursor()
        for schema in ("bronze", "silver", "gold"):
            cur.execute(f"IF SCHEMA_ID(?) IS NULL EXEC(N'CREATE SCHEMA {qi(schema)}')", schema)
        cur.execute("""IF OBJECT_ID(N'bronze._file_loads', N'U') IS NULL
        CREATE TABLE [bronze].[_file_loads](
          [load_id] uniqueidentifier NOT NULL PRIMARY KEY,
          [source_path] nvarchar(1024) NOT NULL, [sha256] char(64) NOT NULL,
          [table_name] sysname NOT NULL, [status] varchar(20) NOT NULL,
          [source_row_count] bigint NULL, [inserted_row_count] bigint NULL,
          [validated_row_count] bigint NULL, [started_at_utc] datetime2(3) NOT NULL,
          [completed_at_utc] datetime2(3) NULL, [error_message] nvarchar(4000) NULL)""")
    finally:
        conn.close()


def ensure_objects() -> None:
    db = valid_db()
    master = connect("master")
    try:
        version = master.cursor().execute("SELECT @@VERSION").fetchone()[0]
        print("Connected using configured ODBC driver; SQL Server version verified:")
        print(str(version).splitlines()[0])
        master.cursor().execute("SELECT DB_ID(?)", db)
        exists = master.cursor().fetchone()[0]
        if exists is None:
            master.cursor().execute(f"CREATE DATABASE {qi(db)}")
    finally:
        master.close()
    conn = connect(db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT OBJECT_ID(N'bronze._file_loads', N'U')")
        if exists is not None and cur.fetchone()[0] is None:
            raise RuntimeError(f"Database conflict: [{db}] exists but is not initialized for this project. No objects were changed.")
        for schema in ("bronze", "silver", "gold"):
            cur.execute(f"IF SCHEMA_ID(?) IS NULL EXEC(N'CREATE SCHEMA {qi(schema)}')", schema)
        cur.execute("""IF OBJECT_ID(N'bronze._file_loads', N'U') IS NULL CREATE TABLE [bronze].[_file_loads](
          [load_id] uniqueidentifier NOT NULL PRIMARY KEY, [source_path] nvarchar(1024) NOT NULL,
          [sha256] char(64) NOT NULL, [table_name] sysname NOT NULL, [status] varchar(20) NOT NULL,
          [source_row_count] bigint NULL, [inserted_row_count] bigint NULL, [validated_row_count] bigint NULL,
          [started_at_utc] datetime2(3) NOT NULL, [completed_at_utc] datetime2(3) NULL, [error_message] nvarchar(4000) NULL)""")
    finally:
        conn.close()


def load_one(src: Source) -> str:
    conn = connect(DB_NAME)
    load_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    table = qi(src.table)
    cols = [qi(h) for h in src.mapped] + ["[_source_path]", "[_load_timestamp_utc]", "[_load_id]"]
    try:
        cur = conn.cursor()
        cur.execute("SELECT TOP (1) 1 FROM [bronze].[_file_loads] WHERE [source_path]=? AND [sha256]=? AND [status]='succeeded'", src.rel, src.digest)
        if cur.fetchone():
            return "skipped unchanged (previous successful load)"
        conn.autocommit = False
        cur.execute("SELECT OBJECT_ID(?)", f"bronze.{src.table}")
        table_id = cur.fetchone()[0]
        expected = [*src.mapped, "_source_path", "_load_timestamp_utc", "_load_id"]
        if table_id is None:
            cur.execute(f"CREATE TABLE [bronze].{table} (" + ", ".join(f"{c} nvarchar(max) NULL" for c in cols[:-2]) + ", [_load_timestamp_utc] datetime2(3) NOT NULL, [_load_id] uniqueidentifier NOT NULL)")
        else:
            cur.execute("SELECT c.name FROM sys.columns c WHERE c.object_id=? ORDER BY c.column_id", table_id)
            actual = [row[0] for row in cur.fetchall()]
            if [x.casefold() for x in actual] != [x.casefold() for x in expected]:
                raise RuntimeError(f"Table conflict: bronze.{src.table} has a different column layout; refusing to alter it")
        with src.path.open("r", encoding=src.encoding, newline="") as stream:
            reader = csv.reader(stream, strict=True)
            next(reader)
            placeholders = ",".join("?" for _ in cols)
            sql = f"INSERT INTO [bronze].{table} ({','.join(cols)}) VALUES ({placeholders})"
            inserted = 0
            stamp = datetime.now(timezone.utc).replace(tzinfo=None)
            batch = []
            for line, row in enumerate(reader, 2):
                if len(row) != len(src.headers):
                    raise ValueError(f"line {line}: expected {len(src.headers)} fields, got {len(row)}")
                batch.append(tuple(row) + (src.rel, stamp, load_id))
                if len(batch) >= 500:
                    cur.fast_executemany = True
                    cur.executemany(sql, batch)
                    inserted += len(batch); batch.clear()
            if batch:
                cur.fast_executemany = True
                cur.executemany(sql, batch); inserted += len(batch)
        cur.execute(f"SELECT COUNT_BIG(*) FROM [bronze].{table} WHERE [_load_id]=?", load_id)
        validated = int(cur.fetchone()[0])
        if inserted != src.rows or validated != src.rows:
            raise RuntimeError(f"row-count mismatch: source={src.rows}, inserted={inserted}, validated={validated}")
        cur.execute("INSERT INTO [bronze].[_file_loads] VALUES (?,?,?,?,?,?,?,?,?,?,?)", load_id, src.rel, src.digest, src.table, "succeeded", src.rows, inserted, validated, started, datetime.now(timezone.utc).replace(tzinfo=None), None)
        conn.commit(); conn.autocommit = True
        return f"loaded and validated {validated:,} rows"
    except Exception as exc:
        try:
            conn.rollback(); conn.autocommit = True
            cur = conn.cursor()
            # A newly created table may persist outside SQL Server's DDL transaction; never drop it.
            cur.execute("INSERT INTO [bronze].[_file_loads] VALUES (?,?,?,?,?,?,?,?,?,?,?)", load_id, src.rel, src.digest, src.table, "failed", src.rows, None, None, started, datetime.now(timezone.utc).replace(tzinfo=None), str(exc)[:4000])
        except Exception:
            pass
        raise
    finally:
        conn.close()


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--setup-only", action="store_true")
    args = ap.parse_args()
    try:
        valid_db()
        sources = scan()
        if args.preflight:
            preflight(sources)
            return 0
        if args.setup_only:
            prepare_database()
            print(f"Database [{DB_NAME}], bronze/silver/gold schemas, and bronze._file_loads are ready.")
            return 0
        preflight(sources)
        # Version/connection check precedes any database creation or loading.
        ensure_objects()
        print("\nPer-file load results:")
        errors = 0
        for src in sources:
            try:
                print(f"{src.rel}: {load_one(src)}")
            except Exception as exc:
                errors += 1
                print(f"{src.rel}: FAILED: {exc}", file=sys.stderr)
        print(f"Completed with {errors} file error(s).")
        return 1 if errors else 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
