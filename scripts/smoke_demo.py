"""Tiny terminal demo against the running local FastAPI server."""

import json

import httpx

BASE = "http://127.0.0.1:8000"

health = httpx.get(f"{BASE}/health", timeout=10)
health.raise_for_status()
print("health:", json.dumps(health.json(), ensure_ascii=False))

payload = {
    "query": "AI tools",
    "max_age_hours": 168,
    "min_views": 1000,
    "min_views_per_hour": 0,
    "min_like_rate": 0,
    "max_duration_seconds": 180,
    "limit": 10,
}
response = httpx.post(f"{BASE}/api/search", json=payload, timeout=30)
response.raise_for_status()
data = response.json()

print(f"\nchecked={data['found']} returned={data['returned']}\n")
for i, video in enumerate(data["videos"], 1):
    print(
        f"{i:>2}. score={video['popularity_score']:.3f} "
        f"v/h={video['views_per_hour']:.0f} "
        f"growth={video['growth_views_per_hour']} "
        f"like={video['like_rate'] * 100:.2f}% "
        f"| {video['title']}"
    )

if data["videos"]:
    manifest = httpx.post(
        f"{BASE}/api/comfy/manifest",
        json={"video": data["videos"][0], "local_video_path": None},
        timeout=10,
    )
    manifest.raise_for_status()
    print("\nTop candidate ComfyUI manifest:")
    print(json.dumps(manifest.json()["manifest"], ensure_ascii=False, indent=2))
