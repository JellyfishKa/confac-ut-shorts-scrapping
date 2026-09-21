# ComfyUI handoff

The repository deliberately does **not** hard-code Dmitry's ComfyUI workflow, because the workflow is still changing.

Instead we expose a stable metadata contract.

## Current flow

```text
YouTube API
  -> VideoCandidate
  -> POST /api/comfy/manifest
  -> JSON manifest
  -> adapter owned by the ComfyUI workflow
```

This allows us to keep doing cheap metadata research while the generation workflow evolves.

## Why we should not download first

Many ComfyUI experiments can start from title, description, source URL, virality score, views/hour, growth views/hour, like/comment rate and breakout ratio.

Only workflow nodes that require actual frames, audio, OCR, transcription or video reconstruction need a local MP4.

```text
candidate
  |
  +-- metadata-only workflow --> no download
  |
  +-- video analysis/generation --> download finalist --> local_video_path
```

## API-format workflow

For programmatic execution, ComfyUI expects its **API workflow format**, not the normal UI save JSON.

Export the workflow in API format (`Export Workflow (API)` / `Save (API)`, wording depends on the frontend version).

A local ComfyUI instance normally accepts a prompt payload at:

```text
POST http://127.0.0.1:8188/prompt
```

Conceptually:

```python
import json
import urllib.request

workflow = json.load(open("workflow_api.json", encoding="utf-8"))
manifest = json.load(open("VIDEO_ID.comfy-manifest.json", encoding="utf-8"))

# Example only. Replace node IDs with the real workflow inputs.
workflow["12"]["inputs"]["text"] = manifest["source"]["title"]
workflow["18"]["inputs"]["text"] = json.dumps(manifest["virality"], ensure_ascii=False)

payload = json.dumps({"prompt": workflow}).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8188/prompt",
    data=payload,
    headers={"Content-Type": "application/json"},
)
urllib.request.urlopen(request).read()
```

Official ComfyUI examples use the same basic `/prompt` pattern. Actual node IDs and input names must come from Dmitry's exported API workflow.

## Stable fields to map

Recommended fields under `comfyui_inputs`:

```text
source_url
source_video_path
source_title
source_description
virality_score
views_per_hour
growth_views_per_hour
like_rate_pct
comment_rate_pct
breakout_ratio
```

When the workflow stabilizes, the next step is a tiny `app/comfy_runner.py` adapter that accepts the manifest, Dmitry's API workflow JSON and a node mapping config, then returns ComfyUI `prompt_id`.
