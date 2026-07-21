"""SearchIndexer - outbox -> search_documents (design §C, R7.1/7.3/16.1).

Idempotent consumers translate resume/job/application change events into
content-safe search documents via the extensible **node-type registry**
(``NODE_TYPES``): each entry knows how to load the current source record (scoped
by the event's ``user_id``) and project it to a ``(title, body, status)`` search
doc. A missing source record => the doc is removed (handles create-then-delete
races). Because indexing is driven off the outbox it never blocks the user's
write, and it is fully **rebuildable** with **drift detection** for recovery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.events import EventType, OutboxEvent, register_handler
from app.search.repo import get_search_repo

logger = logging.getLogger(__name__)

__all__ = [
    "NODE_TYPES",
    "ensure_search_consumers_registered",
    "rebuild_user_index",
    "search_drift",
]

_BODY_MAX = 4000


def _clip(text: str | None, limit: int = _BODY_MAX) -> str:
    if not text:
        return ""
    return text[:limit]


def _db():
    from app import database

    return database.db


# ---------------------------------------------------------------------------
# Node-type registry (extensible - new node types add an entry, no API change)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeType:
    """How to load + project a searchable node."""

    name: str
    load: Callable[[str, str], Awaitable[dict[str, Any] | None]]
    project: Callable[[dict[str, Any]], dict[str, Any]]


def _project_resume(r: dict[str, Any]) -> dict[str, Any]:
    data = r.get("processed_data") or {}
    personal = data.get("personalInfo") or {}
    title = r.get("title") or personal.get("name") or r.get("filename") or "Resume"
    parts: list[str] = [personal.get("title") or "", data.get("summary") or ""]
    for exp in (data.get("workExperience") or [])[:10]:
        parts.append(f"{exp.get('title', '')} {exp.get('company', '')}")
    skills = (data.get("additional") or {}).get("technicalSkills") or []
    parts.append(" ".join(str(s) for s in skills[:40]))
    return {"title": str(title), "body": _clip(" ".join(p for p in parts if p)), "status": r.get("processing_status")}


def _project_job(j: dict[str, Any]) -> dict[str, Any]:
    company = j.get("company") or ""
    role = j.get("role") or ""
    title = (f"{company} - {role}".strip(" -")) or "Job description"
    return {"title": title, "body": _clip(j.get("content")), "status": None}


def _project_application(a: dict[str, Any]) -> dict[str, Any]:
    company = a.get("company") or ""
    role = a.get("role") or ""
    title = (f"{company} - {role}".strip(" -")) or "Application"
    body = " ".join(x for x in (company, role, a.get("notes") or "") if x)
    return {"title": title, "body": _clip(body), "status": a.get("status")}


NODE_TYPES: dict[str, NodeType] = {
    "resume": NodeType("resume", lambda uid, nid: _db().get_resume(uid, nid), _project_resume),
    "job": NodeType("job", lambda uid, nid: _db().get_job(uid, nid), _project_job),
    "application": NodeType(
        "application", lambda uid, nid: _db().get_application(uid, nid), _project_application
    ),
}


# ---------------------------------------------------------------------------
# Indexing primitives
# ---------------------------------------------------------------------------


async def index_node(user_id: str, node_type: str, node_id: str) -> None:
    """(Re)index a single node, or remove it if the source no longer exists."""
    spec = NODE_TYPES.get(node_type)
    if spec is None or not user_id:
        return
    record = await spec.load(user_id, node_id)
    repo = get_search_repo()
    if record is None:
        await repo.remove(node_type, node_id)
        return
    fields = spec.project(record)
    await repo.upsert(
        user_id=user_id,
        node_type=node_type,
        node_id=node_id,
        title=fields["title"],
        body=fields["body"],
        status=fields.get("status"),
    )


def _handler_for(node_type: str, *, delete: bool):
    async def handler(event: OutboxEvent) -> None:
        node_id = event.payload.get("node_id")
        if not node_id or not event.user_id:
            return
        if delete:
            await get_search_repo().remove(node_type, node_id)
        else:
            await index_node(event.user_id, node_type, node_id)

    return handler


_registered = False


def ensure_search_consumers_registered() -> None:
    """Register the outbox->search handlers once (idempotent)."""
    global _registered
    if _registered:
        return
    register_handler(EventType.RESUME_UPSERTED, _handler_for("resume", delete=False))
    register_handler(EventType.RESUME_DELETED, _handler_for("resume", delete=True))
    register_handler(EventType.JOB_UPSERTED, _handler_for("job", delete=False))
    register_handler(EventType.JOB_DELETED, _handler_for("job", delete=True))
    register_handler(EventType.APPLICATION_UPSERTED, _handler_for("application", delete=False))
    register_handler(EventType.APPLICATION_DELETED, _handler_for("application", delete=True))
    _registered = True


# ---------------------------------------------------------------------------
# Rebuild + drift (operator recovery)
# ---------------------------------------------------------------------------


async def rebuild_user_index(user_id: str) -> dict[str, int]:
    """Full, idempotent reindex of a user's nodes from source (R7.1)."""
    db = _db()
    repo = get_search_repo()
    await repo.clear_user(user_id)
    counts = {"resume": 0, "job": 0, "application": 0}
    for r in await db.list_resumes(user_id):
        await index_node(user_id, "resume", r["resume_id"])
        counts["resume"] += 1
    for j in await db.list_jobs(user_id):
        await index_node(user_id, "job", j["job_id"])
        counts["job"] += 1
    for a in await db.list_applications(user_id):
        await index_node(user_id, "application", a["application_id"])
        counts["application"] += 1
    return counts


async def search_drift(user_id: str) -> dict[str, Any]:
    """Compare indexed keys against source keys; report missing/extra (R7.1)."""
    db = _db()
    repo = get_search_repo()
    source: set[tuple[str, str]] = set()
    for r in await db.list_resumes(user_id):
        source.add(("resume", r["resume_id"]))
    for j in await db.list_jobs(user_id):
        source.add(("job", j["job_id"]))
    for a in await db.list_applications(user_id):
        source.add(("application", a["application_id"]))
    indexed = await repo.keys(user_id)
    missing = source - indexed  # in source, not indexed
    extra = indexed - source  # indexed, source gone
    return {"missing": len(missing), "extra": len(extra), "indexed": len(indexed), "source": len(source)}
