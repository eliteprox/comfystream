# Local capabilities

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

The local capability path does not yet register paid Livepeer media operations.
It must not be advertised as a production replacement until runtime, IPC, protocol
and model acceptance pass. Existing live-runner entrypoints remain available.
