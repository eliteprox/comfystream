# Handoff: Swarm publisher client

Build the publisher that signs with the operator's mainnet Swarm key and keeps a live `comfystream/ffmpeg-live` session moving. Players only receive a feed. This process is the signer. The runner never sees the key.

Implement it in `/home/elite/repos/livepeer-python-gateway` as `examples/ffmpeg_swarm_publisher.py`. Use the gateway the way `runner-app-examples/echo/client.py` does. Talk to Bee through [`swarm-bee`](https://github.com/ethswarm-tools/bee-py) (`pip install swarm-bee`, import `bee`). That package is an HTTP client for a Bee node's API: feeds, files, chunks, and postage. It does not run a node and it does not speak libp2p. Do not add a new gateway channel type. Do not put the key, the batch id, or the feed key in the ffmpeg runner image.

## Key

The wallet is the Ethereum keystore v3 at `~/swarm.key` on this machine (`address`, `crypto`, `id`, `version` 3). Decrypt it with `SWARM_KEY_PASSWORD` from the environment. If that variable is empty, read the password from the terminal without echoing it. The `address` field is the 20-byte owner, hex, no `0x`.

- Never print the decrypted private key, the password, or the keystore ciphertext.
- Never commit `~/swarm.key`. Leave the file where it is. The script takes `--key` and defaults to that path.
- The file mode is `0644`. Tell the operator to `chmod 600` it. Do not change the mode from the script.
- This key is not the Livepeer payment signer. `--signer` on the gateway client is only for a paid orchestrator session. Swarm signing uses the keystore.

The address in the keystore must be the owner of the postage batch. Pass that batch as `--batch-id` / `SWARM_BATCH_ID` (32-byte hex). The key already has mainnet funds. Do not buy a batch automatically. Refuse to start when `--batch-id` is missing. The first Bee `POST /chunks` that accepts a stamp is the check that this address owns the batch.

## What the runner already does

`comfystream/ffmpeg-live` is the persistent app. With `BEE_URL` set to a mainnet Bee, `PipelineWorker._open_audio_swarm` requests trickle channels `in`, `out`, `stamps`, and `control`, and serves `GET /sse` on the app URL.

For each ~1 second AAC-LC ADTS segment (`src/comfystream/ffmpeg/swarm.py`):

1. SSE `{seq, addresses, start, duration}`. `addresses` are hex chunk addresses, leaves first, root last.
2. The runner reads `{req_id, stamps}` from the `stamps` trickle. One 113-byte hex stamp per address, same order. `StampIssuer.stamp` from `livepeer_swarm` is that layout: EIP-191 `personal_sign` over `keccak(address || batchId || index || timestamp)`, `v` 27 or 28.
3. The runner packs and `SwarmPublisher.put`s each frame to `{BEE_URL}/chunks`.
4. SSE `{seq, ok, ref, url}`. `url` is `https://bzz.limo/bytes/{ref}?x=.aac`.

Video still leaves on `out` through `MediaPublish` and is not stamped. The runner does not read `control` and does not write the playlist feed. This client does.

`BEE_URL` on the running `ffmpeg-runner` container is empty today, so the live session does not open `stamps` until that env is a real mainnet Bee. The stub Bee is not that node.

## Session

Follow echo:

1. `reserve_session(discovery_url=..., app="comfystream/ffmpeg-live", signer_url=...)`.
2. `POST {session.app_url}/stream` with the session headers the gateway already sends. The JSON body can be `{}` or a preset (`720p`, `480p`, `360p`).
3. The response includes `in`, `out`, and, when Bee is on, `stamps` and `control`.
4. `GET {session.app_url}/sse` for `data: ` JSON lines.
5. Publish the input MPEG-TS to `{in}/{seq}` with `livepeer_gateway` the same way echo publishes to `in`. A file argument or `-` for stdin. The runner only emits audio stamps for audio in that stream.
6. On shutdown, `stop_runner_session`.

Discovery defaults to `https://localhost:8935/discovery`. This machine's ffmpeg runner registers with `https://hky.eliteencoder.net:8936`. Pass that as `--discovery` when talking to the live orchestrator.

The runner still expects one 113-byte stamp per address, from `livepeer_swarm.stamp.StampIssuer`: EIP-191 `personal_sign` over `keccak(address || batchId || index || timestamp)`, `v` 27 or 28. Use one `StampIssuer` for the session. `swarm-bee`'s client-side stamper (`examples/stamper_client_side.py` in that repo) may replace it only if a unit test shows the 113 bytes match `StampIssuer` for the same key, batch, and timestamp. Otherwise leave stamping on `StampIssuer` and use `swarm-bee` for Bee I/O.

`POST {stampsUrl}/{seq}` body `{"req_id": "<uuid>", "stamps": ["<hex>", ...]}` with `Content-Type: application/json`. Wait for the SSE `{seq, ok, ref, url}` before treating that segment as public. On `{ok: false}` log the error and continue. Do not write the AAC to disk.

## Feed

The feed is what [weeb-3](https://github.com/lat-murmeldjur/weeb-3) plays. `attachStream(media, owner, topic, "live")` reads sequential feed updates and parses each retrieved payload with `HlsPlaylist::parse` in `src/stream_hls.rs`. A raw m3u8, or a feed that only stores a 32-byte reference, does not decode: `decode_feed_payload_root` in `src/bzz_stream.rs` requires an embedded Bee data root.

Each feed index is one full playlist snapshot. Indexes are `0, 1, 2, ...` with no gaps. Later snapshots append segments or move `#EXT-X-MEDIA-SEQUENCE` forward. Overlapping segment references must stay the same. Keep at least 16 seconds of media in the window (`HLS_LIVE_STARTUP_BUFFER_SECONDS`). About 40 seconds also covers a Roku sitting 30 seconds back. Rewrite about every 4 seconds, not on every segment. Stay under 16 MiB (`MAX_STREAM_FEED_PAYLOAD_BYTES`).

The playlist text:

```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:1
#EXT-X-PLAYLIST-TYPE:EVENT
#EXT-X-MEDIA-SEQUENCE:<n>
#EXT-X-PROGRAM-DATE-TIME:2026-10-15T04:00:00.000Z
#EXTINF:1.000000,
https://bzz.limo/bytes/<64-hex-ref>?x=.aac
```

- First line is `#EXTM3U`. No byte-order mark.
- No `#EXT-X-ENDLIST` while the show is live. weeb-3 treats a terminal end tag as finalized.
- No master tags. `#EXT-X-STREAM-INF` makes `HlsPlaylist::parse` reject the body. One media playlist, audio only. AAC-LC ADTS is `audio/aac` once the segment bytes start with `0xFFF`.
- `#EXT-X-PROGRAM-DATE-TIME` on each segment, UTC, so `has_timeline()` is true. Without it, live startup takes the codec-bootstrap path.
- The segment line is a Swarm reference weeb-3 can extract. `swarm_reference` accepts a bare 64-hex or 128-hex string, or an `http`/`https` URL whose last path segment is that hex (`?` and `#` are stripped). `https://bzz.limo/bytes/<ref>?x=.aac` satisfies both weeb-3 and BrightScript. The reference is the segment root, 32 bytes, 64 hex characters.

Store that text as the feed update body in the embedded-root layout `embedded_join_root` accepts. For a playlist of at most 4096 bytes the body is `span || playlist`, where `span` is the playlist length as 8 little-endian bytes and the following bytes are the playlist itself. A larger playlist is a Bee chunk tree with the same span prefix, not a bare file. Use `swarm-bee` to write this update to `--bee-url` (the mainnet Bee the runner uploads to, network id `1`). Confirm one stored update round-trips through that layout before calling the feed done. Topic `--topic` defaults to `ffmpeg-live`. Owner is the keystore address.

The runner does not consume `{op: soc}` on `control` yet, so this client writes the feed. Also write the Donkey control body so a later runner can take over the POST without a client change:

`{"op":"soc","index":<n>,"owner":"<address>","id":"<hex>","sig":"<hex>","stamp":"<hex>","body":"<base64>"}`

`POST {controlUrl}/{seq}`.

Print, once, and again whenever the feed index advances:

- owner, 40 hex chars, no `0x`
- topic
- `https://lat-murmeldjur.github.io/weeb-3/live/stream/{owner}/{topic}`
- `https://bzz.limo/feeds/{owner}/{topic}?type=sequence`

weeb-3 calls `attachStream(media, owner, topic, "live")`. BrightScript GETs the bzz.limo feed URL and plays the `/bytes/{ref}?x=.aac` lines, 30 seconds back from the head. This client does not play media.

## Batch path

Keep `comfystream/scripts/ffmpeg_swarm_publish.py` as the one-shot file path. Point its key loading at the same keystore helper instead of `SWARM_SIGNER_KEY` when you touch it. Do not require that env var. Batch still uses `call_runner` on `comfystream/ffmpeg-{tool}` and then `comfystream/ffmpeg-publish` with `{request_id, stamps}`. Print the returned `url`.

## Out of scope

- A Bee node inside this client, a libp2p stack, or the PSS / GSOC parts of `swarm-bee`.
- Buying or topping up the postage batch.
- Turnkey or any remote signer. The keystore is local.
- Changing `packages/swarm-publish` stamp layout, or putting secrets in `docker-compose.ffmpeg-runner.yml`.
- The Roku channel and the webOS page. They consume the URLs this client prints.
- Video segment upload.

## Done when

- A keystore decrypt failure exits before any network call, and a successful run never logs the key.
- Against a mainnet Bee, one live segment returns `{ok: true, url}` and `GET` of that URL starts with the ADTS sync word `0xFFF`.
- A stored feed update decodes as `span || playlist`, and `HlsPlaylist::parse` accepts the playlist. Segment lines are `https://bzz.limo/bytes/<64-hex>?x=.aac`.
- `weeb-3` `attachStream(..., "live")` plays that feed. BrightScript reads `https://bzz.limo/feeds/{owner}/{topic}?type=sequence` and plays those same segment URLs.
- Stopping the process stops the Livepeer session and does not delete the key file.
