"""Prompt template for Job Discovery search-query generation (design §5, Req 2).

``query.py`` feeds the candidate's resume text into these templates and asks the
model for a compact JSON object describing the search intent. The system prompt
pins the output contract; the user prompt carries the resume and any location
hint. Both are plain ``str.format`` templates -- keep every literal ``{`` / ``}``
in the JSON example escaped as ``{{`` / ``}}``.
"""

from __future__ import annotations

# System prompt: fixes the output contract so the model returns only the JSON
# object the parser in query.py expects (titles / search_string / seniority).
DISCOVERY_QUERY_SYSTEM_PROMPT = (
    "You are a job-search query planner. Given a candidate's resume, infer the "
    "roles they are best suited for and a keyword search string a job board "
    "(Indeed, Naukri, LinkedIn) would understand. Respond with valid JSON only "
    "-- no prose, no markdown."
)

# User prompt: the resume text plus an optional location hint. The JSON example
# doubles as the schema contract the deterministic parser validates against.
DISCOVERY_QUERY_PROMPT = """Infer job-search intent from this resume.

RESUME:
{resume_text}

LOCATION HINT (may be empty): {location}

Return ONLY a JSON object with exactly these keys:
{{
  "titles": ["1 to 3 concise job titles the candidate should target"],
  "search_string": "a boolean-style keyword string for a job board",
  "seniority": "one of intern|junior|mid|senior|lead|principal|staff, or null"
}}

Rules:
- "titles": 1-3 items, most relevant first, no duplicates, no seniority words.
- "search_string": combine the target titles with the candidate's strongest
  skills; keep it under ~160 characters.
- "seniority": infer from years of experience and titles; use null if unclear.
- Do NOT invent skills or titles the resume does not support.
- Output the JSON object only. Start with {{ and end with }}."""

__all__ = [
    "DISCOVERY_QUERY_PROMPT",
    "DISCOVERY_QUERY_SYSTEM_PROMPT",
]
