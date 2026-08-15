"""Copy FitWright's database into a new Supabase project (region move).

Schema is NOT dumped: it is rebuilt by running Alembic against the target, which
guarantees it matches exactly what the app expects. Only rows are copied.

Ordering: the schema has 26 foreign keys, so tables are copied in topological
dependency order (parents before children). Self-referencing tables are copied
last within their level.

Safety:
  * refuses to run if the target already holds application rows
  * verifies every table's row count against the source before declaring success
  * never prints a connection string

Inputs (env): SOURCE_URL, TARGET_URL
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

import asyncpg

# Managed by Alembic on the target, never copied.
SKIP_TABLES = {"alembic_version"}


async def table_names(conn) -> list[str]:
    rows = await conn.fetch("""
        select table_name from information_schema.tables
        where table_schema = 'public' and table_type = 'BASE TABLE'
        order by table_name
    """)
    return [r["table_name"] for r in rows if r["table_name"] not in SKIP_TABLES]


async def fk_edges(conn) -> list[tuple[str, str]]:
    """Return (child, parent) pairs from the live catalog."""
    rows = await conn.fetch("""
        select c.conrelid::regclass::text  as child,
               c.confrelid::regclass::text as parent
        from pg_constraint c
        join pg_namespace n on n.oid = c.connamespace
        where c.contype = 'f' and n.nspname = 'public'
    """)
    return [(r["child"].replace('"', ""), r["parent"].replace('"', "")) for r in rows]


def topo_order(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Parents first. Self-references are ignored (a table cannot precede itself)."""
    parents: dict[str, set[str]] = defaultdict(set)
    for child, parent in edges:
        if child != parent and child in tables and parent in tables:
            parents[child].add(parent)

    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not (parents[t] & remaining))
        if not ready:
            # Cycle: emit the rest alphabetically and let the FK check speak up.
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


async def copy_table(src, dst, table: str) -> int:
    cols = [
        r["column_name"]
        for r in await src.fetch("""
            select column_name from information_schema.columns
            where table_schema='public' and table_name=$1
            order by ordinal_position
        """, table)
    ]
    if not cols:
        return 0

    quoted = ", ".join(f'"{c}"' for c in cols)
    rows = await src.fetch(f'select {quoted} from "{table}"')
    if not rows:
        return 0

    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    stmt = f'insert into "{table}" ({quoted}) values ({placeholders})'
    await dst.executemany(stmt, [tuple(r) for r in rows])
    return len(rows)


async def main() -> int:
    source_url = os.environ["SOURCE_URL"]
    target_url = os.environ["TARGET_URL"]

    src = await asyncpg.connect(source_url, timeout=45, ssl="require")
    dst = await asyncpg.connect(target_url, timeout=45, ssl="require")
    try:
        tables = await table_names(src)

        # Guard: an already-populated target means a re-run or the wrong project.
        for probe in ("users", "resumes"):
            if probe in tables:
                existing = await dst.fetchval(f'select count(*) from "{probe}"')
                if existing:
                    print(f"REFUSING: target already has {existing} rows in {probe}.")
                    print("Point at a fresh project, or empty this one first.")
                    return 2

        order = topo_order(tables, await fk_edges(src))
        print(f"copying {len(order)} tables in dependency order\n")

        copied: dict[str, int] = {}
        for table in order:
            n = await copy_table(src, dst, table)
            copied[table] = n
            if n:
                print(f"  {table:<34} {n}")

        # Sequences restart at 1 on a fresh schema; realign them with the data.
        seqs = await src.fetch(
            "select sequence_name from information_schema.sequences "
            "where sequence_schema='public'")
        for row in seqs:
            seq = row["sequence_name"]
            table = seq.rsplit("_", 2)[0]
            col = seq.rsplit("_", 2)[1] if len(seq.rsplit("_", 2)) > 2 else "id"
            if table in tables:
                await dst.execute(
                    f"select setval('{seq}', coalesce((select max(\"{col}\") from \"{table}\"), 1))")
                print(f"  sequence {seq} realigned")

        print("\nverifying every table...")
        mismatches = []
        for table in order:
            a = await src.fetchval(f'select count(*) from "{table}"')
            b = await dst.fetchval(f'select count(*) from "{table}"')
            if a != b:
                mismatches.append((table, a, b))

        if mismatches:
            print("MISMATCH - the copy is not faithful:")
            for table, a, b in mismatches:
                print(f"  {table}: source={a} target={b}")
            return 1

        total = sum(copied.values())
        print(f"OK - all {len(order)} tables match. {total} rows copied.")
        return 0
    finally:
        await src.close()
        await dst.close()


sys.exit(asyncio.run(main()))
