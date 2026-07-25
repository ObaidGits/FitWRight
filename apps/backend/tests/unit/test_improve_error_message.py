"""The tailoring error message must name the REAL cause.

An ``llm_response_invalid`` failure during tailoring should read as a model
capability problem (pick another model), not a vague "invalid response" that
users mistake for a platform fault. Other causes keep their standard messages.
"""

from app.routers.resumes import _improve_api_error


def test_invalid_structured_output_gets_clear_model_focused_message():
    err = _improve_api_error(ValueError("malformed model output"), stage="keywords")
    assert err.code == "llm_response_invalid"
    assert err.status_code == 422
    # Model-focused + actionable, and explicitly not-the-app.
    msg = err.message.lower()
    assert "model" in msg
    assert "structured" in msg
    assert "another model" in msg or "different model" in msg
    assert err.details.get("reason") == "model_structured_output"
    assert err.details.get("stage") == "keywords"


def test_non_content_error_keeps_standard_message():
    # A timeout is not a structured-output problem -> standard classified error.
    err = _improve_api_error(TimeoutError(), stage="rewrite")
    assert err.code == "llm_timeout"
    assert err.details.get("reason") != "model_structured_output"


def test_auth_error_keeps_standard_message():
    import litellm

    exc = litellm.AuthenticationError(
        message="bad key", llm_provider="openai", model="gpt-4"
    )
    err = _improve_api_error(exc, stage="rewrite")
    assert err.code == "llm_authentication_failed"
