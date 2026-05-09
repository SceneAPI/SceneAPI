from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.job_accepted_response import JobAcceptedResponse
from ...models.pipeline_request import PipelineRequest
from ...models.run_recipe_v1_projects_project_id_pipelines_recipe_post_recipe import (
    RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe,
)
from ...types import Response


def _get_kwargs(
    project_id: str,
    recipe: RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe,
    *,
    body: PipelineRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/projects/{project_id}/pipelines/{recipe}".format(
            project_id=quote(str(project_id), safe=""),
            recipe=quote(str(recipe), safe=""),
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
    project_id: str,
    recipe: RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe,
    *,
    client: AuthenticatedClient | Client,
    body: PipelineRequest,
) -> Response[HTTPValidationError | JobAcceptedResponse]:
    """Run Recipe

     Run an end-to-end mapping recipe in one POST.

    Composes ``features -> matches -> verify -> map -> ba -> ...``
    into a single job DAG keyed on ``recipe`` (one of ``incremental``
    | ``global`` | ``hierarchical`` | ``spherical``). The recipe MUST
    match ``body.spec.kind`` — 422 ``ValidationError`` if not. Each
    stage spec keeps optional provider selectors
    so mixed deployments can route hloc and COLMAP implementations
    behind the same portable capability names. Each backend advertises
    which recipes it implements via the
    ``pipelines.{kind}`` capability flags; unsupported recipes
    return ``501 capability_unavailable``. Returns 202 + a
    ``Location`` header pointing at the parent job.

    Args:
        project_id (str):
        recipe (RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe):
        body (PipelineRequest): End-to-end pipeline request — features + pair selection +
            matcher + two-view verification + mapping spec, sent in one body
            for the recipe routes (``/pipelines/{incremental|global|...}``).

            Pair selection (``pairs``) and per-pair matching (``matcher``) are
            independent shapes (AIP-202).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JobAcceptedResponse]
    """

    kwargs = _get_kwargs(
        project_id=project_id,
        recipe=recipe,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    project_id: str,
    recipe: RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe,
    *,
    client: AuthenticatedClient | Client,
    body: PipelineRequest,
) -> HTTPValidationError | JobAcceptedResponse | None:
    """Run Recipe

     Run an end-to-end mapping recipe in one POST.

    Composes ``features -> matches -> verify -> map -> ba -> ...``
    into a single job DAG keyed on ``recipe`` (one of ``incremental``
    | ``global`` | ``hierarchical`` | ``spherical``). The recipe MUST
    match ``body.spec.kind`` — 422 ``ValidationError`` if not. Each
    stage spec keeps optional provider selectors
    so mixed deployments can route hloc and COLMAP implementations
    behind the same portable capability names. Each backend advertises
    which recipes it implements via the
    ``pipelines.{kind}`` capability flags; unsupported recipes
    return ``501 capability_unavailable``. Returns 202 + a
    ``Location`` header pointing at the parent job.

    Args:
        project_id (str):
        recipe (RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe):
        body (PipelineRequest): End-to-end pipeline request — features + pair selection +
            matcher + two-view verification + mapping spec, sent in one body
            for the recipe routes (``/pipelines/{incremental|global|...}``).

            Pair selection (``pairs``) and per-pair matching (``matcher``) are
            independent shapes (AIP-202).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JobAcceptedResponse
    """

    return sync_detailed(
        project_id=project_id,
        recipe=recipe,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    project_id: str,
    recipe: RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe,
    *,
    client: AuthenticatedClient | Client,
    body: PipelineRequest,
) -> Response[HTTPValidationError | JobAcceptedResponse]:
    """Run Recipe

     Run an end-to-end mapping recipe in one POST.

    Composes ``features -> matches -> verify -> map -> ba -> ...``
    into a single job DAG keyed on ``recipe`` (one of ``incremental``
    | ``global`` | ``hierarchical`` | ``spherical``). The recipe MUST
    match ``body.spec.kind`` — 422 ``ValidationError`` if not. Each
    stage spec keeps optional provider selectors
    so mixed deployments can route hloc and COLMAP implementations
    behind the same portable capability names. Each backend advertises
    which recipes it implements via the
    ``pipelines.{kind}`` capability flags; unsupported recipes
    return ``501 capability_unavailable``. Returns 202 + a
    ``Location`` header pointing at the parent job.

    Args:
        project_id (str):
        recipe (RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe):
        body (PipelineRequest): End-to-end pipeline request — features + pair selection +
            matcher + two-view verification + mapping spec, sent in one body
            for the recipe routes (``/pipelines/{incremental|global|...}``).

            Pair selection (``pairs``) and per-pair matching (``matcher``) are
            independent shapes (AIP-202).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JobAcceptedResponse]
    """

    kwargs = _get_kwargs(
        project_id=project_id,
        recipe=recipe,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    project_id: str,
    recipe: RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe,
    *,
    client: AuthenticatedClient | Client,
    body: PipelineRequest,
) -> HTTPValidationError | JobAcceptedResponse | None:
    """Run Recipe

     Run an end-to-end mapping recipe in one POST.

    Composes ``features -> matches -> verify -> map -> ba -> ...``
    into a single job DAG keyed on ``recipe`` (one of ``incremental``
    | ``global`` | ``hierarchical`` | ``spherical``). The recipe MUST
    match ``body.spec.kind`` — 422 ``ValidationError`` if not. Each
    stage spec keeps optional provider selectors
    so mixed deployments can route hloc and COLMAP implementations
    behind the same portable capability names. Each backend advertises
    which recipes it implements via the
    ``pipelines.{kind}`` capability flags; unsupported recipes
    return ``501 capability_unavailable``. Returns 202 + a
    ``Location`` header pointing at the parent job.

    Args:
        project_id (str):
        recipe (RunRecipeV1ProjectsProjectIdPipelinesRecipePostRecipe):
        body (PipelineRequest): End-to-end pipeline request — features + pair selection +
            matcher + two-view verification + mapping spec, sent in one body
            for the recipe routes (``/pipelines/{incremental|global|...}``).

            Pair selection (``pairs``) and per-pair matching (``matcher``) are
            independent shapes (AIP-202).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JobAcceptedResponse
    """

    return (
        await asyncio_detailed(
            project_id=project_id,
            recipe=recipe,
            client=client,
            body=body,
        )
    ).parsed
