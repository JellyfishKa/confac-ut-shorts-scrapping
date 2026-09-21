# Metadata-first virality research

Цель этого research-слоя — **минимизировать полные скачивания видео** и максимально долго работать только с дешёвыми публичными данными.

## Ключевой вывод для обсуждения

Не надо строить pipeline вида:

```text
100 роликов -> скачать 100 -> прогнать модели -> выбрать 3
```

Нужен pipeline:

```text
50–500 video IDs
    -> public metadata
    -> repeated metadata snapshots
    -> shortlist 5–10
    -> cheap enrichment
    -> full download only for 1–3 finalists
    -> Hypit / video analysis
```

## Какие публичные метрики доступны у чужих YouTube-видео

Через YouTube Data API `videos.list`:
- `publishedAt`
- `channelId`
- `title`
- `description`
- `tags` (если присутствуют)
- `categoryId`
- `duration`
- `caption`
- `viewCount`
- `likeCount`
- `commentCount`

Через `channels.list`:
- `subscriberCount`
- суммарный `viewCount`
- `videoCount`

Источники:
- https://developers.google.com/youtube/v3/docs/videos
- https://developers.google.com/youtube/v3/docs/channels

### Важное изменение YouTube в 2026

С 24 августа 2026 `viewCount` для всех форматов, включая Shorts, считается с момента начала воспроизведения (включая autoplay/hover/click/tap). Поэтому старые абсолютные пороги просмотров нельзя бездумно переносить на новые данные.

## Производные метрики без скачивания

### Первый проход

- `age_hours`
- `views_per_hour_lifetime = views / age_hours`
- `like_rate = likes / views`
- `comment_rate = comments / views`
- `engagement_rate = (likes + comments) / views`
- `views_per_subscriber = views / channel_subscribers`

`views_per_subscriber` полезен как breakout-сигнал: ролик небольшого канала, который сильно превосходит размер аудитории, часто интереснее абсолютного гиганта.

### Второй проход: snapshots

Самый важный апгрейд — не скачивать ролик, а повторно запросить его metadata через несколько часов.

Храним:

```text
video_id
captured_at
views
likes
comments
```

И считаем:

```text
delta_views_per_hour
delta_likes_per_hour
growth_acceleration
rank_change
```

Это сильнее `views / age`, потому что показывает **текущую**, а не среднюю историческую скорость.

## Metadata virality score

Для первого MVP предлагаем не ML, а cohort-relative score:

```text
45% velocity percentile
20% like-rate percentile
10% comment-rate percentile
15% breakout percentile
10% freshness percentile
```

Это не «вероятность вирусности». Это ranking proxy, который помогает решить, **что вообще стоит скачивать**.

После накопления snapshots вес `views_per_hour_lifetime` нужно заменить на `delta_views_per_hour`.

## Что нельзя получить публично у чужих роликов

Через публичный Data API мы не получаем:
- retention curve;
- `averageViewDuration`;
- `averageViewPercentage`;
- shares;
- детальную аудиторию.

Эти метрики доступны через YouTube Analytics для авторизованного владельца канала:
- https://developers.google.com/youtube/analytics/metrics
- https://developers.google.com/youtube/analytics/channel_reports

Это важно для будущего feedback loop: **у чужих роликов выбираем референсы по публичным proxy, у своих вариантов уже учимся на настоящем retention.**

## Где Hypit реально полезен

Hypit не является сервисом оценки viral score по metadata. Его место — после shortlist.

По README Hypit:
- принимает reference video;
- превращает его в workflow;
- позволяет менять footage, captions, B-roll, effects;
- позволяет делать много вариантов одной структуры;
- generation-модели необязательны.

Источники:
- https://github.com/hypit-ai/hypit
- https://github.com/hypit-ai/hypit/blob/main/README.md

### Что Hypit может дать нашей системе

После отбора 1–3 референсов:
- сохранить hook, но заменить тему;
- сделать несколько вариантов hook;
- менять caption pacing;
- менять B-roll;
- менять ведущего/voice;
- менять CTA;
- менять длину отдельных сегментов;
- повторно использовать удачную структуру.

То есть Hypit не «угадывает вирусность», а делает **структуру видео параметризованной**.

Дальше появляется нормальный экспериментальный цикл:

```text
public metadata
 -> выбрать reference
 -> Hypit workflow
 -> controlled variants
 -> публикация на своём канале
 -> YouTube Analytics
 -> retention / shares / avg view %
 -> связать performance с параметрами workflow
```

Тогда мы уже можем ответить не «нам кажется, что такой монтаж вирусный», а, например:

> варианты с hook < 1.5 сек и caption-change каждые 0.8–1.2 сек в нашей тематике удерживают лучше.

Это уже данные нашего проекта, а не универсальная магическая формула.

## Что показать Дмитрию

Открыть data-demo из `notebooks/` и показать четыре вещи:
1. top-N по metadata score;
2. scatter velocity vs engagement;
3. snapshots: как меняется momentum;
4. funnel: сколько полных скачиваний удалось избежать.

## Что делать следующим PR

1. SQLite таблицы `videos` и `snapshots`.
2. Команда `collect` без скачивания медиа.
3. Повторный poll известных `video_id`.
4. `delta_views_per_hour`.
5. Экспорт `csv/parquet` для Jupyter.
6. Только после ranking — download/Hypit.
