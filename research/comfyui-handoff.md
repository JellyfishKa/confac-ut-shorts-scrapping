# ComfyUI handoff

Этот документ описывает нейтральный контракт между metadata-collector и ComfyUI workflow.

Цель интеграции — не связывать collector с конкретными node ID или текущей структурой workflow. Collector должен отдавать стабильный manifest, а отдельный adapter уже сопоставляет его поля с input-полями выбранного API-format workflow.

## Текущий поток данных

```text
YouTube Data API
  -> VideoCandidate
  -> POST /api/comfy/manifest
  -> JSON manifest
  -> ComfyUI adapter
  -> API-format workflow
  -> POST /prompt
```

Пока generation workflow развивается, metadata-часть может независимо собирать, ранжировать и экспортировать кандидатов.

## Зачем отделять metadata от MP4

Для части downstream-сценариев достаточно:

- source URL;
- title;
- description;
- popularity score;
- views/hour;
- growth views/hour;
- like/comment rate;
- breakout ratio.

Локальный MP4 нужен только тем шагам, которые работают с содержимым самого видео: кадрами, аудио, OCR, транскрибацией, visual analysis или video reconstruction.

```text
candidate
  |
  +-- metadata-only workflow --> без скачивания
  |
  +-- video-level workflow --> download finalist --> local_video_path
```

## Manifest

Endpoint:

```http
POST /api/comfy/manifest
```

принимает объект кандидата и необязательный `local_video_path`.

На metadata-only этапе:

```json
{
  "video": {"...": "candidate returned by /api/search"},
  "local_video_path": null
}
```

Manifest содержит три логических блока:

```text
source
virality
comfyui_inputs
```

`source` содержит исходные сведения о ролике.

`virality` содержит рассчитанные metadata-метрики.

`comfyui_inputs` — плоский набор значений, предназначенный для последующего mapping в input-поля ComfyUI workflow.

## Рекомендуемые поля `comfyui_inputs`

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

`source_video_path` может быть `null`. Это означает, что кандидат ещё не скачивался и downstream workflow должен работать только с метаданными либо явно запросить скачивание.

## API-format workflow

Для программного запуска ComfyUI используется workflow в API-формате, а не обычный UI-save JSON.

Workflow экспортируется из ComfyUI в API format. Название пункта меню зависит от версии frontend и обычно содержит `Export Workflow (API)` или `Save (API)`.

Локальный ComfyUI обычно принимает prompt по адресу:

```text
POST http://127.0.0.1:8188/prompt
```

Пример принципа интеграции:

```python
import json
import urllib.request

workflow = json.load(open("workflow_api.json", encoding="utf-8"))
manifest = json.load(open("VIDEO_ID.comfy-manifest.json", encoding="utf-8"))

# Node IDs приведены только как пример.
workflow["12"]["inputs"]["text"] = manifest["source"]["title"]
workflow["18"]["inputs"]["text"] = json.dumps(
    manifest["virality"],
    ensure_ascii=False,
)

payload = json.dumps({"prompt": workflow}).encode("utf-8")
request = urllib.request.Request(
    "http://127.0.0.1:8188/prompt",
    data=payload,
    headers={"Content-Type": "application/json"},
)

urllib.request.urlopen(request).read()
```

Фактические node ID и input names должны извлекаться из конкретного экспортированного API workflow.

## Рекомендуемая граница ответственности

Collector отвечает за:

- поиск;
- публичные metadata;
- snapshots;
- derived metrics;
- ranking;
- shortlist;
- manifest.

ComfyUI adapter отвечает за:

- загрузку API-format workflow;
- mapping manifest-полей в inputs;
- подстановку `local_video_path`, если MP4 требуется;
- отправку `/prompt`;
- возврат `prompt_id`.

Такой контракт позволяет менять ComfyUI workflow без изменений в логике поиска и анализа metadata.

## Следующий шаг интеграции

После стабилизации API-format workflow можно добавить модуль, например:

```text
app/comfy_runner.py
```

который принимает:

```text
manifest
workflow_api.json
node_mapping.json
```

и возвращает ComfyUI `prompt_id`.
