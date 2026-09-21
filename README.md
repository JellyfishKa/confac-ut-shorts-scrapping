# confac-ut-shorts-scrapping

Zero-budget **metadata-first** MVP for finding, ranking and selectively downloading YouTube Shorts for the content-factory pipeline.

The point of the demo is not «мы умеем скачивать Shorts». The point is:

> inspect 10–50 candidates as cheap metadata, keep snapshots, rank them, send structured data to the next workflow, and download MP4 only for the few finalists that actually need video-level analysis.

## Что уже можно показать

- real YouTube Data API search;
- public metadata: title, description, publish time, duration, views, likes, comments, channel subscribers;
- derived metrics: age, average views/hour, like rate, comment rate, breakout ratio;
- SQLite snapshots and **real delta views/hour** after the same video is observed again;
- metadata score and shortlist without downloading anything;
- CSV / JSON export;
- one-click **ComfyUI manifest** export for a selected candidate;
- explicit MP4 download via `yt-dlp` only when you choose a finalist;
- Jupyter notebook with live API data and charts.

## Быстрый запуск

```bash
git clone https://github.com/JellyfishKa/confac-ut-shorts-scrapping.git
cd confac-ut-shorts-scrapping
python -m venv .venv
```

Activate:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Install:

```bash
pip install -r requirements.txt
```

Create `.env`:

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Put the key into `.env`:

```env
YOUTUBE_API_KEY=your_key_here
```

Start:

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

The page should show **YouTube API key подключён**.

## Как получить тестовый YouTube API key

Для публичных metadata нужен обычный **API key**, OAuth не нужен.

1. Открой Google Cloud Console.
2. Создай новый project (или выбери существующий).
3. Открой **APIs & Services → Library**.
4. Найди и включи **YouTube Data API v3**.
5. Открой **APIs & Services → Credentials**.
6. Нажми **Create credentials → API key**.
7. Скопируй ключ в `.env`.
8. Лучше сразу поставить API restriction: **YouTube Data API v3**.

Official docs:
- https://developers.google.com/youtube/v3/getting-started
- https://developers.google.com/youtube/registering_an_application
- https://docs.cloud.google.com/docs/authentication/api-keys

As of 2026, `search.list` has its own default bucket of 100 calls/day. One search in this MVP uses one `search.list`, one `videos.list`, and one `channels.list` call.

## Что показать Дмитрию за 2 минуты

1. В UI ищем, например, `AI tools`.
2. Показываем, что получили 10–50 реальных роликов и **не скачали ни одного**.
3. Смотрим `views/h`, `like rate`, `breakout`, metadata `score`.
4. Нажимаем `CSV` или `JSON` — данные можно анализировать отдельно.
5. Нажимаем `Comfy JSON` у одного кандидата — получаем контракт для ComfyUI без MP4.
6. `MP4` нажимаем только если downstream workflow реально требует видео.
7. Через 10–30 минут повторяем тот же поиск: появляется `Δ views/h` на повторно найденных роликах.

Snapshots live in:

```text
data/snapshots.sqlite3
```

## Метрики

Raw public metadata:

```text
published_at
duration_seconds
views
likes
comments
subscribers (если публичны)
```

Derived:

```text
age_hours
views_per_hour = views / age_hours
like_rate = likes / views
comment_rate = comments / views
breakout_ratio = views / subscribers
```

After a repeated observation:

```text
growth_views_per_hour =
(current_views - previous_views) / elapsed_hours
```

`popularity_score` сейчас простой конфигурируемый heuristic. Это стартовая гипотеза, а не «формула виральности».

Audience retention произвольного чужого видео публичный YouTube Data API не отдаёт. Для своих опубликованных роликов позже можно подключить YouTube Analytics и построить feedback loop с retention/engagement.

## ComfyUI

Кнопка **Comfy JSON** выгружает workflow-agnostic manifest с блоками `source`, `virality` и `comfyui_inputs`.

Это позволяет Дмитрию спокойно менять workflow в ComfyUI, а наш collector не зависит от конкретных node IDs. Когда его workflow стабилизируется, делаем маленький adapter `manifest -> API workflow inputs -> POST /prompt`.

See [`research/comfyui-handoff.md`](research/comfyui-handoff.md).

## Jupyter demo

```bash
pip install -r requirements-research.txt
jupyter lab
```

Open:

```text
notebooks/live_metadata_demo.ipynb
```

FastAPI server должен работать в другом terminal. Notebook вызывает локальный backend и строит графики по **реальным API results**.

## API

### Search + rank + snapshot

`POST /api/search`

```json
{
  "query": "AI tools",
  "max_age_hours": 168,
  "min_views": 1000,
  "min_views_per_hour": 0,
  "min_like_rate": 0,
  "max_duration_seconds": 180,
  "limit": 15
}
```

### Build ComfyUI manifest without downloading

`POST /api/comfy/manifest`

```json
{
  "video": {"...": "candidate returned by /api/search"},
  "local_video_path": null
}
```

### Download one finalist

`POST /api/download`

```json
{
  "url": "https://www.youtube.com/shorts/VIDEO_ID"
}
```

## Architecture

```text
YouTube Data API
       ↓
public metadata
       ↓
SQLite snapshots ──→ real Δviews/hour
       ↓
filter + score
       ↓
TOP candidates
       ↓
CSV / notebook / ComfyUI manifest
       ↓
        ├── metadata workflow → no MP4
        │
        └── video-level workflow → yt-dlp only for finalist
```

## Known limitations

- YouTube Data API does not expose a public `isShort` flag. We request `videoDuration=short` and apply our own `<= 180s` filter.
- The first observation cannot have real growth velocity; that requires at least two snapshots.
- The metadata score is heuristic until we collect enough data to calibrate it.
- `yt-dlp` can require updates as YouTube changes.
- Hypit is not a virality oracle. Its useful future role is to decompose the few selected references into structural features / variants after metadata selection.
