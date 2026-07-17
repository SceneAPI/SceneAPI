"""BadRequestError shape + 400 mapping."""

from __future__ import annotations

import pytest

from sceneapi.server.core.errors import BadRequestError, SfmApiError, ValidationError

pytestmark = pytest.mark.unit


def test_bad_request_is_400() -> None:
    err = BadRequestError("malformed Content-Range header")
    assert err.status_code == 400
    assert err.error_type == "bad_request"


def test_bad_request_distinct_from_validation_error() -> None:
    """ValidationError (422) is for shape-valid but semantically wrong
    inputs; BadRequestError (400) is for inputs that couldn't be
    parsed at all."""
    assert ValidationError.status_code == 422
    assert BadRequestError.status_code == 400
    assert ValidationError.error_type != BadRequestError.error_type


def test_bad_request_inherits_problem_json_shape() -> None:
    err = BadRequestError("bad", extra_field="value")
    body = err.as_problem(instance="/v1/uploads")
    assert body["status"] == 400
    assert body["title"] == "Bad request"
    assert body["detail"] == "bad"
    assert body["instance"] == "/v1/uploads"
    assert body["extra_field"] == "value"
    assert body["type"].endswith("/bad_request")


def test_bad_request_is_sfm_api_error_subclass() -> None:
    """Existing handlers catch SfmApiError; BadRequest must be caught."""
    assert issubclass(BadRequestError, SfmApiError)
