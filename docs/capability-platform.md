# Local capabilities

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
and module paths retained as imports. Queue binding preserves existing in-process
live behavior. The new shared-memory utility is not yet wired into the live transport.

The runtime pin includes an upstream candidate packaging fix making the legacy Google
SDK optional. `constraints-linux.in` selects the existing CUDA 12.8 family and avoids
an obsolete s3fs build on Python 3.12. Run uv resolution normally; do not use a
blanket `--no-deps` workaround. The full lock and clean-image acceptance are pending.

The local capability path does not yet register paid Livepeer media operations.
It must not be advertised as a production replacement until runtime, IPC, protocol
and model acceptance pass. Existing live-runner entrypoints remain available.
