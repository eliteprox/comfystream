# Local capabilities

The batch catalog uses capability App IDs: `text-to-image`, `image-edit`,
`transcription`, `music` and `image-to-3d`. Exact models and immutable model
revisions belong in the v1 envelope. Deployment files preserve stable runner IDs,
prices and capacity groups. Existing live/media compatibility App IDs are retained
until their clients are migrated explicitly.

The capability branch is based on `feat/ffmpeg-stream-batch`, initially
`02ca83c`, preserving its FFmpeg tools, Swarm publishing and realtime pricing.
Both runners have installed console entrypoints; use the `ffmpeg` extra for
`comfystream-ffmpeg-runner`. Workflow/config resources ship in the wheel.

`CapabilityClient` executes bundled Stream Pack descriptors with the embedded core's
spawn-based process executor. It does not use the CUDA-disabled hosted-provider pool.
Use one client per scheduled GPU slot and share `capacity_file` across containers
using that physical GPU. A worker is evicted when the model changes or another container requests its
shared capacity. Successful workers otherwise retain their model weights. Cancellation waits for the
worker to exit before releasing GPU admission.

Provide explicit workspace, staged input directory and output directory paths.
Only operator-installed descriptors can execute. Candidate workflows remain private;
callers pass capability identifiers and validated public parameters.

Workflow behavior was moved to the Stream Pack dependency, with the old node names
and module paths retained as imports. Live frame queues now use bounded,
session-scoped shared-memory channels with a core process executor. Inference
workers keep their canonical module identity so spawn can pickle core callables.

The runtime pin includes an upstream candidate packaging fix making the legacy Google
SDK optional. `constraints-linux.in` selects the existing CUDA 12.8 family and avoids
an obsolete s3fs build on Python 3.12. Run uv resolution normally; do not use a
blanket `--no-deps` workaround. The older branch fix in `91c4b9d` selects the
packaged fork's `v0.37.0.7` tag, which removes the old direct protobuf cap. That
tag still includes `google-generativeai`; its pinned language client requires
protobuf below 6, conflicting with the Livepeer SDK's minimum 6.31.1. Making the
unused legacy provider optional resolves this remaining conflict. Both the
workspace and installed package use the same immutable runtime revision.

The initial CUDA 12.8 media image installed successfully with normal dependency
resolution on eliteprd1. Rebased locks, both GPU profiles, model acceptance and
legacy workflow compatibility still need verification before deployment.

`comfystream-catalog-runner --config deployment.json` registers five synchronous
single-shot applications: FLUX, BiRefNet, Whisper, ACE-Step and TripoSR. Qwen and
Wan are excluded from this deployment. Each application advertises one exact model,
an immutable image and descriptor deployment identity, and the same one-slot capacity
group. All requests use the embedded client's core process executor and shared
capacity file. The gateway continues to dispatch through Builder Engine/orchestrators.

The deployment file contains `image` (an immutable image digest), `capacity_group`
and `models` (capability ID to `app`, `runner_id`, and positive USD `price`). Prices
are fixed per request; the current operator-approved price is USD 0.02 per model.
Startup validates every installed workflow before registering; partial registration
failure unregisters previous entries. Shutdown unregisters every entry and joins
the executor. Metadata remains a candidate until independently admitted.

This server binds loopback and requires the orchestrator on the same host; containers
must use host networking. Do not expose its port through an unauthenticated reverse
proxy. The orchestrator owns public payment/session authorization. Operator secrets
are supplied only through `LIVEPEER_ORCH_SECRET`. Other required environment values
are `LIVEPEER_ORCH_URL`, `COMFYUI_CWD` and `COMFYSTREAM_CAPACITY_FILE`.

Each registration URL ends in `/catalog/{capability}`; the orchestrator appends the
standard operation path. `/openapi.json` documents these routes. Image output uses
OpenAI base64 response shapes, transcription accepts multipart `file`, and extension
routes return typed base64 media. Image inputs accept inline data URLs or multipart
uploads. Filesystem paths and remote URLs are refused. Request files and workflow
artifacts are removed after synchronous delivery; gateway receipts/artifact retention
are independent. Unsupported fields fail before model dispatch.

Do not describe registration as paid-network acceptance. Runtime, IPC, protocol,
failure and model acceptance remain deployment gates. Existing live-runner entrypoints
remain available and the production live service is not replaced by this batch server.

Successful batch requests retain their model worker and weights indefinitely while
clearing request caches and history. There is no idle expiry. Switching capabilities
replaces the worker in the single residency slot. Competing containers using the
same capacity file publish locked waiter files on its shared volume; an idle owner
joins its worker before releasing the slot. Active requests are never evicted by
capacity demand. Cancellation, failure and shutdown also join the worker. All
containers sharing a capacity file must support this demand protocol; older clients
that only wait on the capacity lock cannot request eviction from a warm owner.


## Repeatable runner-contract v1 deployment

`runner_contract: 1` is the metadata protocol. The registration `Version` string
(such as `inference.runner.v2`) is informational and does not select a contract.
The deployment hash includes the full digest-pinned image reference, model source
and revision, profiles, and behavior settings. Raw metadata must fit 1,024 UTF-8 bytes.

`contracts/runner-registry.lock.json` pins the shared registry source. Its native
profile additions currently use a review branch, not a released registry tag.
Gateways must use that same registry revision to resolve the native profiles.
The profiles preserve candidate admission; the lock and draft review do not claim
complete profile conformance or paid-network acceptance.

```sh
python3 scripts/fetch_runner_registry.py --destination ../runner-registry
python3 scripts/build_capability_runtime.py --base-image <existing-image-id> \
  --role catalog --tag comfystream-catalog:<source-commit> --record catalog-build.json
```

The build helper archives committed source only, resolves the existing base to its
immutable local image ID, installs normally against the branch lockfile, and records
source, registry, base, and output image identities. It does not copy model caches,
wallets, credentials, or unrelated untracked handovers. Live builds using the legacy
conda base pass `--venv /workspace/miniconda3/envs/comfystream` and
`--uv-bin /workspace/miniconda3/envs/comfystream/bin/uv`. The supported build roles
are `catalog`, `live`, `ffmpeg`, and the existing caller-stamped `swarm` publisher.
The headless setup retains inference plugins, disables the legacy Manager startup
logger, fixes the OpenCV loader conflict, and prevents a custom-node directory
from shadowing the installed Python package. FFmpeg is explicitly CPU-only.

`configure_capability_deployment.py --inventory host.json --output staged` renders
Compose and catalog configuration from explicit host values. Wallet paths, account,
RPC provider, grants, payment policy, GPU UUID, model caches, prices, and shared
capacity IDs must belong to that host. Additional read-only model overlays are
preserved. Secrets use explicit stack interpolation; importing an env file alone
does not inject variables into containers. Local image IDs must be built/loaded on
the destination; a build record does not claim the image was registry-published.

For a v1 live service, set `COMFYSTREAM_RUNTIME_IMAGE`, immutable
`COMFYSTREAM_MODEL_REVISIONS`, `COMFYSTREAM_GPU_CAPACITY_GROUP`, and the physical
`COMFYSTREAM_WORKFLOW_GPU`. One live capacity group uses one GPU. Generic workflow
sessions reserve the same supervisor slot as the isolated model workers and release
it only after their executor exits. A completed workflow session receives a fresh
executor for the next session.

FAL is off by default. `--skip-batch` makes that explicit; `--enable-fal` opts into
the legacy adapter and is excluded from this v1 rollout. Before enabling FAL under
v1, map every provider endpoint and reviewed schema hash to actual registry
operations and verify submit/status/result/cancel/error semantics and pricing.
Do not substitute a native media profile for a provider queue contract.

Validate captured raw discovery with `scripts/check_runner_contracts.py`. It checks
registered UTF-8 byte size, envelope schema, registry hashes, and operation IDs;
it does not claim inference or paid conformance. Drain work before replacement.
`scripts/replace_capability_runner.py` consumes a host-private inspection snapshot,
refuses stale container IDs, retains the previous image/container, and restores it
on failed health checks. Inspection snapshots and generated create definitions
contain credentials and stay host-local with owner-only permissions. Do not run
two orchestrators or advertise two versions of the same capacity during cutover.
