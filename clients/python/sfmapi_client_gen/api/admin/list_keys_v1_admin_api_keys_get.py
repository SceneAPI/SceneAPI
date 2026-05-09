from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.api_key_out import ApiKeyOut
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/admin/api-keys",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> list[ApiKeyOut] | None:
    if response.status_code == 200:
        response_200 = []
        _response_200 = response.json()
        for response_200_item_data in _response_200:
            response_200_item = ApiKeyOut.from_dict(response_200_item_data)

            response_200.append(response_200_item)

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[list[ApiKeyOut]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[list[ApiKeyOut]]:
    """List Keys

     List every API key on file (active + revoked).

    Raw-key material is NEVER returned; use :func:`issue` and capture
    the value at creation time. Ordered by ``created_at`` ascending.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[list[ApiKeyOut]]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> list[ApiKeyOut] | None:
    """List Keys

     List every API key on file (active + revoked).

    Raw-key material is NEVER returned; use :func:`issue` and capture
    the value at creation time. Ordered by ``created_at`` ascending.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        list[ApiKeyOut]
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[list[ApiKeyOut]]:
    """List Keys

     List every API key on file (active + revoked).

    Raw-key material is NEVER returned; use :func:`issue` and capture
    the value at creation time. Ordered by ``created_at`` ascending.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[list[ApiKeyOut]]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> list[ApiKeyOut] | None:
    """List Keys

     List every API key on file (active + revoked).

    Raw-key material is NEVER returned; use :func:`issue` and capture
    the value at creation time. Ordered by ``created_at`` ascending.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        list[ApiKeyOut]
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
