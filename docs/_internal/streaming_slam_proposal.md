# Streaming SLAM Endpoint — Design Proposal

**Status**: Proposal. Not implemented. Reviewed but not committed.

**Owner**: TBD.

**Companion**: `docs/guides/decisions.md` (would add `P5`),
`CLAUDE.md` "Locked Tech Decisions" §SSE-only (touches L9),
`docs/guides/oneshot_streaming_proposal.md` (P4 — single-frame
ergonomics that compose with this).

---

## Why this exists

Some consumers want **real-time visual SLAM**: a video / camera
stream goes in, a 6-DoF pose comes out per frame, with loop closure
and live map refinement. That is structurally different from sfmapi's
current batch SfM flow.

The request shape:

```
client streams frames at ~30 Hz
   → server holds a long-lived per-session worker with the live map
   → server streams pose + occasional map deltas back at ~30 Hz
   → optional IMU + timestamps tightly coupled
   → loop closure runs in background, occasionally rewrites prior poses
```

Latency target: **< 50 ms per frame** for VIO-grade SLAM (inertial
helps), **< 100 ms** for visual-only. `oneshot/localize` (P4 phase b)
caps at ~250 ms even with no DB roundtrip — fine for AR overlay
against a static scan, useless for a moving robot.

## What this breaks vs sfmapi's locked decisions

| Locked decision | SLAM impact |
|---|---|
| **L4** Sealed-snapshot reads only | SLAM consumers want the **live** map, by definition |
| **L5** One Task = one ARQ job | A SLAM session is one **long-lived process state**, not a series of one-shot tasks |
| **L9** SSE-only realtime | SLAM is **bidirectional**: client streams frames, server streams poses. WebSocket required |
| **L1** One GPU per instance / per-GPU concurrency = 1 | SLAM holds the GPU continuously for one session. The slot model still works (1 session = 1 GPU); **scheduling differs**: long-lived occupancy instead of bursty per-task |
| **L13** Job rollup via `_maybe_finalize_job` | SLAM "sessions" don't fit the Job state machine. Need a parallel `Session` resource |

`L4` and `L9` would each need explicit unlocking entries in
`docs/guides/decisions.md` if this proposal is approved.

## Proposed design — separate namespace, parallel infrastructure

### New resource: `Session`

A SLAM session is its own resource type, parallel to `Reconstruction`
but with different invariants:

```
SessionRow
├── session_id (ULID)
├── tenant_id
├── project_id
├── status            (active | paused | closed | failed)
├── slam_backend      ("orbslam3" | "openvins" | "droid_slam")
├── created_at, last_active_at, closed_at
├── live_map_ref      (path to in-process worker's map state)
└── keyframe_count    (live counter)
```

REST endpoints (all standard HTTP, the streaming part is separate):

```
POST   /v1/slam/sessions                    → create + reserve a worker slot
GET    /v1/slam/sessions/{sid}              → typed session state
GET    /v1/slam/sessions/{sid}/keyframes    → paginated keyframes (when client wants to inspect)
GET    /v1/slam/sessions/{sid}/map.snapshot → trigger a sealed-snapshot of the live map (yes — that primitive still works for "freeze the current state")
DELETE /v1/slam/sessions/{sid}              → close + free the worker slot
GET    /v1/slam/sessions                    → list active sessions (admin)
```

### Streaming endpoint at `/ws/v1/slam/sessions/{sid}/frames`

WebSocket. Symmetric framing in both directions, binary-preferred:

**Client → server (per frame, ~30 Hz)**:

```
{
  "schema_version": 1,
  "kind": "frame",
  "frame_id": <uint64 monotonic per session>,
  "timestamp_ns": <int64>,
  "image": {
    "format": "jpeg" | "rgb8",
    "bytes": <base64 in JSON mode> | <binary in msgpack mode>,
    "width": ..., "height": ...
  },
  "imu": [{"t_ns": ..., "gyro": [x,y,z], "accel": [x,y,z]}, ...]   // optional, tightly coupled if present
}
```

**Server → client (per frame, ~30 Hz)**:

```
{
  "schema_version": 1,
  "kind": "pose",
  "frame_id": <matches client>,
  "cam_from_world": {"rotation": {...}, "translation": [...]},
  "tracking_state": "ok" | "lost" | "relocalizing",
  "num_inliers": ...,
  "is_keyframe": bool,
  "map_dirty": bool   // hint: client should refetch a map snapshot soon
}
```

**Server → client (occasional, on loop closure or relocalization)**:

```
{
  "schema_version": 1,
  "kind": "pose_correction",
  "from_frame_id": ...,
  "to_frame_id": ...,
  "corrections": [{"frame_id": ..., "cam_from_world": {...}}, ...]
}
```

WebSocket framing: JSON for v0 (debugging-friendly), binary
`application/x-slam-frame-v1` / `application/x-slam-pose-v1`
add-on for v1 (≤16 byte header + raw float32 pose). Mirrors the
existing binary-format pattern (`points-binary`, `depth-binary`).

### New backend protocol — `SlamBackend`

Sibling of `SfmBackend`. Lives at `app/adapters/slam_backend.py`:

```python
class SlamBackend(Protocol):
    name: str
    version: str

    def create_session(
        self, *, intrinsics, imu_calibration=None, ...
    ) -> "SlamSessionHandle": ...

class SlamSessionHandle(Protocol):
    """Long-lived per-session worker state."""

    def push_frame(
        self, frame_id: int, ts_ns: int, image_bytes: bytes,
        imu: list[ImuMeasurement] | None
    ) -> "FrameResult": ...

    def relocalize_against_recon(self, recon_id: str) -> bool: ...

    def snapshot_map(self) -> "SealedMapSnapshot": ...

    def close(self) -> None: ...
```

Concrete adapters (each in its own file, lazy-imported like the
pycolmap adapter):

| Adapter | Backend | Notes |
|---|---|---|
| `app/adapters/slam_orbslam3.py` | ORB-SLAM3 (Python bindings) | Mature, monocular / stereo / RGB-D + VIO. License: GPLv3 — verify before shipping |
| `app/adapters/slam_openvins.py` | OpenVINS | VIO-only, MIT license. Faster but no loop closure |
| `app/adapters/slam_droid.py` | DROID-SLAM (PyTorch) | GPU-heavy, very accurate, slower |

Capability flags: `slam.session.monocular`, `slam.session.stereo`,
`slam.session.rgbd`, `slam.imu_tightly_coupled`, `slam.loop_closure_online`,
`slam.relocalize_against_recon`. Discoverable via existing
`/v1/capabilities` so consumers can select a backend.

### New worker model — `SessionSupervisor`

Parallel to `app/workers/dispatcher.py`. The Task-DAG dispatcher is
unchanged.

`app/workers/session_supervisor.py`:

```python
class SessionSupervisor:
    """One process, one GPU, one live SLAM session.

    Receives frames over an asyncio Queue from the WS handler,
    pushes them through the SlamBackend session, returns pose
    results to a downstream Queue the WS handler reads from."""

    async def run(self, session_id: str) -> None:
        backend = get_slam_backend()
        handle = backend.create_session(...)
        try:
            while not self._stop.is_set():
                frame = await self.frame_in.get()
                if frame is None: break
                pose = await asyncio.to_thread(handle.push_frame, ...)
                await self.pose_out.put(pose)
                if pose.is_keyframe:
                    self._record_keyframe_to_db(...)
        finally:
            handle.close()
```

The lease pattern from the Task dispatcher (`app/orchestrator/lease.py`)
adapts: a Session row carries a `lease_expires_at` + `worker_id`,
the supervisor heartbeats every 5s, the janitor reclaims abandoned
sessions and frees the GPU slot.

### Persistence model — sparse, deliberate

| Frame | DB row | Blob persisted |
|---|---|---|
| Every frame | **No** | **No** (discarded after backend processes) |
| Keyframe | `Keyframe` row (session_id, frame_id, pose, timestamp) | Optional: blob of the JPEG, gated by `?persist_keyframe_images=true` |
| Map snapshot (on demand) | `MapSnapshot` row (session_id, seq, sealed_path) | Yes — published as a sealed snapshot, identical pattern to recon snapshots (`L4`) |
| Pose stream | **No** | **No** — high-frequency, downstream's responsibility to record |

This is the sharpest invariant difference from sfmapi today: most
of the SLAM stream is **not persisted**. The system is designed
around the discipline that the live map is the worker's
responsibility, not the DB's.

### What stays the same

- Auth + tenancy: `current_tenant()` dep on the WS handshake.
- Capability discovery: SLAM features advertised under `slam.*`.
- Sealed snapshots: still the only way the API exposes "a map view at a moment in time" — `POST /v1/slam/sessions/{sid}/map.snapshot` triggers the same atomic-rename publish protocol.
- Decision register discipline: every SLAM-specific invariant gets a row.
- SDK ergonomics layered the same way: typed errors, `Capabilities.supports()`, content-addressed blobs for keyframe images.

## Backend choice — recommend OpenVINS for v0

| Backend | Pros | Cons | Verdict |
|---|---|---|---|
| ORB-SLAM3 | Mature, broad sensor support, loop closure | GPLv3 — all sfmapi deployers must comply | **Skip v0** unless GPL is acceptable |
| OpenVINS | MIT, VIO-only, fast (≤30 ms) | No loop closure, no monocular-only | **Recommend v0** — VIO is the most-asked SLAM mode anyway |
| DROID-SLAM | Most accurate, monocular | Heavy GPU (≥6 GB), slower (~80 ms), PyTorch dep | **Phase b** — once OpenVINS adapter pattern is proven |

Loop closure can be added later via a separate `loop_closure` worker
that periodically reads keyframes + map state and rewrites poses
through the `pose_correction` channel. That's a real Phase c if the
v0 OpenVINS adapter ships clean.

## Implementation cost

| Phase | LOC | New files | Tests | Time |
|---|---|---|---|---|
| **a — Session resource (REST only, no WS yet)** | ~300 | 4 | unit + integration | ~6h |
| **b — `SlamBackend` Protocol + OpenVINS adapter** | ~500 | 2 | unit (against bundled VIO test data) | ~12h |
| **c — `SessionSupervisor` + lease + GPU slot mgmt** | ~250 | 1 | integration with stub backend | ~6h |
| **d — WS handler + frame/pose framing** | ~200 | 1 | live-server WS test (Python suite + TS suite) | ~5h |
| **e — Map snapshot trigger + sealed-publish reuse** | ~80 | 0 | integration | ~2h |
| **f — SDK ergonomics: streamSlam(), submitFrame()** | ~400 | per-language | per-language contract tests | ~10h |

**Total: ~40-45 hours of careful work.** This is the biggest single
proposal in the register and the only one that touches multiple
locked decisions.

The numbers exclude:
- ORB-SLAM3 / DROID-SLAM adapters (Phase b alternates).
- Loop-closure background worker (Phase c+).
- Multi-camera / stereo support (would be a separate proposal layered on top).

## Decision tree

```
Is there a real consumer for streaming SLAM today?
├─ YES, "VIO with IMU, < 50ms latency"
│   ├─ Approve P5 Phases a-d. Bundle OpenVINS adapter.
│   ├─ Unlock L4 (live map reads via WS — sealed snapshots remain
│   │   for REST reads).
│   ├─ Unlock L9 (WebSocket realtime added under /ws/v1/slam/...).
│   └─ Shipping target: ~6 weeks.
├─ YES, "monocular relocalization against an existing scan, ~200ms acceptable"
│   ├─ Approve P4 phase b (oneshot/localize) ONLY.
│   └─ Defer P5. The single-frame cycle covers the use case.
└─ NO real consumer yet
    └─ Defer both. The wire-spec patterns sfmapi has developed
        (binary formats, capability flags, contract tests, three-
        language SDKs with parity) directly transfer to a separate
        `slamapi` service if streaming SLAM ever lands as a
        first-class product. Forcing it into sfmapi now would
        compromise the batch SfM design without a confirmed need.
```

## Recommendation

Approve **P4 phase b (oneshot/localize)** for the relocalization use
case — that's small (~3h additional on top of P4 phase a),
contained, and serves the "AR overlay / heritage doc / static-map
relocalization" consumer.

**Defer P5 (full streaming SLAM)** unless one of these is true:
1. There's a confirmed consumer with VIO data flowing through a
   prototype.
2. The product roadmap has shifted from "SfM-as-a-service" to
   "spatial-AI-as-a-service" and SLAM is in scope.

If neither holds, the right answer is to track P5 as a known design,
recommend `slamapi` as a sibling service when the time comes, and
keep sfmapi focused on the batch-SfM invariants it has correctly
locked.

Add to `docs/guides/decisions.md`:

```
| P5 | Streaming SLAM endpoint at `/ws/v1/slam/sessions/{sid}/frames` with `Session` resource + `SlamBackend` Protocol + OpenVINS reference adapter. | Phases a-d ready to design; ~40-45h total. Touches L4 (live map reads) + L9 (WebSocket). | docs/guides/streaming_slam_proposal.md | **Confirmed consumer + readiness to unlock L4 + L9**. Otherwise: track as future architectural reference; recommend `slamapi` sibling service when needed. |
```
