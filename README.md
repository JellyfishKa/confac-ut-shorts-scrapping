# confac-ut-shorts-scrapping

Metadata-first MVP для поиска и ранжирования YouTube Shorts, ручного добавления отдельных роликов из других площадок и выборочного скачивания в контент-пайплайне.

Основная идея проекта — максимально долго работать только с метаданными и скачивать исходное видео лишь тогда, когда оно действительно требуется для последующей обработки.

## Что реализовано

- поиск кандидатов через YouTube Data API v3;
- ручной импорт прямой ссылки на VK Клип, TikTok, Instagram Reel или видео Rutube через `yt-dlp` без скачивания MP4;
- получение публичных метаданных видео и каналов;
- фильтрация по возрасту, длительности, просмотрам и engagement-метрикам;
- расчёт производных метрик для сравнения роликов;
- сохранение повторных наблюдений в SQLite;
- расчёт фактического роста просмотров между snapshot'ами;
- экспорт результатов в CSV и JSON;
- формирование нейтрального JSON-manifest для передачи данных в ComfyUI;
- выборочное скачивание финалистов через `yt-dlp`;
- анализ сюжета выбранного публичного YouTube Shorts через Gemini с экспортом JSON;
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

В Windows PowerShell можно обойтись без активации и гарантированно установить пакеты именно в окружение проекта:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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
GEMINI_API_KEY=your_gemini_key_here
```

Запуск:

```bash
python -m uvicorn app.main:app --reload
```

Для Windows PowerShell надёжнее запускать Python из `.venv` напрямую:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app
```

Команда `py -m ...` может выбрать глобальный Python даже после активации `.venv`. Проверить используемый интерпретатор можно командой `.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"`; путь должен содержать `.venv` этого проекта.

Интерфейс будет доступен по адресу:

```text
http://127.0.0.1:8000
```

### Ролики из других площадок

Поле **Добавить по ссылке** принимает ссылку на один публичный ролик VK Клипы, TikTok, Instagram Reels или Rutube. Приложение считывает доступные метаданные и добавляет видео в общий список; MP4 на этом шаге не скачивается. Кнопка **MP4** позволяет отдельно скачать выбранный ролик. Если площадка не отдаёт просмотры, лайки или время публикации, поле остаётся пустым: ноль не подставляется и рейтинг не вычисляется по выдуманным данным.

Тематический поиск и автоматический отбор популярных роликов сейчас работают через YouTube Data API. Добавление по ссылке не заменяет поиск по VK/TikTok/Instagram/Rutube. Некоторые ролики могут быть недоступны `yt-dlp` из-за ограничений платформы, региона или требования входа; поддержку каждой площадки нужно проверить на реальных ссылках. Подробнее: [список поддерживаемых сайтов yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

Кнопка **Сюжет** у найденного ролика отправляет его публичную YouTube-ссылку в Gemini Video Understanding и показывает хронологическое описание. Результат можно скопировать или скачать в JSON. `GEMINI_API_KEY` хранится только на сервере в `.env`; без него интерфейс покажет понятную ошибку. Анализ может занимать время и расходовать квоту Gemini. Доступность зависит от прав и доступности ролика для Gemini; таймкоды и факты следует проверить по оригиналу. MP4 для этого действия не скачивается.

### Как пользоваться анализом видео

1. Выполните поиск и выберите один ролик из списка.
2. Нажмите **Сюжет**. Для публичной ссылки YouTube Gemini составит описание происходящего по хронологии и отметит различимые реплики. Это черновик для просмотра и отбора, а не дословная расшифровка.
3. Сверьте важные события, цитаты и таймкоды с оригиналом. Если нужен исходник для дальнейшей обработки, нажмите **MP4** только у выбранного видео.
4. Сохраните JSON сюжета отдельно от CSV/JSON с метриками: метрики отвечают за отбор, сюжет — за содержание ролика.

Если Gemini сообщает о перегрузке, попробуйте повторить запрос позже. Действительный ключ не гарантирует доступность модели в конкретный момент. После изменения `.env` перезапустите сервер.

### Следующий шаг: точные реплики и AutoClip

Для дословных реплик нужен отдельный источник текста: субтитры автора либо распознавание звука. [Gemini Transcribe](https://ai.google.dev/gemini-api/docs/transcribe) принимает загруженный аудиофайл и поддерживает таймкоды и разделение говорящих; локальный Whisper — вариант без облачного распознавания. Текущий `/api/story` принимает только публичную ссылку YouTube и не транскрибирует скачанный MP4. Эти функции пока не подключены.

План подключения: выбранный ролик → MP4/аудио → транскрипт с таймкодами → сверка транскрипта с сюжетом → SRT для нарезки. [AutoClip](https://github.com/zhouxiaoka/autoclip/blob/main/README-EN.md) умеет работать с готовым SRT через CLI `autoclip produce VIDEO.mp4 --srt TRANSCRIPT.srt ...`, поэтому будущий шаг транскрибации можно использовать и для нарезки. Детали передачи данных и проверки описаны в [`research/video-analysis-handoff.md`](research/video-analysis-handoff.md).

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

### Сюжет выбранного видео

```http
POST /api/story
```

```json
{
  "url": "https://www.youtube.com/shorts/VIDEO_ID"
}
```

### Импорт одного ролика по ссылке

```http
POST /api/import
```

```json
{
  "url": "https://www.instagram.com/reel/VIDEO_ID/"
}
```

Возвращает `video` в формате кандидата. Поле `platform` различает источники; статистика может отсутствовать. Анализ сюжета по прямой ссылке на не-YouTube видео не подключён: для него потребуется загрузка файла в модель анализа видео.

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
