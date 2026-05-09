from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.api_key_out import ApiKeyOut
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    api_key_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/v1/admin/api-keys/{api_key_id}".format(
            api_key_id=quote(str(api_key_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ApiKeyOut | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ApiKeyOut.from_dict(response.json())

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
) -> Response[ApiKeyOut | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    api_key_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ApiKeyOut | HTTPValidationError]:
    """Revoke

     Revoke a previously-issued API key.

    Soft-delete: the row stays for audit, ``revoked_at`` is stamped
    and ``revoked=true`` shipped on the next read. Subsequent auth
    attempts with that key will fail. Idempotent; revoking an
    already-revoked key is a 200 no-op.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Args:
        api_key_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ApiKeyOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        api_key_id=api_key_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    api_key_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ApiKeyOut | HTTPValidationError | None:
    """Revoke

     Revoke a previously-issued API key.

    Soft-delete: the row stays for audit, ``revoked_at`` is stamped
    and ``revoked=true`` shipped on the next read. Subsequent auth
    attempts with that key will fail. Idempotent; revoking an
    already-revoked key is a 200 no-op.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Args:
        api_key_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ApiKeyOut | HTTPValidationError
    """

    return sync_detailed(
        api_key_id=api_key_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    api_key_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ApiKeyOut | HTTPValidationError]:
    """Revoke

     Revoke a previously-issued API key.

    Soft-delete: the row stays for audit, ``revoked_at`` is stamped
    and ``revoked=true`` shipped on the next read. Subsequent auth
    attempts with that key will fail. Idempotent; revoking an
    already-revoked key is a 200 no-op.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Args:
        api_key_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ApiKeyOut | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        api_key_id=api_key_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    api_key_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ApiKeyOut | HTTPValidationError | None:
    """Revoke

     Revoke a previously-issued API key.

    Soft-delete: the row stays for audit, ``revoked_at`` is stamped
    and ``revoked=true`` shipped on the next read. Subsequent auth
    attempts with that key will fail. Idempotent; revoking an
    already-revoked key is a 200 no-op.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Args:
        api_key_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ApiKeyOut | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            api_key_id=api_key_id,
            client=client,
        )
    ).parsed
