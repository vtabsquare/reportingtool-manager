"""
VTAB Reporting Manager - SQLite to Supabase Migration Utility
Transfers all tables and data from local SQLite to Supabase PostgreSQL.

Usage:
    python migrate_sqlite_to_supabase.py
    or
    python migrate_sqlite_to_supabase.py --database-url "postgresql://postgres:[PASSWORD]@[HOST]:5432/postgres"
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

TABLES_IN_ORDER = [
    "clients",
    "client_agents",
    "releases",
    "deployments",
    "deployment_logs",
    "deployment_locks",
    "support_tickets",
    "client_infrastructure",
    "client_configuration_versions",
    "manager_settings",
    "audit_events",
    "github_webhook_events",
]

AUTO_INCREMENT_TABLES = [
    ("deployment_logs", "id"),
    ("audit_events", "id"),
    ("client_configuration_versions", "id"),
]


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")
    parser = argparse.ArgumentParser(description="Migrate SQLite DB to Supabase PostgreSQL")
    parser.add_argument(
        "--database-url",
        default=os.getenv("VTAB_MANAGER_DATABASE_URL", ""),
        help="Supabase PostgreSQL connection URL",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(Path(__file__).parent / "data" / "vtab_manager.db"),
        help="Path to source SQLite database file",
    )
    args = parser.parse_args()

    database_url = args.database_url.strip()
    if not database_url:
        print("[ERROR] No Supabase DATABASE_URL provided.")
        print("Please provide it via --database-url or set VTAB_MANAGER_DATABASE_URL in .env")
        return 1

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        print(f"[ERROR] Source SQLite database not found at {sqlite_path}")
        return 1

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("[ERROR] psycopg is required for PostgreSQL connection. Install it with: pip install psycopg[binary]")
        return 1

    print(f"[*] Connecting to SQLite database at {sqlite_path}...")
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    print("[*] Connecting to Supabase PostgreSQL...")
    try:
        pg_conn = psycopg.connect(database_url, autocommit=False)
    except Exception as e:
        print(f"[ERROR] Failed to connect to Supabase: {e}")
        return 1

    print("[*] Applying Supabase schema...")
    schema_path = Path(__file__).parent / "supabase_schema.sql"
    if schema_path.exists():
        with pg_conn.cursor() as cur:
            cur.execute(schema_path.read_text(encoding="utf-8"))
        pg_conn.commit()
        print("[+] Schema successfully created/verified in Supabase.")

    print("\n[*] Migrating table data...")
    total_migrated = 0

    for table in TABLES_IN_ORDER:
        sqlite_rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        if not sqlite_rows:
            print(f"  - {table}: 0 rows (skipped)")
            continue

        columns = list(sqlite_rows[0].keys())
        col_names = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        query = f'INSERT INTO public.{table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

        migrated_for_table = 0
        with pg_conn.cursor() as cur:
            for row in sqlite_rows:
                vals = [row[c] for c in columns]
                cur.execute(query, vals)
                migrated_for_table += cur.rowcount

        pg_conn.commit()
        print(f"  + {table}: {len(sqlite_rows)} rows checked ({migrated_for_table} new rows inserted)")
        total_migrated += migrated_for_table

    # Reset sequences for auto-increment tables so next inserts succeed without conflict
    print("\n[*] Updating sequence generators for auto-increment primary keys...")
    with pg_conn.cursor() as cur:
        for table, col in AUTO_INCREMENT_TABLES:
            try:
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('public.{table}', '{col}'), COALESCE((SELECT MAX({col}) FROM public.{table}), 1));"
                )
            except Exception as e:
                print(f"  ! Notice on {table} sequence: {e}")
        pg_conn.commit()

    pg_conn.close()
    sqlite_conn.close()

    print(f"\n[SUCCESS] Migration completed successfully! Total rows inserted: {total_migrated}")
    print("[*] Next step: set VTAB_MANAGER_DATABASE_URL in .env to use Supabase permanently.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
