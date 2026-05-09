from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.backend_action_run_request import BackendActionRunRequest
from ...models.http_validation_error import HTTPValidationError
from ...models.job_accepted_response import JobAcceptedResponse
from ...types import Response


def _get_kwargs(
    action_id: str,
    *,
    body: BackendActionRunRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/backend/actions/{action_id}:run".format(
            action_id=quote(str(action_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | JobAcceptedResponse | None:
    if response.status_code == 202:
        response_202 = JobAcceptedResponse.from_dict(response.json())

        return response_202

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | JobAcceptedResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    action_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BackendActionRunRequest,
) -> Response[HTTPValidationError | JobAcceptedResponse]:
    """Run Action

     Submit a backend-native action as a normal sfmapi job.

    All execution goes through the existing job/task path, so clients
    use ``GET /v1/jobs/{job_id}``, ``/progress``, cancellation, and SSE
    exactly as they do for standard SfM workflows.

    Args:
        action_id (str):
        body (BackendActionRunRequest): Submit a backend-native action as an sfmapi job.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JobAcceptedResponse]
    """

    kwargs = _get_kwargs(
        action_id=action_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    action_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BackendActionRunRequest,
) -> HTTPValidationError | JobAcceptedResponse | None:
    """Run Action

     Submit a backend-native action as a normal sfmapi job.

    All execution goes through the existing job/task path, so clients
    use ``GET /v1/jobs/{job_id}``, ``/progress``, cancellation, and SSE
    exactly as they do for standard SfM workflows.

    Args:
        action_id (str):
        body (BackendActionRunRequest): Submit a backend-native action as an sfmapi job.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JobAcceptedResponse
    """

    return sync_detailed(
        action_id=action_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    action_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BackendActionRunRequest,
) -> Response[HTTPValidationError | JobAcceptedResponse]:
    """Run Action

     Submit a backend-native action as a normal sfmapi job.

    All execution goes through the existing job/task path, so clients
    use ``GET /v1/jobs/{job_id}``, ``/progress``, cancellation, and SSE
    exactly as they do for standard SfM workflows.

    Args:
        action_id (str):
        body (BackendActionRunRequest): Submit a backend-native action as an sfmapi job.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JobAcceptedResponse]
    """

    kwargs = _get_kwargs(
        action_id=action_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    action_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BackendActionRunRequest,
) -> HTTPValidationError | JobAcceptedResponse | None:
    """Run Action

     Submit a backend-native action as a normal sfmapi job.

    All execution goes through the existing job/task path, so clients
    use ``GET /v1/jobs/{job_id}``, ``/progress``, cancellation, and SSE
    exactly as they do for standard SfM workflows.

    Args:
        action_id (str):
        body (BackendActionRunRequest): Submit a backend-native action as an sfmapi job.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JobAcceptedResponse
    """

    return (
        await asyncio_detailed(
            action_id=action_id,
            client=client,
            body=body,
        )
    ).parsed
