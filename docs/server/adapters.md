# Public Backend API

Backend packages should import this public surface, not the internal
`sfmapi.server.adapters` modules. sfmapi ships **no real backend**. It provides
layered backend protocols, registry hooks, contract checkers, and a
no-op stub for tests / ephemeral demos.

Action-only packages can satisfy the minimal `Backend` protocol and
expose native tools through backend actions. Complete engines can
satisfy `SfmBackend` and implement the full portable feature, match,
mapping, refinement, and export surface.

```{eval-rst}
.. automodule:: sfmapi.backends
   :members:
   :no-index:

.. automodule:: sfmapi.runtime
   :members:
   :no-index:

.. automodule:: sfmapi.errors
   :members:
   :no-index:

.. automodule:: sfmapi.server.adapters.stub_backend
   :members:
   :no-index:
```
