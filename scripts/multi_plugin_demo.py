"""End-to-end demonstration: COLMAP CLI + RealityScan CLI loaded as two
plugins in the SAME running sfmapi instance.

Boots the app in ephemeral mode (which auto-discovers sceneapi.backends
entry points), then verifies the multi-plugin invariants on every
surface: the in-process registry, the sfm_hub routing layer, the HTTP
discovery endpoints, per-provider job submission, and capability
detection.

Run with::

    uv pip install -e ../sfmapi_colmap_cli -e ../sfmapi_realityscan
    SCENEAPI_EPHEMERAL=true uv run python scripts/multi_plugin_demo.py

``SCENEAPI_AUTO_LOAD_BACKEND_PLUGINS`` defaults to true, so ``pip install``
of any ``sceneapi.backends`` entry-point plugin is enough — no separate
opt-in flag is needed.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Force ephemeral mode for self-contained boot + plugin discovery.
os.environ.setdefault("SCENEAPI_EPHEMERAL", "true")

from httpx import ASGITransport, AsyncClient

from sceneapi.server.main import create_app


def heading(title: str) -> None:
    print(f"\n=== {title} ===")


async def main() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        # ---- Layer 1: the in-process registry sees both plugins.
        from sceneapi.server.adapters.registry import list_backend_providers, list_backends

        heading("Layer 1 — in-process registry")
        backends = list_backends()
        providers = list_backend_providers()
        print(f"registered backends ({len(backends)}): {backends}")
        print(f"registered providers ({len(providers)}): {providers}")
        assert "colmap_cli" in providers, "colmap_cli provider not registered"
        assert "realityscan_cli" in providers, "realityscan_cli provider not registered"

        # ---- Layer 2: get_backend(provider=...) routes to distinct backends.
        from sceneapi.server.adapters.registry import get_backend

        heading("Layer 2 — per-provider resolution")
        c = get_backend(provider="colmap_cli")
        r = get_backend(provider="realityscan_cli")
        print(f"provider=colmap_cli       -> {type(c).__name__} (name={c.name!r})")
        print(f"provider=realityscan_cli  -> {type(r).__name__} (name={r.name!r})")
        assert type(c) is not type(r), "two providers resolved to the same backend class"
        assert c.name != r.name, "two providers resolved to backends with the same name"

        # ---- Layer 3: each backend advertises its own capability vocabulary.
        heading("Layer 3 — per-provider capabilities")
        c_caps = c.capabilities()
        r_caps = r.capabilities()
        print(f"colmap_cli      caps ({len(c_caps)}): {sorted(c_caps)[:6]}...")
        print(f"realityscan_cli caps ({len(r_caps)}): {sorted(r_caps) or '(action-only plugin)'}")
        # The two plugins are intentionally complementary — colmap_cli
        # owns the portable SfM stages; realityscan_cli is action-catalog
        # only. Asserting that distinction proves they did not collapse.
        assert "features.extract.sift" in c_caps
        assert "features.extract.sift" not in r_caps

        # ---- Layer 4: HTTP discovery endpoints surface BOTH plugins.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://x") as client:
            heading("Layer 4 — HTTP discovery (/v1/admin/plugins/entry-points)")
            r1 = await client.get("/v1/admin/plugins/entry-points")
            assert r1.status_code == 200, r1.text
            ep_ids = sorted(item["plugin_id"] for item in r1.json()["items"])
            print(f"discovered entry points ({len(ep_ids)}): {ep_ids}")
            assert "colmap_cli" in ep_ids
            assert "realityscan_cli" in ep_ids

            heading("Layer 5 — HTTP discovery (/v1/admin/plugins)")
            r2 = await client.get("/v1/admin/plugins")
            assert r2.status_code == 200, r2.text
            plugin_ids = sorted(item["plugin_id"] for item in r2.json()["items"])
            colmap_row = next(it for it in r2.json()["items"] if it["plugin_id"] == "colmap_cli")
            rc_row = next(it for it in r2.json()["items"] if it["plugin_id"] == "realityscan_cli")
            print(f"registry plugins ({len(plugin_ids)}): {plugin_ids}")
            print(
                f"  colmap_cli:      installed={colmap_row['installed']} enabled={colmap_row['enabled']}"
            )
            print(f"  realityscan_cli: installed={rc_row['installed']} enabled={rc_row['enabled']}")
            assert colmap_row["installed"]
            assert colmap_row["enabled"]
            assert rc_row["installed"]
            assert rc_row["enabled"]

            heading("Layer 6 — HTTP discovery (/v1/backend/providers)")
            r3 = await client.get("/v1/backend/providers")
            assert r3.status_code == 200, r3.text
            prov_items = r3.json()["items"]
            prov_ids = sorted(item["provider_id"] for item in prov_items)
            print(f"active providers ({len(prov_ids)}): {prov_ids}")
            assert "colmap_cli" in prov_ids
            assert "realityscan_cli" in prov_ids

            # ---- Layer 7: per-task routing. Submit features with each
            # provider; the route must accept both (501 = capability gate
            # firing on the LOCAL process-wide backend is fine — the
            # provider IS resolved and persisted into task state).
            heading("Layer 7 — per-task routing on /datasets/.../features")
            proj = (await client.post("/v1/projects", json={"name": "multi-plug"})).json()
            ds = (
                await client.post(
                    f"/v1/projects/{proj['project_id']}/datasets",
                    json={
                        "name": "ds",
                        "source": {"kind": "upload", "entries": []},
                        "camera_model": "SIMPLE_RADIAL",
                        "intrinsics_mode": "single_camera",
                    },
                )
            ).json()
            did = ds["dataset_id"]
            # Need an image registered for stage validation.
            init = (await client.post("/v1/uploads", json={"expected_size": 4})).json()
            await client.patch(
                f"/v1/uploads/{init['upload_id']}",
                content=b"\x00\x01\x02\x03",
                headers={"Content-Range": "bytes 0-3/4"},
            )
            fin = (await client.post(f"/v1/uploads/{init['upload_id']}:finalize")).json()
            await client.post(
                f"/v1/datasets/{did}/images",
                json={"name": "x.jpg", "blob_sha": fin["blob_sha"], "width": 8, "height": 8},
            )

            # colmap_cli IS expected to accept features.extract.sift (it
            # advertises that capability). realityscan_cli is an
            # action-only plugin — it does NOT advertise features.extract.sift,
            # so the routing layer rejects the pairing with a clean 422.
            for provider, expect_202 in (("colmap_cli", True), ("realityscan_cli", False)):
                submit = await client.post(
                    f"/v1/datasets/{did}/features",
                    json={"spec": {"version": 1, "type": "sift", "provider": provider}},
                )
                body = submit.json()
                if expect_202:
                    assert submit.status_code == 202, body
                    print(
                        f"  provider={provider!r} -> 202 job_id={body['job_id']!r} "
                        f"echoed provider={body.get('provider')!r}"
                    )
                    assert body.get("provider") == provider, (
                        f"route did not echo resolved provider: {body}"
                    )
                else:
                    # Routing layer must reject the mismatch and name BOTH
                    # the offending provider and the alternative candidates
                    # that COULD satisfy the stage. That informativeness is
                    # the whole point of multi-plugin error reporting.
                    assert submit.status_code == 422, body
                    detail = str(body.get("detail") or "")
                    print(f"  provider={provider!r} -> 422 detail={detail[:140]!r}")
                    assert provider in detail, f"422 body did not name the provider: {body}"
                    assert "candidates" in detail, (
                        f"422 body did not list alternative candidates: {body}"
                    )
                    # The candidates list MUST include colmap_cli (which
                    # IS enabled for features), proving routing knows about
                    # both plugins simultaneously.
                    assert "colmap_cli" in detail, f"candidates list missing colmap_cli: {body}"

        heading("Result")
        print("OK — two plugins coexist in one sfmapi instance and route independently.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        sys.exit(1)
