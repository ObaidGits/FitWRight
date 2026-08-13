"""Recording and reading per-board health.

The threshold question is what counts as a failure. A single empty search is
normal - the user may have asked for something rare. Three consecutive empty runs
against a board that has produced rows before is not normal; that is a dead
selector or a lapsed session. So:

* ``ok``        - rows came back. Resets the counter.
* ``empty``     - nothing came back. Counts as a failure only in aggregate.
* ``signed_out``- a login wall. Counts, and is the most actionable of all.
* ``error``     - the harvest itself failed.
* ``capped``    - the daily pacing limit. Explicitly NOT a failure: we chose not
                  to run, and counting our own restraint as a fault would tell
                  the user a healthy board is broken.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = ["FAILURE_THRESHOLD", "record_outcome", "list_health", "boards_needing_attention"]

# Consecutive bad runs before we say something is wrong.
FAILURE_THRESHOLD = 3

# Statuses that count against a board's health. `capped` is deliberately absent.
_FAILING = frozenset({"empty", "signed_out", "error"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def record_outcome(
    db,
    user_id: str,
    *,
    board: str,
    status: str,
    found: int = 0,
    error: str | None = None,
) -> None:
    """Record one harvest outcome for one board."""
    from sqlalchemy import select

    from app.models import BoardHealth

    now = _now()
    async with db._session() as session:  # noqa: SLF001
        async with session.begin():
            row = (
                await session.execute(
                    select(BoardHealth).where(
                        (BoardHealth.user_id == user_id) & (BoardHealth.board == board)
                    )
                )
            ).scalar_one_or_none()

            if row is None:
                row = BoardHealth(user_id=user_id, board=board, last_status=status, last_run_at=now)
                session.add(row)

            row.last_status = status
            row.last_error = (error or None) and str(error)[:500]
            row.last_run_at = now
            row.last_found = found
            row.total_runs = (row.total_runs or 0) + 1

            if status == "capped":
                # We chose not to run. Neither a success nor a fault.
                pass
            elif status in _FAILING:
                row.consecutive_failures = (row.consecutive_failures or 0) + 1
            else:
                row.consecutive_failures = 0
                row.last_success_at = now


async def record_run(db, user_id: str, per_site: list[dict[str, Any]]) -> int:
    """Record a whole run's per-board outcomes. Returns how many were recorded."""
    recorded = 0
    for entry in per_site or []:
        board = str(entry.get("source") or "").strip()
        if not board:
            continue
        found = int(entry.get("found") or 0)
        reason = entry.get("reason")
        if found > 0:
            status = "ok"
        elif reason in {"signed-out", "capped", "empty"}:
            status = {"signed-out": "signed_out"}.get(reason, reason)
        elif entry.get("error"):
            status = "error"
        else:
            status = "empty"

        await record_outcome(
            db,
            user_id,
            board=board,
            status=status,
            found=found,
            error=entry.get("error"),
        )
        recorded += 1
    return recorded


def _to_dict(row) -> dict[str, Any]:
    return {
        "board": row.board,
        "last_status": row.last_status,
        "last_error": row.last_error,
        "last_run_at": row.last_run_at,
        "last_success_at": row.last_success_at,
        "last_found": row.last_found,
        "consecutive_failures": row.consecutive_failures,
        "total_runs": row.total_runs,
        # The judgement, made once here so every surface agrees.
        "needs_attention": (row.consecutive_failures or 0) >= FAILURE_THRESHOLD,
        # A board that has worked before and is failing now is a different problem
        # from one that never worked - the first is fixable by the user.
        "worked_before": bool(row.last_success_at),
    }


async def list_health(db, user_id: str) -> list[dict[str, Any]]:
    """Every board we have run for this user, worst first."""
    from sqlalchemy import select

    from app.models import BoardHealth

    async with db._session() as session:  # noqa: SLF001
        rows = (
            (
                await session.execute(
                    select(BoardHealth)
                    .where(BoardHealth.user_id == user_id)
                    .order_by(BoardHealth.consecutive_failures.desc(), BoardHealth.board)
                )
            )
            .scalars()
            .all()
        )
    return [_to_dict(r) for r in rows]


async def boards_needing_attention(db, user_id: str) -> list[dict[str, Any]]:
    """Only the boards worth telling the user about."""
    return [b for b in await list_health(db, user_id) if b["needs_attention"]]
