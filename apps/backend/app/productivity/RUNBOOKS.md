# P3 Productivity — Operations Runbooks

Operational procedures for the P3 productivity subsystem (design §Observability).
All jobs are single-flighted (KVStore lock) + idempotent, so every recovery
action below is safe to run repeatedly and safe to run while workers are live.

## Metrics & alerts

Scrape `GET /api/v1/internal/metrics` (header `X-Internal-Job-Token`). The
`productivity` section carries in-process counters (per worker — scrape all) and
live, worker-independent gauges:

| Signal | Source | Alert when |
|---|---|---|
| outbox backlog | `productivity.gauges.backlog` | > 1000 sustained (indexer/notifier lag) |
| outbox DLQ depth | `productivity.gauges.dead` | > 0 (poison events parked) |
| SSRF probes | `jd_blocked_ssrf_total` | spikes (someone probing internal hosts) |
| avatar rejects | `avatar_upload_total.rejected` | spikes (malicious-upload attempts) |
| duplicate prevention | `notification_deduped_total` | informational (exactly-once working) |
| double-fire | (none — guaranteed 0 by claim + dedupe_key) | any observed duplicate notification |

Feature flags / kill-switches (env, no redeploy): `SEARCH_ENABLED`,
`NOTIFICATIONS_ENABLED`, `NOTIFICATIONS_EMAIL_ENABLED`, `REMINDERS_ENABLED`,
`INTERVIEWS_ENABLED`, `AGENDA_ENABLED`, `JD_FROM_URL_ENABLED`, `VERSION_HISTORY_ENABLED`.

## Runbook: drain / replay the outbox

- Backlog high: confirm the cron (`POST /internal/run-jobs`) is firing; each call
  drains a bounded batch. Increase cron frequency or switch `SCHEDULER_MODE=internal`.
- Poison events (DLQ > 0): inspect `outbox.last_error`. After fixing the cause,
  replay: `app.events.outbox.replay_dead_letters()` (re-arms `dead_at`/`attempts`).

## Runbook: rebuild the search index / fix drift

- Per-user rebuild: `POST /api/v1/search/reindex` (as that user), or
  `app.search.indexer.rebuild_user_index(user_id)`.
- Detect drift: `app.search.indexer.search_drift(user_id)` → `{missing, extra}`.
  Non-zero `missing` ⇒ run a rebuild; `extra` self-heals on the next write or
  rebuild.

## Runbook: unstick a scheduler claim

A reminder stuck in `firing` (crashed worker) is auto-reclaimed after the claim
lease (`_CLAIM_LEASE_SECONDS`, 5 min) on the next scan — no action needed. To
force it: set `status='pending'`, `claimed_at=NULL` for the row.

## Runbook: reconcile the unread counter

If a badge looks wrong: `app.notifications.repo.NotificationRepo.reconcile_unread(user_id)`
recomputes it from the table (O(1) badge restored).

## Runbook: replay the email DLQ

Notification emails that failed send are left with `emailed_at IS NULL` and
retried on the next `process_pending_emails` pass. If the provider was down,
just wait for the next cron; nothing is lost.

## Runbook: reclaim orphan avatars

Runs automatically in the retention job (`_reclaim_orphan_avatars`, local
provider). Hosted CDN objects are reclaimed by the provider lifecycle rules; the
DB `users.avatar_key` is the authoritative reference set (`all_avatar_keys`).

## Retention windows (env)

`NOTIFICATION_RETENTION_DAYS` (read/dismissed notifications + fired reminders),
`OUTBOX_RETENTION_DAYS` (processed outbox rows). Dead-lettered outbox rows are
retained for inspection.
