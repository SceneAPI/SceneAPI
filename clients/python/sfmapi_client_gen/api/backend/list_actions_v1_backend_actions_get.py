from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.page_backend_action_out import PageBackendActionOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 50,
    include_schemas: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params["page_size"] = page_size

    params["include_schemas"] = include_schemas

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/backend/actions",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PageBackendActionOut | None:
    if response.status_code == 200:
        response_200 = PageBackendActionOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | PageBackendActionOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 50,
    include_schemas: bool | Unset = False,
) -> Response[HTTPValidationError | PageBackendActionOut]:
    """List Actions

     List backend-native extension actions.

    This is the generic discovery layer for COLMAP commands and future
    backend-specific tools. Portable sfmapi features still belong in
    ``GET /v1/capabilities``; this catalog is intentionally namespaced
    and backend-specific.

    Args:
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 50.
        include_schemas (bool | Unset): Include each action's input/output schema in the list
            response. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageBackendActionOut]
    """

    kwargs = _get_kwargs(
        page_token=page_token,
        page_size=page_size,
        include_schemas=include_schemas,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 50,
    include_schemas: bool | Unset = False,
) -> HTTPValidationError | PageBackendActionOut | None:
    """List Actions

     List backend-native extension actions.

    This is the generic discovery layer for COLMAP commands and future
    backend-specific tools. Portable sfmapi features still belong in
    ``GET /v1/capabilities``; this catalog is intentionally namespaced
    and backend-specific.

    Args:
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 50.
        include_schemas (bool | Unset): Include each action's input/output schema in the list
            response. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageBackendActionOut
    """

    return sync_detailed(
        client=client,
        page_token=page_token,
        page_size=page_size,
        include_schemas=include_schemas,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 50,
    include_schemas: bool | Unset = False,
) -> Response[HTTPValidationError | PageBackendActionOut]:
    """List Actions

     List backend-native extension actions.

    This is the generic discovery layer for COLMAP commands and future
    backend-specific tools. Portable sfmapi features still belong in
    ``GET /v1/capabilities``; this catalog is intentionally namespaced
    and backend-specific.

    Args:
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 50.
        include_schemas (bool | Unset): Include each action's input/output schema in the list
            response. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageBackendActionOut]
    """

    kwargs = _get_kwargs(
        page_token=page_token,
        page_size=page_size,
        include_schemas=include_schemas,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 50,
    include_schemas: bool | Unset = False,
) -> HTTPValidationError | PageBackendActionOut | None:
    """List Actions

     List backend-native extension actions.

    This is the generic discovery layer for COLMAP commands and future
    backend-specific tools. Portable sfmapi features still belong in
    ``GET /v1/capabilities``; this catalog is intentionally namespaced
    and backend-specific.

    Args:
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 50.
        include_schemas (bool | Unset): Include each action's input/output schema in the list
            response. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageBackendActionOut
    """

    return (
        await asyncio_detailed(
            client=client,
            page_token=page_token,
            page_size=page_size,
            include_schemas=include_schemas,
        )
    ).parsed
