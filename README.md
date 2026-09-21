# confac-ut-shorts-scrapping

Zero-budget MVP for finding, ranking, and downloading YouTube Shorts for the content-factory pipeline.

## What is implemented

- YouTube discovery through YouTube Data API v3
- public metadata only: views, likes, comments, publication date, duration
- derived metrics: age, views/hour, like rate, comment rate
- configurable popularity score
- filtering before download
- one-click download through `yt-dlp`
- tiny FastAPI backend + static UI
- tests for scoring/filter rules

> Audience retention is intentionally not used: it is not publicly available for arbitrary third-party videos through YouTube Data API.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
# put your YouTube Data API key into YOUTUBE_API_KEY
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## MVP flow

```text
YouTube Data API
  -> candidates
  -> public metadata
  -> hard filter
  -> popularity score
  -> TOP N
  -> download button
  -> yt-dlp
  -> local mp4
```

## Default filters

Defaults live in `.env.example` and can be overridden without changing code:

- maximum age: 168 hours
- minimum views: 10,000
- minimum views/hour: 500
- minimum like rate: 2%
- maximum duration: 180 seconds

Popularity weights:

- views/hour: 0.55
- like rate: 0.30
- comment rate: 0.15

These are starting hypotheses, not universal truth. Tune them after collecting real project data.

## API

### Search and rank

`POST /api/search`

```json
{
  "query": "AI news",
  "max_age_hours": 168,
  "min_views": 10000,
  "min_views_per_hour": 500,
  "min_like_rate": 0.02,
  "max_duration_seconds": 180,
  "limit": 10
}
```

### Download selected video

`POST /api/download`

```json
{
  "url": "https://www.youtube.com/shorts/VIDEO_ID"
}
```

Downloaded files are written to `downloads/` by default and ignored by Git.

## Notes / known MVP limitations

- YouTube does not expose an `isShort` flag in Data API. The MVP treats short-duration search results as Shorts candidates and emits `/shorts/{id}` URLs.
- Search uses one `search.list` call plus one `videos.list` call per request to keep quota usage simple.
- `yt-dlp` depends on YouTube's current behavior and may occasionally require updates.
- There is no persistent snapshot history yet, so true delta-based growth velocity is a next step. Current `views_per_hour` is `views / video_age_hours`.
- No TikTok/Instagram/Hypit integration is included in this branch; this branch deliberately stops at local MP4 + metadata.
