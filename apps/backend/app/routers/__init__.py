"""API routers."""

from app.routers.admin import router as admin_router
from app.routers.agenda import router as agenda_router
from app.routers.applications import router as applications_router
from app.routers.auth import router as auth_router
from app.routers.config import router as config_router
from app.routers.contact import router as contact_router
from app.routers.enrichment import router as enrichment_router
from app.routers.health import router as health_router
from app.routers.internal import router as internal_router
from app.routers.interviews import ics_router as interviews_ics_router
from app.routers.interviews import router as interviews_router
from app.routers.jd import router as jd_router
from app.routers.jobs import router as jobs_router
from app.routers.media import router as media_router
from app.routers.notifications import router as notifications_router
from app.routers.profile import router as profile_router
from app.routers.public_profile import router as public_profile_router
from app.routers.reminders import router as reminders_router
from app.routers.reviews import router as reviews_router
from app.routers.resume_wizard import router as resume_wizard_router
from app.routers.resumes import router as resumes_router
from app.routers.search import router as search_router
from app.routers.users import router as users_router
from app.routers.versions import router as versions_router

__all__ = [
    "resumes_router",
    "versions_router",
    "jobs_router",
    "config_router",
    "contact_router",
    "reviews_router",
    "health_router",
    "enrichment_router",
    "applications_router",
    "resume_wizard_router",
    "auth_router",
    "users_router",
    "internal_router",
    "admin_router",
    "notifications_router",
    "search_router",
    "reminders_router",
    "interviews_router",
    "interviews_ics_router",
    "agenda_router",
    "jd_router",
    "media_router",
    "profile_router",
    "public_profile_router",
]
