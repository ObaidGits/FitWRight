# CodeScout — Graph Report

> Auto-generated architectural overview. No LLM tokens used.

## 📊 Statistics

| Metric | Value |
|--------|-------|
| Nodes | 1053 |
| Edges | 1499 |
| Avg connections/file | 2.8 |
| Max fan-in | 118 |
| Max fan-out | 33 |

## 🏛️ God Nodes (highest connectivity)

These files are the backbone of the project — many other files depend on them.

- **apps/backend/app/config.py** — 118 connections (entrypoint)
- **apps/backend/app/main.py** — 56 connections (entrypoint)
- **apps/backend/app/models.py** — 46 connections (entrypoint)
- **apps/backend/app/database.py** — 39 connections (entrypoint)
- **docs/agent/README.md** — 36 connections (orchestrator (imports many))
- **apps/backend/app/admin/schemas.py** — 35 connections (entrypoint)
- **apps/backend/app/routers/__init__.py** — 31 connections (entrypoint)
- **apps/backend/app/routers/resumes.py** — 31 connections (entrypoint)

## 🏘️ Communities (file clusters)

### apps/backend
apps/backend module (629 files)

- README.md
- SETUP.md
- apps/backend/alembic/env.py
- apps/backend/app/admin/__init__.py
- apps/backend/app/admin/ai_metrics.py
- apps/backend/app/admin/config_diag.py
- apps/backend/app/admin/cursor.py
- apps/backend/app/admin/deps.py
- apps/backend/app/admin/error_reports_service.py
- apps/backend/app/admin/errors_metrics.py
- ... and 619 more

### apps/extension
apps/extension module (7 files)

- apps/extension/src/lib/api.ts
- apps/extension/src/lib/diagnostics.ts
- apps/extension/src/lib/dom.ts
- apps/extension/src/lib/fields.ts
- apps/extension/src/lib/messages.ts
- apps/extension/src/lib/storage.ts
- apps/extension/src/lib/types.ts

### apps/frontend
apps/frontend module (7 files)

- apps/frontend/lib/resilience/backoff.ts
- apps/frontend/lib/resilience/crypto.ts
- apps/frontend/lib/resilience/integrity.ts
- apps/frontend/lib/resilience/local-store.ts
- apps/frontend/lib/resilience/save-controller.ts
- apps/frontend/lib/resilience/store-engine.ts
- apps/frontend/lib/resilience/sync-controller.ts

### apps/extension
apps/extension module (6 files)

- apps/extension/src/adapters/ats.ts
- apps/extension/src/adapters/boards.ts
- apps/extension/src/adapters/enterprise-ats.ts
- apps/extension/src/adapters/generic.ts
- apps/extension/src/adapters/registry.ts
- apps/extension/src/adapters/types.ts

### apps/extension
apps/extension module (4 files)

- apps/extension/src/content/autofill.ts
- apps/extension/src/content/index.ts
- apps/extension/src/content/overlay.ts
- apps/extension/src/content/tracking.ts

### apps/frontend
apps/frontend module (4 files)

- apps/frontend/components/enrichment/enrichment-modal.tsx
- apps/frontend/components/enrichment/loading-steps.tsx
- apps/frontend/components/enrichment/preview-step.tsx
- apps/frontend/components/enrichment/question-step.tsx

### apps/frontend
apps/frontend module (4 files)

- apps/frontend/components/preview/index.ts
- apps/frontend/components/preview/page-container.tsx
- apps/frontend/components/preview/paginated-preview.tsx
- apps/frontend/components/preview/use-pagination.ts

### apps/frontend
apps/frontend module (4 files)

- apps/frontend/lib/seo/config.ts
- apps/frontend/lib/seo/metadata.ts
- apps/frontend/lib/seo/og-image.tsx
- apps/frontend/lib/seo/structured-data.ts

### apps/backend
apps/backend module (3 files)

- apps/backend/app/ai_receipts.py
- apps/backend/app/app_settings.py
- apps/backend/tests/integration/test_receipts_and_mail.py

### apps/backend
apps/backend module (3 files)

- apps/backend/app/job_discovery/board_health.py
- apps/backend/app/job_discovery/retention.py
- apps/backend/tests/test_discovery_trust.py

### apps/backend
apps/backend module (3 files)

- apps/backend/app/productivity/__init__.py
- apps/backend/app/productivity/metrics.py
- apps/backend/tests/unit/test_productivity_metrics.py

### apps/frontend
apps/frontend module (3 files)

- apps/frontend/components/atelier/button.tsx
- apps/frontend/components/atelier/confirm-dialog.tsx
- apps/frontend/components/atelier/dialog.tsx

### apps/frontend
apps/frontend module (3 files)

- apps/frontend/components/atelier/link-dialog.tsx
- apps/frontend/components/atelier/rich-text-editor.tsx
- apps/frontend/components/atelier/rich-text-toolbar.tsx

### apps/backend
apps/backend module (2 files)

- apps/backend/app/ai_feature_prices.py
- apps/backend/tests/integration/test_pricing_and_plans.py

### apps/backend
apps/backend module (2 files)

- apps/backend/app/job_discovery/search_jobs.py
- apps/backend/tests/test_background_search.py

### apps/backend
apps/backend module (2 files)

- apps/backend/app/profile/analytics.py
- apps/backend/app/profile/analytics_consumer.py

### apps/backend
apps/backend module (2 files)

- apps/backend/app/retention/__init__.py
- apps/backend/app/retention/jobs.py

### apps/backend
apps/backend module (2 files)

- apps/backend/app/scripts/check_scoping.py
- apps/backend/tests/unit/test_scoping_guard.py

### apps/backend
apps/backend module (2 files)

- apps/backend/e2e_monitor/AGENT_PLAYBOOK.md
- apps/backend/e2e_monitor/README.md

### apps/backend
apps/backend module (2 files)

- apps/backend/tests/architecture/test_router_db_isolation.py
- apps/backend/tests/conftest.py

### apps/frontend
apps/frontend module (2 files)

- apps/frontend/app/(app)/wizard/page.tsx
- apps/frontend/app/(app)/wizard/structured-sections.tsx

### apps/frontend
apps/frontend module (2 files)

- apps/frontend/components/marketing/faq-data.ts
- apps/frontend/components/marketing/faq.tsx

### apps/frontend
apps/frontend module (2 files)

- apps/frontend/components/resilience/degradation-banner.tsx
- apps/frontend/components/resilience/resilience-provider.tsx

### apps/frontend
apps/frontend module (2 files)

- apps/frontend/components/resume/render-template.tsx
- apps/frontend/components/resume/resume-document.tsx

### apps/frontend
apps/frontend module (2 files)

- apps/frontend/features/discovery/extension-bridge.ts
- apps/frontend/features/discovery/use-extension.ts

### root
root module (1 files)

- AGENTS.md

### root
root module (1 files)

- CLAUDE.md

### root
root module (1 files)

- HEROKU_DEPLOY_INSTRUCTIONS.md

### root
root module (1 files)

- INSTRUCTIONS_TAILOR_RESUME.md

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0001_baseline_current_implicit_schema.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0002_auth_tables_and_kv.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0003_add_user_id_to_owned_tables.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0004_backfill_bootstrap_owner.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0005_enforce_user_scoping_constraints.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0006_email_change_tokens.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0007_admin_soft_delete_counters_metrics.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0008_admin_search_and_active_indexes.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0009_resume_versions.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0010_platform_notifications.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0011_search_documents.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0012_reminders_interviews.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0013_profile_avatar_fields.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0014_resume_version_cas.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0015_profile_system.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0016_profile_public_sharing.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0017_profile_public_theme.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0018_profile_image_metadata.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0019_analysis_artifacts.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0020_resume_template_settings.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0021_resume_version_template_settings.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0022_tailor_previews.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0023_admin_invites.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0024_admin_observability_accuracy.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0025_user_llm_configs.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0026_user_error_reports.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0027_job_discovery_tables.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0028_discovery_feed_tables.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0029_application_fields_and_submissions.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0030_discovery_queue_link_and_grouping.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0031_board_health.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0032_resume_ats_score.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0033_ai_channels.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0034_ai_usage_ledger.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0035_credit_accounts.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0036_channel_credential_on_row.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0037_usage_latency.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0038_credit_purchases.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0039_credit_packs.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0040_feature_prices_and_plans.py

### apps/backend
apps/backend module (1 files)

- apps/backend/alembic/versions/0041_app_settings.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/ai_abuse_signals.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/ai_channel_test.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/ai_plans.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/ai_purchase_notify.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/analytics/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/applications/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/jd/concurrency.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/jd/health.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/jd/ml_scorer.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/jd/monitoring/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/job_discovery/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/job_discovery/connectors/browser_fetch.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/job_discovery/scoring.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/job_discovery/worker.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/migrations_runtime.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/productivity/RUNBOOKS.md

### apps/backend
apps/backend module (1 files)

- apps/backend/app/profile/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/app/scripts/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/e2e_monitor/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/scripts/check_model_migration_parity.py

### apps/backend
apps/backend module (1 files)

- apps/backend/scripts/razorpay_webhook.py

### apps/backend
apps/backend module (1 files)

- apps/backend/scripts/seed_credit_packs.py

### apps/backend
apps/backend module (1 files)

- apps/backend/scripts/seed_pricing.py

### apps/backend
apps/backend module (1 files)

- apps/backend/scripts/verify_postgres.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_admin_import_graph.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_ai_metering_ratchet.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_composition_ownership.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_domain_purity.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_module_ownership.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_non_goals_guard.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_ports_registry.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/architecture/test_profile_containment.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/contract/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/contract/test_kvstore_contract.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/evals/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/evals/golden/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/integration/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/integration/test_ai_channel_credentials.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/integration/test_ai_untested_paths.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/integration/test_redis_kvstore.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/live/test_live_llm_features.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/service/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/test_discovery_feed_filters.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/test_jd_golden.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/test_jd_phase2.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/unit/__init__.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/unit/test_auth_interfaces.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/unit/test_check_locale_parity.py

### apps/backend
apps/backend module (1 files)

- apps/backend/tests/unit/test_photo_flows.py

### apps/extension
apps/extension module (1 files)

- apps/extension/build.mjs

### apps/extension
apps/extension module (1 files)

- apps/extension/src/background/service-worker.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/content/bridge.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/lib/application-form.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/lib/bridge-registration.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/lib/i18n.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/lib/login-wall.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/lib/pacing.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/lib/site-prefs.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/options/options.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/src/popup/popup.ts

### apps/extension
apps/extension module (1 files)

- apps/extension/vitest.config.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/agenda/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/answers/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/applications/[id]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/applications/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/billing/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/builder/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/discovery/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/error.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/home/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/import/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/layout.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/loading.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/profile/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/resumes/[id]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/resumes/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/samples/[slug]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/samples/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/settings/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/setup/extension/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/tailor/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/templates/[slug]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(app)/templates/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/forgot/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/layout.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/login/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/reset/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/signup/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/verify-email/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(auth)/verify/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/ats-resume-checker/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/connect/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/contact/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/cover-letter-generator/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/interview-preparation/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/layout.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/pricing/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/privacy/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/resume-tailoring/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/(marketing)/terms/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/ai-ops/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/ai/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/audit/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/business/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/channels/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/errors/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/health/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/invites/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/layout.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/packs/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/pricing/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/spend/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/storage/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/admin/users/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/error.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/global-error.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/layout.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/manifest.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/not-found.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/opengraph-image.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/p/[slug]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/p/[slug]/portfolio/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/print/cover-letter/[id]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/print/interview-prep/[id]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/print/resumes/[id]/page.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/robots.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/sitemap.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/app/twitter-image.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/admin/admin-shell.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/admin/delete-user-dialog.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/admin/local-time.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/admin/mini-chart.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/admin/user-credits-panel.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/ai/ai-progress.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/ai/explain.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/answers/application-answers.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/answers/eligibility-answers.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/applications/apply-queue.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/applications/outcomes.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/badge.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/dropdown-menu.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/input.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/label.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/misc.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/select.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/sheet.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/skeleton.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/states.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/tab-strip.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/table.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/tabs.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/atelier/toast.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/auth-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/email-change-confirm-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/error-banner.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/forgot-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/password-field.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/reset-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/step-up-modal.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/verify-email-banner.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/auth/verify-email-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/billing/plan-badge.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/builder/tab-ids.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/command/command-palette.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/common/error-boundary.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/common/profile-avatar.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/common/resume_previewer_context.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/common/unsaved-changes-guard.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/connect/review-form.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/contact/contact-form.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/dashboard/resume-component.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/dev/mode-mismatch-banner.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/discovery/feed-health-panel.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/discovery/search-progress-bar.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/account-menu.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/app-shell.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/bottom-nav.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/nav-items.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/pane-toggle.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/public-top-bar.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/layout/sidebar.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/capabilities-data.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/capability-landing.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/contact-cta.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/hero.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/home-pricing.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/mockups.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/pricing-calculator.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/pricing-tables.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/marketing/reveal.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/notifications/notification-center.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/analytics-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/avatar-uploader.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/completeness-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/export-menu.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/import-dialog.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/profile-search.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/profile-workspace.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/share-dialog.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/skill-tag-input.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/sync-dialog.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/profile/version-history.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/providers/app-providers.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/providers/rate-limit-listener.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/public/public-profile-view.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resilience/conflict-dialog.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resilience/offline-indicator.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resilience/recovery-banner.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resilience/recovery-center.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resilience/save-status-chip.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resume/export-button.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resume/resume-cta-buttons.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resume/resume-thumbnail.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resume/sample-gallery.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resume/template-gallery.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/resume/version-history-panel.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/scheduling/scheduling-panel.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/settings/account-security.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/settings/ai-usage-panel.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/settings/buy-credits.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/settings/feature-prompts-editor.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/settings/notification-preferences.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/settings/profile-settings.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/tailor/ats-score-card.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/theme/theme-provider.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/components/theme/theme-toggle.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/e2e/auth.setup.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/eslint.config.mjs

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/admin/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/agenda/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/application-fields/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/applications/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/applications/queue-hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/discovery/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/home/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/profile/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/resumes/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/resumes/upload.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/scheduling/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/settings/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/features/tailor/hooks.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/hooks/use-file-upload.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/hooks/use-regenerate-wizard.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/ai-availability.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/ai-progress-copy.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/api/public-pricing.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/cloudinary.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/config/auth.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/config/version.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/constants/page-dimensions.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/context/session.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/hooks/use-ai-progress.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/hooks/use-autosave.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/hooks/use-draft.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/hooks/use-recovery.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/hooks/use-resilience-flags.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/hooks/use-stream.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/i18n/locale.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/layout/page-width.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/query/client.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resilience/degradation.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resilience/diff.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resilience/reachability.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resilience/stream-client.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resilience/sw-register.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resilience/tab-coordinator.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/appearance-storage.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/filename.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/pagination.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/preferred-template.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/sample-catalog.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/sample-resume.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/template-catalog.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/resume/template-recommend.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/seo/json-ld.tsx

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/seo/page-keywords.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/types/domain.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/types/lucide.d.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils/download.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils/hidden-items.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils/html-sanitizer.browser.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils/html-sanitizer.server.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils/html-sanitizer.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/lib/utils/resume-sort.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/middleware.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/next-env.d.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/next.config.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/playwright.config.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/postcss.config.mjs

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/public/sw.js

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/vitest.config.ts

### apps/frontend
apps/frontend module (1 files)

- apps/frontend/vitest.setup.ts

### deploy/supabase
deploy/supabase module (1 files)

- deploy/supabase/RUNBOOK.md

### docs/agent
docs/agent module (1 files)

- docs/agent/features/job-discovery.md

### docs/runbooks
docs/runbooks module (1 files)

- docs/runbooks/ai-credits.md

### scripts
scripts module (1 files)

- scripts/check_locale_parity.py

### scripts
scripts module (1 files)

- scripts/db_region_move.py

## ❓ Suggested Questions

Questions this graph is well-positioned to answer:

- What is the responsibility of app/config.py and why does everything depend on it?
- How do apps/backend and apps/extension interact?
- What is the data flow between app/config.py and app/main.py?
- Which files would be affected if I refactor the most-connected module?
- Is scripts/db_region_move.py still needed or is it dead code?

---
*Generated by CodeScout • 8/15/2026 • Zero tokens used*
