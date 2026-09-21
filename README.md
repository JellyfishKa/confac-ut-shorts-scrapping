# confac-ut-shorts-scrapping

Metadata-first MVP для поиска, ранжирования и выборочного скачивания YouTube Shorts в контент-пайплайне.

Основная идея проекта — максимально долго работать только с метаданными и скачивать исходное видео лишь тогда, когда оно действительно требуется для последующей обработки.

## Что реализовано

- поиск кандидатов через YouTube Data API v3;
- получение публичных метаданных видео и каналов;
- фильтрация по возрасту, длительности, просмотрам и engagement-метрикам;
- расчёт производных метрик для сравнения роликов;
- сохранение повторных наблюдений в SQLite;
- расчёт фактического роста просмотров между snapshot'ами;
- экспорт результатов в CSV и JSON;
- формирование нейтрального JSON-manifest для передачи данных в ComfyUI;
- выборочное скачивание финалистов через `yt-dlp`;
- Jupyter Notebook для анализа результатов и построения графиков.

## Архитектура

```text
YouTube Data API
       ↓
public metadata
       ↓
SQLite snapshots ─────→ growth metrics
       ↓
filter + scoring
       ↓
shortlist
       ↓
CSV / JSON / Jupyter / ComfyUI manifest
       ↓
        ├── metadata-only workflow → без скачивания
        │
        └── video-level workflow → yt-dlp → local MP4
```

Такой подход позволяет сначала обработать десятки кандидатов по дешёвым данным и скачивать только несколько роликов, которым нужен анализ кадров, аудио, OCR, транскрибация или другой video-level processing.

## Используемые данные

### Публичные метаданные

Для каждого кандидата сохраняются:

```text
video_id
url
title
description
channel_title
published_at
duration_seconds
views
likes
comments
subscribers
thumbnail_url
```

Количество подписчиков может отсутствовать, если канал не предоставляет его публично.

### Производные метрики

`age_hours`

Возраст ролика в часах.

`views_per_hour`

```text
views / age_hours
```

Средняя скорость набора просмотров с момента публикации.

`like_rate`

```text
likes / views
```

`comment_rate`

```text
comments / views
```

`breakout_ratio`

```text
views / subscribers
```

Показывает масштаб ролика относительно размера канала. Метрика недоступна, если число подписчиков скрыто.

`growth_views_per_hour`

```text
(current_views - previous_views) / elapsed_hours
```

Рассчитывается после повторного наблюдения того же видео и отражает фактическую скорость роста между двумя snapshot'ами.

`popularity_score`

Конфигурируемая эвристическая оценка для сортировки кандидатов. На текущем этапе она не является моделью прогнозирования виральности и предназначена только для предварительного ранжирования.

## Почему используются snapshot'ы

`views_per_hour` показывает среднюю скорость за всё время существования ролика. Для поиска роликов, которые ускоряются прямо сейчас, этого недостаточно.

Поэтому каждый поиск сохраняет наблюдение в SQLite:

```text
video_id
observed_at
views
likes
comments
```

При следующем появлении того же ролика можно вычислить изменение метрик за фактический интервал времени.

База создаётся автоматически:

```text
data/snapshots.sqlite3
```

## Быстрый запуск

```bash
git clone https://github.com/JellyfishKa/confac-ut-shorts-scrapping.git
cd confac-ut-shorts-scrapping
python -m venv .venv
```

Активация окружения:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Установка зависимостей:

```bash
python -m pip install -r requirements.txt
```

Создание `.env`:

```powershell
# Windows
copy .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

Добавьте API key:

```env
YOUTUBE_API_KEY=your_key_here
```

Запуск:

```bash
python -m uvicorn app.main:app --reload
```

Интерфейс будет доступен по адресу:

```text
http://127.0.0.1:8000
```

## Получение YouTube Data API key

Для доступа к публичным метаданным достаточно обычного API key; OAuth на текущем этапе не требуется.

1. Создайте или выберите проект в Google Cloud Console.
2. Откройте **APIs & Services → Library**.
3. Включите **YouTube Data API v3**.
4. Откройте **APIs & Services → Credentials**.
5. Выберите **Create credentials → API key**.
6. Сохраните ключ в `.env`.
7. Рекомендуется ограничить ключ только API `YouTube Data API v3`.

Документация Google:

- https://developers.google.com/youtube/v3/getting-started
- https://developers.google.com/youtube/registering_an_application
- https://docs.cloud.google.com/docs/authentication/api-keys

## Сценарий демонстрации

1. Запустить приложение и выполнить поиск по теме, например `AI tools`.
2. Показать, что список кандидатов и их метрики получены без скачивания MP4.
3. Сравнить кандидатов по `views/h`, `like rate`, `comment rate`, `breakout` и `score`.
4. Экспортировать текущую выборку в CSV или JSON.
5. Сформировать `Comfy JSON` для выбранного кандидата.
6. Повторить тот же поиск через некоторое время и показать появившийся `Δ views/h`.
7. Скачать MP4 только для выбранного финалиста, если downstream-пайплайну действительно требуется видео.

## ComfyUI integration

Endpoint `POST /api/comfy/manifest` формирует workflow-agnostic manifest. Collector не зависит от конкретных node ID внутри ComfyUI workflow.

Пример структуры:

```json
{
  "source": {
    "url": "https://www.youtube.com/shorts/VIDEO_ID",
    "local_video_path": null,
    "title": "...",
    "description": "..."
  },
  "virality": {
    "views": 250000,
    "views_per_hour": 12000,
    "growth_views_per_hour": 18000,
    "like_rate": 0.052,
    "comment_rate": 0.004,
    "breakout_ratio": 12.4,
    "popularity_score": 0.81
  },
  "comfyui_inputs": {
    "source_url": "...",
    "source_video_path": null,
    "source_title": "...",
    "virality_score": 0.81
  }
}
```

Пока workflow в ComfyUI меняется, manifest выступает стабильным промежуточным контрактом. После стабилизации workflow можно добавить adapter, который сопоставит поля manifest с конкретными input-полями API-format workflow и отправит его в ComfyUI через `/prompt`.

Подробности: [`research/comfyui-handoff.md`](research/comfyui-handoff.md).

## Jupyter-анализ

Дополнительные зависимости:

```bash
python -m pip install -r requirements-research.txt
jupyter lab
```

Notebook:

```text
notebooks/live_metadata_demo.ipynb
```

Notebook обращается к локальному FastAPI backend, поэтому API key остаётся в `.env` приложения. В нём доступны:

- таблица кандидатов;
- ranking по `popularity_score`;
- график `views/hour` против `like rate`;
- график фактического `growth_views_per_hour` после повторного сбора данных;
- пример формирования ComfyUI manifest.

## API

### Проверка состояния

```http
GET /health
```

### Поиск, фильтрация и snapshot

```http
POST /api/search
```

Пример:

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

### Формирование ComfyUI manifest

```http
POST /api/comfy/manifest
```

```json
{
  "video": {"...": "candidate returned by /api/search"},
  "local_video_path": null
}
```

### Скачивание выбранного видео

```http
POST /api/download
```

```json
{
  "url": "https://www.youtube.com/shorts/VIDEO_ID"
}
```

## Ограничения текущего MVP

- YouTube Data API не возвращает публичный флаг `isShort`; используется фильтр длительности и `videoDuration=short` на этапе поиска.
- Для первого наблюдения невозможно вычислить фактический growth rate — требуется минимум два snapshot'а.
- `popularity_score` пока является эвристикой и требует калибровки на накопленных данных.
- Audience retention чужих видео недоступен через публичный YouTube Data API.
- `yt-dlp` зависит от текущего поведения YouTube и периодически требует обновлений.
- Hypit пока не является частью runtime-пайплайна. Потенциальное применение — структурный анализ небольшого числа выбранных референсов после metadata-first отбора.

## Дальнейшее развитие

Логичные следующие шаги:

- накопление dataset из metadata snapshots;
- анализ распределений и корреляций в Jupyter;
- калибровка scoring-функции по фактическим данным;
- отдельный adapter для стабильного ComfyUI API workflow;
- подключение YouTube Analytics для собственных опубликованных видео;
- добавление дополнительных источников данных без изменения основного контракта кандидата.
