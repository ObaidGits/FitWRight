"""ML-based content scoring for JD extraction (§14, Phase 4).

A lightweight, dependency-free logistic-regression classifier that estimates the
probability a block of text is a genuine job description (vs navigation, cookie
banners, marketing boilerplate, or an error page). It is used as an ADDITIONAL
confidence signal for the ambiguous DOM/headless extraction paths - never for
the authoritative API/JSON-LD paths, and never to fabricate content.

Design choices (honesty + latency):
- Pure Python, no numpy/sklearn - keeps the dependency graph and cold-start light.
- Trained lazily on a small COMMITTED labeled corpus (``_TRAINING_DATA``) with
  deterministic gradient descent (fixed init, fixed data -> reproducible weights).
- Feature extraction is O(n) over the text and runs in well under 1ms for typical
  JD lengths.

This is a genuine trained model (logistic regression fit on labeled examples),
not a neural net. The "corpus" is intentionally small and can be expanded; the
``train()`` function is exposed so weights can be re-fit from a larger dataset.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = ["score_content", "extract_features", "train", "MODEL", "FEATURE_NAMES"]

FEATURE_NAMES = (
    "bias",
    "log_len",
    "heading_hits",
    "bullet_density",
    "jd_keyword_density",
    "negative_density",
    "avg_words_per_line",
    "has_experience_signal",
)

_HEADING_KW = (
    "responsibilities", "requirements", "qualifications", "what you'll do",
    "what you will do", "who you are", "about the role", "the role", "what we offer",
    "benefits", "your profile", "we are looking for", "key responsibilities",
    "skills", "duties", "role overview",
)

_JD_KW = (
    "experience", "team", "develop", "design", "build", "manage", "collaborate",
    "responsible", "skills", "years", "degree", "engineer", "work", "role",
    "candidate", "ability", "knowledge", "strong", "communication", "project",
    "customer", "product", "technical", "support", "lead", "requirements",
)

_NEGATIVE_KW = (
    "cookie", "cookies", "subscribe", "newsletter", "sign in", "log in", "log out",
    "privacy policy", "terms of service", "all rights reserved", "©", "menu",
    "navigation", "skip to content", "search jobs", "create account",
    "accept all", "back to top", "follow us", "©", "404", "page not found",
    "enable javascript",
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪]|\d+[.)]|[a-z][.)])\s+", re.M)


@dataclass
class LogisticModel:
    weights: list[float]

    def predict(self, features: list[float]) -> float:
        z = sum(w * f for w, f in zip(self.weights, features))
        # Clamp to avoid overflow in exp.
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))


def extract_features(text: str) -> list[float]:
    """Extract the fixed feature vector (matches FEATURE_NAMES) from ``text``."""
    if not text:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    lower = text.lower()
    n_chars = len(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    n_lines = max(1, len(lines))
    words = _WORD_RE.findall(text)
    n_words = max(1, len(words))

    # 1. Normalized log length (0..1, saturates ~4000 chars).
    log_len = min(1.0, math.log10(n_chars + 1) / math.log10(4000))

    # 2. Heading keyword hits (normalized, saturates at 5).
    heading_hits = min(1.0, sum(1 for kw in _HEADING_KW if kw in lower) / 5.0)

    # 3. Bullet density: bullet lines / total lines.
    bullet_lines = len(_BULLET_RE.findall(text))
    bullet_density = min(1.0, bullet_lines / n_lines)

    # 4. JD keyword density: distinct JD keywords present / total keyword set.
    jd_hits = sum(1 for kw in _JD_KW if kw in lower)
    jd_keyword_density = min(1.0, jd_hits / 12.0)

    # 5. Negative signal density (normalized, saturates at 4).
    neg_hits = sum(1 for kw in _NEGATIVE_KW if kw in lower)
    negative_density = min(1.0, neg_hits / 4.0)

    # 6. Average words per non-empty line (normalized, saturates ~20).
    avg_words_per_line = min(1.0, (n_words / n_lines) / 20.0)

    # 7. Experience/seniority signal.
    has_experience_signal = 1.0 if re.search(r"\b\d+\+?\s*(years|yrs)\b", lower) else 0.0

    return [
        1.0,  # bias
        log_len,
        heading_hits,
        bullet_density,
        jd_keyword_density,
        negative_density,
        avg_words_per_line,
        has_experience_signal,
    ]


# --- Committed labeled training corpus ------------------------------------
# (text, label) where label 1 = genuine JD content, 0 = non-JD (nav/boilerplate).
_POSITIVE = [
    "Senior Backend Engineer\n\nResponsibilities:\n- Design and build scalable APIs using Python and FastAPI\n- Lead migration from monolith to microservices\n- Mentor junior engineers\n\nRequirements:\n- 5+ years of backend experience\n- Strong knowledge of distributed systems and databases",
    "About the role\nWe are looking for a Product Designer to own end-to-end design for our mobile app. You will run user research, build prototypes, and collaborate with engineering.\n\nWhat we're looking for:\n- 4+ years of product design experience\n- Strong portfolio of shipped work\n- Excellent communication skills",
    "Data Scientist\n\nThe Role:\n- Build and deploy production ML models\n- Own the full model lifecycle\n- Partner with product teams\n\nQualifications:\n- 3+ years in applied machine learning\n- Python, PyTorch, SQL\n- Strong statistical background",
    "Staff Software Engineer\n\nWhat you'll do:\n- Own the reliability of our core services\n- Design multi-region failover\n- Set engineering standards across teams\n\nWhat we look for:\n- 8+ years building distributed systems\n- Deep expertise in Go or Rust\n- Leadership experience",
    "Marketing Manager\n\nResponsibilities include developing marketing campaigns, managing the content calendar, and analyzing performance. Requirements: 5 years of marketing experience, strong analytical skills, and excellent written communication.",
    "DevOps Engineer\n\nYou will manage our cloud infrastructure, build CI/CD pipelines, and improve observability. Skills required: Kubernetes, Terraform, AWS, and 4+ years of experience in a similar role. Strong problem-solving ability.",
    "Customer Success Manager\n\nAbout the role: You will be responsible for onboarding new customers, driving product adoption, and reducing churn. Qualifications: 3+ years in customer-facing roles, excellent communication, and experience with SaaS products.",
    "Frontend Engineer\n\nWhat you will do:\n- Build responsive web applications with React and TypeScript\n- Collaborate with designers to deliver polished UI\n- Write tests and maintain code quality\n\nRequirements:\n- 3+ years frontend experience\n- Strong CSS and accessibility knowledge",
    "Financial Analyst\n\nThe candidate will prepare financial models, analyze budgets, and support forecasting. Requirements include a degree in finance or accounting, 2+ years of experience, and strong Excel skills. Knowledge of SQL is a plus.",
    "Registered Nurse\n\nResponsibilities: provide patient care, administer medication, and collaborate with the medical team. Qualifications: active nursing license, 2+ years of clinical experience, and strong interpersonal skills.",
]

_NEGATIVE = [
    "Home About Products Pricing Blog Contact Sign in Create account\nFollow us on Twitter Facebook LinkedIn\n© 2026 Company Inc. All rights reserved. Privacy Policy Terms of Service",
    "We use cookies to improve your experience. By continuing to browse the site you agree to our use of cookies. Accept all Manage preferences",
    "404 Page not found. The page you are looking for does not exist. Return to the homepage or search for what you need.",
    "Please enable JavaScript to view this site. This application requires JavaScript to run. Enable it in your browser settings and reload.",
    "Subscribe to our newsletter to get the latest updates. Enter your email address. By subscribing you agree to receive marketing emails.",
    "Skip to content Menu Search jobs Sign in Back to top Loading... Please wait while we load the content.",
    "Cookie settings We value your privacy. We and our partners store and access information on your device. Accept Reject Manage options",
    "Login required. Please sign in to view this job posting. Don't have an account? Create one now. Forgot your password?",
    "Just a moment... Checking your browser before accessing the site. This process is automatic. Your browser will redirect shortly.",
    "Search results Filters Location Salary Date posted Sort by relevance Showing 1-20 of 340 jobs Next page Previous page",
]


def train(dataset: list[tuple[str, int]] | None = None, *, iterations: int = 400, lr: float = 0.3) -> LogisticModel:
    """Fit logistic-regression weights on ``dataset`` (deterministic).

    Defaults to the committed corpus. Returns a LogisticModel. Pure batch
    gradient descent with zero-init weights - reproducible across runs.
    """
    if dataset is None:
        dataset = [(t, 1) for t in _POSITIVE] + [(t, 0) for t in _NEGATIVE]

    feats = [extract_features(t) for t, _ in dataset]
    labels = [float(y) for _, y in dataset]
    n_features = len(FEATURE_NAMES)
    weights = [0.0] * n_features
    m = len(dataset)

    for _ in range(iterations):
        grad = [0.0] * n_features
        for x, y in zip(feats, labels):
            z = sum(w * xi for w, xi in zip(weights, x))
            z = max(-30.0, min(30.0, z))
            pred = 1.0 / (1.0 + math.exp(-z))
            err = pred - y
            for j in range(n_features):
                grad[j] += err * x[j]
        for j in range(n_features):
            weights[j] -= lr * grad[j] / m

    return LogisticModel(weights=weights)


# Lazily-trained singleton (trained on first use; ~a few ms on the tiny corpus).
_MODEL: LogisticModel | None = None


def _get_model() -> LogisticModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = train()
    return _MODEL


# Public accessor (also lets tests introspect learned weights).
class _ModelProxy:
    @property
    def weights(self) -> list[float]:
        return _get_model().weights

    def predict(self, features: list[float]) -> float:
        return _get_model().predict(features)


MODEL = _ModelProxy()


def score_content(text: str) -> float:
    """Return P(text is a genuine job description) in [0, 1]."""
    return _get_model().predict(extract_features(text))
