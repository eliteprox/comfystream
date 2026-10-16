# Handoff: schema-backed request forms

Build the default request UI from the schema a capability registers. Do not hardcode one JSON body per ffmpeg tool. The images-to-video app rejects a compose body with `images is required` because those are different schemas. The form for a selected app comes from that app's registration.

The runner that serves this is `ffmpeg-runner` in `/home/elite/repos/comfystream`. Schemas live in `src/comfystream/ffmpeg/schemas.py`. Registration is in `server/ffmpeg_runner.py`.

## Where the schema is

Do not read `GET https://engine-production-e189.up.railway.app/v1/offerings` for the form. That response is `app`, modes, rates, and runner counts. It has no request schema.

Read orchestrator discovery:

`GET https://hky.eliteencoder.net:8936/discovery`

Each ffmpeg runner object has `app`, `url`, and `metadata`. `metadata` is a JSON string. Parse it. For `comfystream/ffmpeg-images-to-video` it looks like:

```json
{
  "kind": "ffmpeg-batch",
  "required": ["images"],
  "schema_sha256": "<64 lowercase hex>",
  "schema_url": "./schema",
  "streaming": false,
  "tool": "images-to-video"
}
```

`comfystream/ffmpeg-compose` is the same shape with `"required": ["tracks"]` and `"tool": "compose"`. `comfystream/ffmpeg-publish` uses `"kind": "ffmpeg-swarm-publish"` and `"required": ["request_id", "stamps"]`.

`required` is enough to refuse an obviously wrong body. It is not the form. Fetch the document.

Resolve `schema_url` against `url` the same way `server/live_runner_batch_client.py` does: if the relative path is `.`, `./`, or empty, use `url` with no extra segment; otherwise `urljoin(url.rstrip("/") + "/", schema_url)`. `./schema` on `https://hky.eliteencoder.net:8936/apps/runner_…/app` is `https://hky.eliteencoder.net:8936/apps/runner_…/app/schema`.

That GET through the orchestrator returns HTTP 402 `invalid live runner payment signer address` until the same Livepeer payment signer used for a paid call is attached. Inside the docker network the unsigned route is `GET http://ffmpeg-runner:8994/batch/{tool}/schema` and `GET http://ffmpeg-runner:8994/publish/schema`. Prefer the discovered URL plus the signer the UI already uses for jobs. Do not bake `ffmpeg-runner:8994` into the page.

sha256 the response body, hex, lowercase. It must equal `schema_sha256`. On a mismatch, show the error and do not render a guessed form. Runner ids change when `ffmpeg-runner` restarts, so rediscover rather than caching an app URL across reloads.

Fal capabilities already register the same `schema_url` and `schema_sha256` pair (`src/comfystream/capabilities/catalog.py`). Use one extractor for every discovery entry that has both fields.

## How to read the document

Two formats exist. Branch on `format`.

`comfystream.ffmpeg.request.v1` is the request schema itself: `type: object`, `properties`, `required`, `additionalProperties: true`. `tool` names the capability. This is what the ffmpeg batch apps serve.

`livepeer.fal.endpoint-schema.v1` wraps the request. `input_schema` is a local `$ref` such as `#/components/schemas/FfmpegApiComposeInput`. Resolve that ref against `components.schemas` and treat the target as the request schema. Do not treat the wrapper as the form.

Render from the request schema:

- Show every `required` property. Fill `default` when present (`fps` 4, `frames` 1, `mode` `pad`, `frame_type` `first`, `start_time` 0, `integrated_loudness` -18).
- `enum` is a select. `mode` is `pad`, `crop`, or `stretch`. `frame_type` is `first` or `last`. A compose track `type` is `video`, `audio`, or `image`.
- An array starts with one item. `minItems` is the floor (`video_urls` is at least two, `images` and `tracks` at least one).
- `oneOf` on an images entry accepts a URL string or `{url, frames}`. Default the object form so duration is visible. A bare string remains valid and means one frame.
- Compose `duration` and `timestamp` are milliseconds. `duration: 2000` at `fps: 4` is eight frames, two seconds.
- Leave extra keys the user typed. The runner accepts additional properties. Do not invent keys that are not in `properties`.

## What a generated body looks like

Images-to-video, sent as the job payload:

```json
{
  "fps": 4,
  "images": [
    {
      "url": "https://eliteencoder.net/assets/img/map-image.png",
      "frames": 8
    }
  ]
}
```

Compose:

```json
{
  "fps": 4,
  "tracks": [
    {
      "id": "video",
      "type": "image",
      "keyframes": [
        {
          "url": "https://eliteencoder.net/assets/img/map-image.png",
          "timestamp": 0,
          "duration": 2000
        }
      ]
    }
  ]
}
```

Post that object as `payload`, not as the job itself. The builder is `POST https://engine-production-e189.up.railway.app/v1/jobs` with `Authorization: Bearer <builder key>` and:

```json
{
  "app": "comfystream/ffmpeg-compose",
  "payload": { }
}
```

`app` is the discovery `app` string (`comfystream/ffmpeg-images-to-video` or `comfystream/ffmpeg-compose`), never a runner id. A body whose top-level keys omit `metadata.required` is not submitted. Sending `tracks` to the images-to-video app is the bug this UI exists to stop.

Audio compose tracks are skipped by the runner. A timeline with only `type: "audio"` fails with `tracks need a video or image keyframe`. The default track type is `image` when the user pastes the map still, and `video` when they paste a video URL.

## Out of scope

- Editing `src/comfystream/ffmpeg/schemas.py` or the runner routes.
- Teaching `GET /v1/offerings` to grow a schema field. Discovery is the source.
- Swarm stamps, `~/swarm.key`, or `comfystream/ffmpeg-publish` signing. Publish is another schema (`request_id`, `stamps`) and only appears in discovery when `BEE_URL` is set. The running container has `BEE_URL` empty, so jobs return a file URL, not a Swarm reference.
- Caching a runner URL across a container restart.

## Done when

- Choosing `comfystream/ffmpeg-images-to-video` renders `images` and `fps`. The default payload includes `images`. It does not include `tracks`.
- Choosing `comfystream/ffmpeg-compose` renders `tracks` and `fps`. The default payload includes `tracks`.
- Both forms were produced by fetching `./schema` and checking `schema_sha256`. A hash mismatch blocks the form.
- Submitting the images-to-video default with `https://eliteencoder.net/assets/img/map-image.png` does not return `images is required`.
- A capability the UI has never seen, if its discovery metadata has `schema_url` and `schema_sha256`, gets a form from that schema without a code change.
