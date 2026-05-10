from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.page_artifact_kind_out import PageArtifactKindOut
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/artifacts/kinds",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> PageArtifactKindOut | None:
    if response.status_code == 200:
        response_200 = PageArtifactKindOut.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[PageArtifactKindOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[PageArtifactKindOut]:
    """List Artifact Kinds

     List sfmapi's reserved core artifact kinds.

    Backends may still emit namespaced extension kinds. The core list
    gives clients stable semantics for portable stage inputs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PageArtifactKindOut]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> PageArtifactKindOut | None:
    """List Artifact Kinds

     List sfmapi's reserved core artifact kinds.

    Backends may still emit namespaced extension kinds. The core list
    gives clients stable semantics for portable stage inputs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PageArtifactKindOut
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[PageArtifactKindOut]:
    """List Artifact Kinds

     List sfmapi's reserved core artifact kinds.

    Backends may still emit namespaced extension kinds. The core list
    gives clients stable semantics for portable stage inputs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[PageArtifactKindOut]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> PageArtifactKindOut | None:
    """List Artifact Kinds

     List sfmapi's reserved core artifact kinds.

    Backends may still emit namespaced extension kinds. The core list
    gives clients stable semantics for portable stage inputs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        PageArtifactKindOut
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
