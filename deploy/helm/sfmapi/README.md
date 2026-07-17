# sfmapi Helm chart

Kubernetes-native install of the [sfmapi](https://github.com/sfmapi/sfmapi)
web tier, with optional in-cluster GPU workers and bundled
Postgres/Redis subcharts.

## Quick install

```bash
# Resolve subcharts
helm dependency update deploy/helm/sfmapi

# Install (web + bundled postgres + redis, no GPU worker).
# The chart ships NO default database password — rendering fails
# until you provide one (dev) or an existing Secret (production).
helm install sfmapi ./deploy/helm/sfmapi \
    --namespace sfmapi --create-namespace \
    --set postgresql.auth.password=dev-only-password
```

## Secrets

The chart never templates credentials with defaults. Two hooks:

- **Database password** — create a Secret and point the chart at it.
  The Bitnami postgresql subchart provisions the DB user from the same
  secret, and the web/worker pods build `SCENEAPI_DB_URL` around a
  `valueFrom.secretKeyRef` env var (`SCENEAPI_DB_PASSWORD`, expanded by
  the kubelet via `$(SCENEAPI_DB_PASSWORD)`), so the password never
  appears in the rendered manifests:

  ```bash
  kubectl -n sfmapi create secret generic sfmapi-db \
      --from-literal=password='<app-user-password>' \
      --from-literal=postgres-password='<superuser-password>'
  helm install sfmapi ./deploy/helm/sfmapi -n sfmapi \
      --set postgresql.auth.existingSecret=sfmapi-db
  ```

  The app-user key defaults to `password`; override with
  `postgresql.auth.secretKeys.userPasswordKey`. (`postgres-password`
  is required by the Bitnami subchart for the superuser.)

- **Other `SFMAPI_*` secrets** (external DB/Redis URLs with
  credentials, S3 keys, ...) — put them in a Secret whose keys are
  env-var names and set `env.existingSecret=<name>`; it is injected
  via `envFrom.secretRef` into both web and worker pods. When the
  bundled subcharts are disabled the chart omits `SCENEAPI_DB_URL` /
  `SCENEAPI_REDIS_URL` rather than rendering them empty, so
  secret-provided values take effect.

## Production values

```yaml
# values-prod.yaml
image:
  tag: "v0.1.0"
env:
  authMode: api_key
web:
  replicas: 3
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: api.example.com
        paths: [{ path: /, pathType: Prefix }]
    tls:
      - hosts: [api.example.com]
        secretName: sfmapi-tls
  autoscaling:
    enabled: true
    maxReplicas: 12
worker:
  enabled: true
  image:
    repository: ghcr.io/your-org/sfmapi-worker
    tag: "v0.1.0-cuda12"
  nodeSelector:
    node.sfmapi/gpu: "true"
postgresql:
  enabled: false           # use a managed Postgres
env:
  # Secret with key SCENEAPI_DB_URL=postgresql+psycopg://sfm:...@db.svc.local:5432/sfmapi
  # (plaintext alternative: env.extraEnv.SCENEAPI_DB_URL)
  existingSecret: sfmapi-env
```

```bash
helm upgrade --install sfmapi ./deploy/helm/sfmapi \
    -n sfmapi -f values-prod.yaml
```

## What gets created

| Resource | Always | When |
|---|---|---|
| `Deployment/<rel>-web` | ✓ | always |
| `Service/<rel>-web` | ✓ | always |
| `ServiceAccount` | ✓ | `serviceAccount.create=true` |
| `PersistentVolumeClaim/<rel>-workspaces` | ✓ | `workspace.persistentVolumeClaim.enabled=true` |
| `Ingress/<rel>-web` | | `web.ingress.enabled=true` |
| `HorizontalPodAutoscaler/<rel>-web` | | `web.autoscaling.enabled=true` |
| `DaemonSet/<rel>-worker` | | `worker.enabled=true` |
| `postgresql` (subchart) | ✓ | `postgresql.enabled=true` |
| `redis` (subchart) | ✓ | `redis.enabled=true` |

## GPU worker images

We deliberately do not publish a worker image: the wheel must be
built against your cluster's exact CUDA + cuDSS versions, which the
chart cannot pick for you. Build one off `colmap_mod` and reference
it via `worker.image.repository`. See
https://sfmapi.github.io/guides/deployment for a worker-image
Dockerfile template.

## Linting

A database password (or existingSecret) is mandatory, so lint/template
with one set — rendering with bare defaults fails by design:

```bash
helm lint deploy/helm/sfmapi --set postgresql.auth.password=x
helm template release-name deploy/helm/sfmapi \
    --set postgresql.auth.existingSecret=sfmapi-db --debug \
  | kubectl apply --dry-run=client -f -
```

## Workspace storage

The chart provisions a single PVC at `/workspaces` shared by every
web pod and (when enabled) every worker pod on every node. **Pick a
ReadWriteMany-capable StorageClass** (NFS, CephFS, EFS, Filestore).
The default `ReadWriteOnce` works for single-replica dev installs.
