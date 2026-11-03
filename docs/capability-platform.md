# Local capabilities

Public registration names describe the service: `flux-klein`, `birefnet`, `whisper`,
`ace-step` and `triposr` for the native batch catalog; `live/*` for streaming models;
`media/ffmpeg-*` for native FFmpeg tools; and `fal-*` for enabled provider adapters.
FAL's reviewed upstream contract identities and hashes remain intact while the
registration removes the implementation prefix. `LIVEPEER_APP_ID` can override the
base live service's `live/video-to-video` name. Runtime package names remain internal.

The capability branch is based on `feat/ffmpeg-stream-batch`, initially
`02ca83c`, preserving its FFmpeg tools, Swarm publishing and realtime pricing.
Both runners have installed console entrypoints; use the `ffmpeg` extra for
`comfystream-ffmpeg-runner`. Workflow/config resources ship in the wheel.

`CapabilityClient` executes bundled Stream Pack descriptors with the embedded core's
spawn-based process executor. It does not use the CUDA-disabled hosted-provider pool.
Use one client per scheduled GPU slot and share `capacity_file` across containers
using that physical GPU. A worker is evicted when the model changes or after five
idle seconds so another container can acquire the slot. Cancellation waits for the
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
