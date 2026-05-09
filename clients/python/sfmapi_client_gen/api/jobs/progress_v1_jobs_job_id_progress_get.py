from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.job_progress_out import JobProgressOut
from ...types import Response


def _get_kwargs(
    job_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/jobs/{job_id}/progress".format(
            job_id=quote(str(job_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | JobProgressOut | None:
    if response.status_code == 200:
        response_200 = JobProgressOut.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | JobProgressOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | JobProgressOut]:
    """Progress

     Return a compact progress snapshot for a job.

    This is the polling counterpart to ``GET /v1/jobs/{job_id}/events``.
    It always works from durable state: task lifecycle rows plus the
    latest persisted progress events. ``progress`` is a best-effort
    fraction, so clients should treat it as UI telemetry rather than a
    scheduling guarantee.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JobProgressOut]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | JobProgressOut | None:
    """Progress

     Return a compact progress snapshot for a job.

    This is the polling counterpart to ``GET /v1/jobs/{job_id}/events``.
    It always works from durable state: task lifecycle rows plus the
    latest persisted progress events. ``progress`` is a best-effort
    fraction, so clients should treat it as UI telemetry rather than a
    scheduling guarantee.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JobProgressOut
    """

    return sync_detailed(
        job_id=job_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | JobProgressOut]:
    """Progress

     Return a compact progress snapshot for a job.

    This is the polling counterpart to ``GET /v1/jobs/{job_id}/events``.
    It always works from durable state: task lifecycle rows plus the
    latest persisted progress events. ``progress`` is a best-effort
    fraction, so clients should treat it as UI telemetry rather than a
    scheduling guarantee.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JobProgressOut]
    """

    kwargs = _get_kwargs(
        job_id=job_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    job_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | JobProgressOut | None:
    """Progress

     Return a compact progress snapshot for a job.

    This is the polling counterpart to ``GET /v1/jobs/{job_id}/events``.
    It always works from durable state: task lifecycle rows plus the
    latest persisted progress events. ``progress`` is a best-effort
    fraction, so clients should treat it as UI telemetry rather than a
    scheduling guarantee.

    Args:
        job_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JobProgressOut
    """

    return (
        await asyncio_detailed(
            job_id=job_id,
            client=client,
        )
    ).parsed
