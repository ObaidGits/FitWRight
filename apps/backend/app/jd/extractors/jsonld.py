"""JSON-LD structured data extractor (§13 of enhancement plan).

Extracts JobPosting schema.org data from <script type="application/ld+json"> tags.
This runs on the already-fetched static HTML - zero additional network cost.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["extract_jsonld"]

VERSION = "1.0.0"


def _flatten_graph(data) -> list[dict]:
    """Handle @graph arrays and nested structures."""
    if isinstance(data, list):
        items = []
        for item in data:
            items.extend(_flatten_graph(item))
        return items
    if isinstance(data, dict):
        if "@graph" in data:
            return _flatten_graph(data["@graph"])
        return [data]
    return []


def _strip_html(html_str: str) -> str:
    """Remove HTML tags from a string."""
    if not html_str:
        return ""
    text = re.sub(r"<[^>]+>", "\n", html_str)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_jsonld(html: str) -> ExtractionResult | None:
    """Extract JobPosting from JSON-LD in static HTML.

    Returns ExtractionResult if a valid JobPosting is found, None otherwise.
    Latency: ~5-20ms (HTML parsing + JSON parsing, no I/O).
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", type="application/ld+json")

    for script in scripts:
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        items = _flatten_graph(data)
        for item in items:
            item_type = item.get("@type", "")
            # Handle both string and list @type
            if isinstance(item_type, list):
                if "JobPosting" not in item_type:
                    continue
            elif item_type != "JobPosting":
                continue

            # Found a JobPosting!
            return _parse_job_posting(item)

    return None


def _parse_job_posting(jp: dict) -> ExtractionResult:
    """Parse a schema.org JobPosting dict into ExtractionResult."""
    title = jp.get("title", "") or jp.get("name", "")
    description_raw = jp.get("description", "")
    description = _strip_html(description_raw)

    # Company
    org = jp.get("hiringOrganization", {})
    if isinstance(org, dict):
        company = org.get("name", "")
    else:
        company = str(org) if org else ""

    # Location
    location_data = jp.get("jobLocation", {})
    location = _extract_location(location_data)

    # Salary
    salary_data = jp.get("baseSalary", {})
    salary = _extract_salary(salary_data)

    # Employment type
    emp_type = jp.get("employmentType", "")
    if isinstance(emp_type, list):
        emp_type = ", ".join(emp_type)

    # Remote
    remote = jp.get("jobLocationType", "")

    # Build full content
    full_content = f"{title}\n\n{description}" if title and description else description or title

    confidence_score = 85
    reasons = ["schema.org JobPosting structured data"]
    if len(description) >= 400:
        confidence_score += 5
        reasons.append("Description length sufficient")
    if title:
        confidence_score = min(95, confidence_score + 5)

    return ExtractionResult(
        content=full_content,
        title=FieldProvenance(
            value=title, source="json_ld", confidence=95,
            extractor_version=VERSION, raw_location="json_ld:$.title",
        ) if title else None,
        company=FieldProvenance(
            value=company, source="json_ld", confidence=90,
            extractor_version=VERSION, raw_location="json_ld:$.hiringOrganization.name",
        ) if company else None,
        location=FieldProvenance(
            value=location, source="json_ld", confidence=85,
            extractor_version=VERSION, raw_location="json_ld:$.jobLocation",
        ) if location else None,
        salary=FieldProvenance(
            value=salary, source="json_ld", confidence=80,
            extractor_version=VERSION, raw_location="json_ld:$.baseSalary",
        ) if salary else None,
        employment_type=FieldProvenance(
            value=emp_type, source="json_ld", confidence=85,
            extractor_version=VERSION, raw_location="json_ld:$.employmentType",
        ) if emp_type else None,
        remote_status=FieldProvenance(
            value=remote, source="json_ld", confidence=80,
            extractor_version=VERSION, raw_location="json_ld:$.jobLocationType",
        ) if remote else None,
        confidence=ConfidenceResult(
            level="HIGH" if confidence_score >= 70 else "MEDIUM",
            score=min(100, confidence_score),
            reasons=reasons,
        ),
        source="json_ld",
    )


def _extract_location(loc) -> str:
    """Extract location string from jobLocation schema."""
    if isinstance(loc, str):
        return loc
    if isinstance(loc, dict):
        address = loc.get("address", {})
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality", ""),
                address.get("addressRegion", ""),
                address.get("addressCountry", ""),
            ]
            return ", ".join(p for p in parts if p)
        return address if isinstance(address, str) else loc.get("name", "")
    if isinstance(loc, list) and loc:
        return _extract_location(loc[0])
    return ""


def _extract_salary(salary) -> str:
    """Extract salary string from baseSalary schema."""
    if isinstance(salary, str):
        return salary
    if isinstance(salary, dict):
        currency = salary.get("currency", "")
        value = salary.get("value", {})
        if isinstance(value, dict):
            min_val = value.get("minValue", "")
            max_val = value.get("maxValue", "")
            unit = value.get("unitText", "YEAR")
            if min_val and max_val:
                return f"{currency} {min_val}-{max_val}/{unit}".strip()
            elif min_val:
                return f"{currency} {min_val}/{unit}".strip()
        elif value:
            return f"{currency} {value}".strip()
    return ""
