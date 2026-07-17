# OpenAPI

The full OpenAPI 3.1 document for the `/v1` surface is published as a
release asset (`openapi.json`) and rendered interactively here.

```{raw} html
<div id="swagger-ui" style="margin-top:1.2rem"></div>

<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui.css" />
<script src="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui-bundle.js" crossorigin></script>
<script src="https://unpkg.com/swagger-ui-dist@5.18.2/swagger-ui-standalone-preset.js" crossorigin></script>
<script>
  window.addEventListener("load", function () {
    const ui = SwaggerUIBundle({
      url: "/_static/openapi.json",
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
      layout: "BaseLayout",
      docExpansion: "list",
      tryItOutEnabled: false,
      defaultModelsExpandDepth: 0,
    });
    window.ui = ui;
  });
</script>
```

## Static download

- [`openapi.json`](../_static/openapi.json)

This file is regenerated on every docs build (and pinned into each
GitHub release as `openapi.json`).

## Code generation

Use it with any OpenAPI-aware generator. For the official TypeScript
SDK, use the repository generator so artifact content responses are
patched to byte buffers:

```bash
# Official TypeScript SDK types
cd sfmapi-sdk/typescript
npm run gen:openapi-types

# Full client
npx @openapitools/openapi-generator-cli generate \
    -i https://sfmapi.github.io/_static/openapi.json \
    -g python -o ./gen-python
```

Third-party TypeScript clients that call `openapi-typescript` directly
should apply the same artifact-content transform used by
`sfmapi-sdk/scripts/regen_from_openapi.py`, otherwise binary media
variants may be emitted as strings instead of byte buffers.

The supported Python and TypeScript SDK surfaces are generated from this
OpenAPI document (`sceneapi_client_gen` and `@sceneapi/client/generated`).
The hand-written compatibility wrappers remain for migration, but the
OpenAPI document is the source of truth either way.
