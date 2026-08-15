"""Refuse an input that is too large BEFORE the call (task 6.1).

The reserve is sized from a typical request. A user who pastes a 300-page PDF does not
break the accounting - the settle caps the charge at what was held - but that is
precisely the problem: the OPERATOR absorbs the overrun, and one user can do it
repeatedly for free. The hold is a promise about the user's balance, not a limit on the
provider's invoice.

Refusing early is also better for the user. A 400 that says "this is too long, here is
the limit" arrives in milliseconds; the alternative is a minute of waiting, a truncated
or failed generation, and no explanation.

The ceilings are per feature because the features are not comparable: a resume upload
is legitimately a whole document, while an outreach message is a paragraph. One global
number would either block real resumes or permit abuse everywhere else.
"""

from __future__ import annotations

import logging

from app.errors import ApiError

logger = logging.getLogger(__name__)

__all__ = ["InputTooLarge", "check_input_size", "estimate_tokens_for_text"]

#: Roughly 4 characters per token for English prose. Deliberately a rough constant
#: rather than a real tokeniser: this is a guard rail, and loading a tokeniser on the
#: request path to make a limit 3% more accurate would cost more than it saves.
_CHARS_PER_TOKEN = 4

#: Generous ceilings in INPUT TOKENS. Sized several times above the realistic worst
#: case for each feature, because the purpose is to stop abuse and accidents, not to
#: police long CVs. A user hitting one of these has almost certainly pasted the wrong
#: thing.
FEATURE_INPUT_CEILINGS: dict[str, int] = {
    "resume_parse": 60_000,      # a long CV, plus a generous margin
    "resume_tailor": 40_000,     # resume + job description
    "resume_wizard": 20_000,
    "cover_letter": 30_000,
    "outreach": 20_000,
    "interview_prep": 40_000,
    "enrichment": 20_000,
    "jd_extract": 30_000,
    "discovery_recommend": 40_000,
    "extension_draft": 20_000,
    "match_score": 30_000,
}

#: Applied to a feature with no explicit entry, so a NEW feature is covered the day it
#: ships rather than the day someone remembers to add it here.
_DEFAULT_CEILING = 30_000


class InputTooLarge(ApiError):
    """The submitted text exceeds this feature's ceiling.

    A 413 rather than a 400: the request is well-formed, it is the payload that is too
    large, and the distinction matters to anyone reading logs later.
    """

    def __init__(self, *, feature: str, estimated_tokens: int, ceiling: int):
        super().__init__(
            413,
            "input_too_large",
            "This is too long to process in one go. Try trimming it, or splitting it "
            "into smaller pieces.",
            details={
                "feature": feature,
                "estimated_tokens": estimated_tokens,
                "limit_tokens": ceiling,
            },
        )


def estimate_tokens_for_text(*parts: str | None) -> int:
    """Rough input-token estimate across several text fields."""
    total_chars = sum(len(p or "") for p in parts)
    return total_chars // _CHARS_PER_TOKEN


def ceiling_for(feature: str) -> int:
    return FEATURE_INPUT_CEILINGS.get(feature, _DEFAULT_CEILING)


def check_input_size(feature: str, *parts: str | None) -> int:
    """Raise :class:`InputTooLarge` if these inputs exceed the feature's ceiling.

    Returns the estimate when it passes, so a caller can log or reuse it.
    """
    estimated = estimate_tokens_for_text(*parts)
    ceiling = ceiling_for(feature)
    if estimated > ceiling:
        logger.info(
            "Refused oversized input for %s: ~%d tokens against a %d ceiling",
            feature,
            estimated,
            ceiling,
        )
        raise InputTooLarge(
            feature=feature, estimated_tokens=estimated, ceiling=ceiling
        )
    return estimated
