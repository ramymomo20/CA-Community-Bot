from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import duckdb
import polars as pl

from analytics.duckdb.manifest import SNAPSHOT_SPECS, SnapshotSpec


ROOT_DIR = Path(__file__).resolve().parents[2]
SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DUCKDB_PATH = ROOT_DIR / ".deploy_stage" / "iosca_analytics.duckdb"


def _quote_ident(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _qualified_name(schema: str, table: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(table)}"


def _postgres_dsn() -> str:
    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("SUPABASE_POOLER_URL")
    if not dsn:
        raise RuntimeError("SUPABASE_DB_URL or SUPABASE_POOLER_URL must be set.")
    return dsn


def _duckdb_path() -> Path:
    configured = os.getenv("IOSCA_DUCKDB_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DUCKDB_PATH


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return value


def _rows_to_frame(rows: list[asyncpg.Record], columns: list[str]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame({column: [] for column in columns})
    return pl.DataFrame(
        [
            {column: _normalize_value(row[column]) for column in columns}
            for row in rows
        ],
        strict=False,
    )


def _table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    result = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        LIMIT 1
        """,
        [schema, table],
    ).fetchone()
    return bool(result)


def _ensure_bootstrap(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))


def _ensure_target_table(
    conn: duckdb.DuckDBPyConnection,
    spec: SnapshotSpec,
    temp_view_name: str,
) -> None:
    if _table_exists(conn, spec.target_schema, spec.target_table):
        return
    conn.execute(
        f"""
        CREATE TABLE {_qualified_name(spec.target_schema, spec.target_table)} AS
        SELECT *
        FROM {_quote_ident(temp_view_name)}
        WHERE 1 = 0
        """
    )


def _merge_into_target(
    conn: duckdb.DuckDBPyConnection,
    spec: SnapshotSpec,
    temp_view_name: str,
    columns: list[str],
) -> None:
    target_name = _qualified_name(spec.target_schema, spec.target_table)
    join_clause = " AND ".join(
        f"target.{_quote_ident(column)} = source.{_quote_ident(column)}"
        for column in spec.primary_keys
    )
    update_clause = ", ".join(
        f"{_quote_ident(column)} = source.{_quote_ident(column)}"
        for column in columns
        if column not in spec.primary_keys
    )
    insert_columns = ", ".join(_quote_ident(column) for column in columns)
    insert_values = ", ".join(f"source.{_quote_ident(column)}" for column in columns)

    if not update_clause:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {target_name}
            SELECT *
            FROM {_quote_ident(temp_view_name)}
            """
        )
        return

    conn.execute(
        f"""
        MERGE INTO {target_name} AS target
        USING {_quote_ident(temp_view_name)} AS source
        ON {join_clause}
        WHEN MATCHED THEN
            UPDATE SET {update_clause}
        WHEN NOT MATCHED THEN
            INSERT ({insert_columns})
            VALUES ({insert_values})
        """
    )


def _store_sync_state(
    conn: duckdb.DuckDBPyConnection,
    spec: SnapshotSpec,
    row_count: int,
    watermark_value: datetime | None,
    status: str,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO cache_meta.sync_state (
            source_name,
            target_schema,
            target_table,
            sync_mode,
            watermark_column,
            watermark_value,
            last_row_count,
            last_synced_at,
            status,
            last_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT (source_name) DO UPDATE SET
            target_schema = excluded.target_schema,
            target_table = excluded.target_table,
            sync_mode = excluded.sync_mode,
            watermark_column = excluded.watermark_column,
            watermark_value = excluded.watermark_value,
            last_row_count = excluded.last_row_count,
            last_synced_at = excluded.last_synced_at,
            status = excluded.status,
            last_error = excluded.last_error
        """,
        [
            spec.source_name,
            spec.target_schema,
            spec.target_table,
            spec.mode,
            spec.watermark_column,
            watermark_value,
            row_count,
            status,
            last_error,
        ],
    )


def _load_watermark(
    conn: duckdb.DuckDBPyConnection,
    spec: SnapshotSpec,
) -> datetime | None:
    row = conn.execute(
        """
        SELECT watermark_value
        FROM cache_meta.sync_state
        WHERE source_name = ?
        """,
        [spec.source_name],
    ).fetchone()
    return row[0] if row else None


async def _fetch_rows(
    pg_conn: asyncpg.Connection,
    spec: SnapshotSpec,
    watermark: datetime | None,
) -> tuple[list[str], list[asyncpg.Record]]:
    if spec.mode == "incremental" and spec.watermark_column and watermark is not None:
        sql = f"""
            SELECT *
            FROM ({spec.source_sql}) AS source
            WHERE source.{_quote_ident(spec.watermark_column)} > $1
            ORDER BY source.{_quote_ident(spec.watermark_column)}
        """
        statement = await pg_conn.prepare(sql)
        columns = [attribute.name for attribute in statement.get_attributes()]
        rows = await statement.fetch(watermark)
        return columns, rows

    statement = await pg_conn.prepare(spec.source_sql)
    columns = [attribute.name for attribute in statement.get_attributes()]
    rows = await statement.fetch()
    return columns, rows


async def main() -> None:
    duckdb_path = _duckdb_path()
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(duckdb_path))
    _ensure_bootstrap(conn)

    pg_conn = await asyncpg.connect(_postgres_dsn())
    try:
        for spec in SNAPSHOT_SPECS:
            watermark = _load_watermark(conn, spec)
            try:
                columns, rows = await _fetch_rows(pg_conn, spec, watermark)
                frame = _rows_to_frame(rows, columns)
                temp_view_name = f"tmp_{spec.target_schema}_{spec.target_table}"
                arrow_table = frame.to_arrow()
                try:
                    conn.unregister(temp_view_name)
                except Exception:
                    pass
                conn.register(temp_view_name, arrow_table)

                if spec.mode == "full":
                    conn.execute(
                        f"""
                        CREATE OR REPLACE TABLE {_qualified_name(spec.target_schema, spec.target_table)} AS
                        SELECT *
                        FROM {_quote_ident(temp_view_name)}
                        """
                    )
                else:
                    _ensure_target_table(conn, spec, temp_view_name)
                    if rows:
                        _merge_into_target(conn, spec, temp_view_name, columns)

                next_watermark = None
                if spec.watermark_column and rows:
                    next_watermark = max(
                        row[spec.watermark_column]
                        for row in rows
                        if row[spec.watermark_column] is not None
                    )
                elif spec.watermark_column:
                    next_watermark = watermark

                _store_sync_state(conn, spec, len(rows), next_watermark, "ok")
                print(f"{spec.source_name} -> {spec.target_schema}.{spec.target_table}: {len(rows)} row(s)")
            except Exception as exc:
                _store_sync_state(conn, spec, 0, watermark, "failed", str(exc))
                raise
    finally:
        await pg_conn.close()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
