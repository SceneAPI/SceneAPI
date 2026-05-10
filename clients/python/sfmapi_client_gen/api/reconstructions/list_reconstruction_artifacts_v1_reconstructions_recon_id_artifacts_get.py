from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.page_stage_artifact_out import PageStageArtifactOut
from ...types import UNSET, Response, Unset


def _get_kwargs(
    recon_id: str,
    *,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 100,
    kind: None | str | Unset = UNSET,
    task_id: None | str | Unset = UNSET,
    name: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params["page_size"] = page_size

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    else:
        json_kind = kind
    params["kind"] = json_kind

    json_task_id: None | str | Unset
    if isinstance(task_id, Unset):
        json_task_id = UNSET
    else:
        json_task_id = task_id
    params["task_id"] = json_task_id

    json_name: None | str | Unset
    if isinstance(name, Unset):
        json_name = UNSET
    else:
        json_name = name
    params["name"] = json_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/reconstructions/{recon_id}/artifacts".format(
            recon_id=quote(str(recon_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | PageStageArtifactOut | None:
    if response.status_code == 200:
        response_200 = PageStageArtifactOut.from_dict(response.json())

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
) -> Response[HTTPValidationError | PageStageArtifactOut]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    recon_id: str,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 100,
    kind: None | str | Unset = UNSET,
    task_id: None | str | Unset = UNSET,
    name: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PageStageArtifactOut]:
    """List Reconstruction Artifacts

     List all typed stage artifacts attached to a reconstruction.

    Use this when a pipeline produces multiple candidate outputs, such
    as dual matchers, alternate verified pair sets, or several mapping
    components.

    Args:
        recon_id (str):
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 100.
        kind (None | str | Unset): Optional exact artifact kind filter, e.g.
            reconstruction.snapshot.
        task_id (None | str | Unset): Optional producing task id filter.
        name (None | str | Unset): Optional exact artifact name filter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageStageArtifactOut]
    """

    kwargs = _get_kwargs(
        recon_id=recon_id,
        page_token=page_token,
        page_size=page_size,
        kind=kind,
        task_id=task_id,
        name=name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    recon_id: str,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 100,
    kind: None | str | Unset = UNSET,
    task_id: None | str | Unset = UNSET,
    name: None | str | Unset = UNSET,
) -> HTTPValidationError | PageStageArtifactOut | None:
    """List Reconstruction Artifacts

     List all typed stage artifacts attached to a reconstruction.

    Use this when a pipeline produces multiple candidate outputs, such
    as dual matchers, alternate verified pair sets, or several mapping
    components.

    Args:
        recon_id (str):
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 100.
        kind (None | str | Unset): Optional exact artifact kind filter, e.g.
            reconstruction.snapshot.
        task_id (None | str | Unset): Optional producing task id filter.
        name (None | str | Unset): Optional exact artifact name filter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageStageArtifactOut
    """

    return sync_detailed(
        recon_id=recon_id,
        client=client,
        page_token=page_token,
        page_size=page_size,
        kind=kind,
        task_id=task_id,
        name=name,
    ).parsed


async def asyncio_detailed(
    recon_id: str,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 100,
    kind: None | str | Unset = UNSET,
    task_id: None | str | Unset = UNSET,
    name: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | PageStageArtifactOut]:
    """List Reconstruction Artifacts

     List all typed stage artifacts attached to a reconstruction.

    Use this when a pipeline produces multiple candidate outputs, such
    as dual matchers, alternate verified pair sets, or several mapping
    components.

    Args:
        recon_id (str):
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 100.
        kind (None | str | Unset): Optional exact artifact kind filter, e.g.
            reconstruction.snapshot.
        task_id (None | str | Unset): Optional producing task id filter.
        name (None | str | Unset): Optional exact artifact name filter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | PageStageArtifactOut]
    """

    kwargs = _get_kwargs(
        recon_id=recon_id,
        page_token=page_token,
        page_size=page_size,
        kind=kind,
        task_id=task_id,
        name=name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    recon_id: str,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    page_size: int | Unset = 100,
    kind: None | str | Unset = UNSET,
    task_id: None | str | Unset = UNSET,
    name: None | str | Unset = UNSET,
) -> HTTPValidationError | PageStageArtifactOut | None:
    """List Reconstruction Artifacts

     List all typed stage artifacts attached to a reconstruction.

    Use this when a pipeline produces multiple candidate outputs, such
    as dual matchers, alternate verified pair sets, or several mapping
    components.

    Args:
        recon_id (str):
        page_token (None | str | Unset):
        page_size (int | Unset):  Default: 100.
        kind (None | str | Unset): Optional exact artifact kind filter, e.g.
            reconstruction.snapshot.
        task_id (None | str | Unset): Optional producing task id filter.
        name (None | str | Unset): Optional exact artifact name filter.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | PageStageArtifactOut
    """

    return (
        await asyncio_detailed(
            recon_id=recon_id,
            client=client,
            page_token=page_token,
            page_size=page_size,
            kind=kind,
            task_id=task_id,
            name=name,
        )
    ).parsed
