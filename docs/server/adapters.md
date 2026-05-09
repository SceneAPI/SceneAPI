# `app.adapters`

The boundary layer between sfmapi and any concrete SfM engine.
sfmapi ships **no real backend** — only the ``SfmBackend`` Protocol
that engine packages must satisfy, the registry that wires them in,
and a no-op stub for tests / ephemeral demos.

```{eval-rst}
.. automodule:: app.adapters.backend
   :members:
   :no-index:

.. automodule:: app.adapters.registry
   :members:
   :no-index:

.. automodule:: app.adapters.progress
   :members:
   :no-index:

.. automodule:: app.adapters.backend_actions
   :members:
   :no-index:

.. automodule:: app.adapters.backend_config
   :members:
   :no-index:

.. automodule:: app.adapters.backend_contract
   :members:
   :no-index:

.. automodule:: app.adapters.stub_backend
   :members:
   :no-index:
```
