# Error Model

All errors follow [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807)
`application/problem+json`:

```json
{
  "type": "https://sfmapi/errors/conflict",
  "title": "Conflict",
  "status": 409,
  "detail": "Project 'foo' already exists",
  "instance": "/v1/projects"
}
```

## Status Code Mapping

| HTTP | Server error class | Caller handling |
|---|---|---|
| 403 | `TenantViolationError` | Treat as an auth or tenancy failure. |
| 404 | `NotFoundError` | Resource does not exist in the current tenant scope. |
| 409 | `ConflictError` | Retry only after changing the request or idempotency key. |
| 413 | `QuotaExceededError` (storage) | Reduce upload size or increase the storage quota. |
| 422 | `ValidationError` | Fix request fields using the structured `errors[]` details. |
| 429 | `QuotaExceededError` (rate / GPU seconds) | Back off or reduce concurrent work. |
| 503 | Backend unavailable error | Retry when the selected backend/provider is available. |
| 507 | `StorageError` | Free capacity or move to a larger storage backend. |

## Server-Side Hierarchy

```{eval-rst}
.. automodule:: app.core.errors
   :members:
   :no-index:
```

Client packages map this RFC 7807 envelope into their own exception
types. Keep SDK-specific exception imports in the `sfmapi-sdk`
repository so this server reference stays focused on the wire contract.
