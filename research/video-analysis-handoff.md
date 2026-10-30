# Передача сюжета и реплик в дальнейшую обработку

## Сейчас

`POST /api/story` получает публичную YouTube-ссылку либо ссылку на ролик VK, TikTok, Instagram или Rutube вместе с путём ранее скачанного MP4 из `downloads`. Для соцролика интерфейс после подтверждения скачивает MP4 и передаёт его в Gemini. Ответ `{url, model, story}` — описание видео, а не проверенная транскрипция. Файл ограничен 14 МиБ и 180 секундами.

Реплики распознаются отдельно и локально: `POST /api/transcript` прогоняет скачанный через приложение MP4 любой площадки (до 180 секунд) через [faster-whisper](https://github.com/SYSTRAN/faster-whisper) на этом компьютере. Зависимость необязательная (`requirements-transcribe.txt`), модель по умолчанию — `large-v3-turbo` на CPU (`int8`). Результат хранится отдельно от описания видео и метрик:

```json
{
  "source_url": "https://rutube.ru/video/VIDEO_ID/",
  "language": "ru",
  "model": "large-v3-turbo",
  "segments": [
    {"start_seconds": 1.2, "end_seconds": 3.8, "speaker": null, "text": "Распознанная реплика"}
  ]
}
```

Whisper не разделяет говорящих, поэтому `speaker` всегда `null`. Сегменты — черновик: перед публикацией как цитат их нужно просмотреть и исправить. Панель **Реплики** в интерфейсе позволяет поправить текст и таймкоды, удалить или добавить строки; `POST /api/transcript/srt` записывает проверенные сегменты в `downloads/VIDEO_ID.srt` рядом с MP4.

Проверено на реальном новостном ролике Rutube (31 с, русская речь): 7 реплик за ~16 секунд на CPU, одна ошибка распознавания («Бицепикер» вместо «вице-спикер») исправлена вручную, сохранённый SRT принят проверкой аргументов `scripts/autoclip_handoff.py --srt`.

## Передача в AutoClip

`MP4 + SRT` передаются в [AutoClip CLI](https://github.com/zhouxiaoka/autoclip/blob/main/docs/CLI_AND_MCP.md) явной командой `scripts/autoclip_handoff.py "downloads\VIDEO_ID.mp4" --srt "downloads\VIDEO_ID.srt"`; интерфейс показывает эту команду после сохранения SRT. Результат AutoClip не смешивается с `story`: первый создаёт клипы, второй описывает исходный ролик. Кнопка AutoClip в интерфейсе пока не нужна: запуск расходует ресурсы и, с `--use-project-gemini`, квоту Gemini, поэтому остаётся явным действием.

## Что может понадобиться дальше

- Разделение говорящих: [WhisperX](https://github.com/m-bain/whisperX) или [Gemini Transcribe](https://ai.google.dev/gemini-api/docs/transcribe) (облачный вариант с метками говорящих).
- Расшифровка на видеокарте: `WHISPER_DEVICE=cuda` требует библиотек CUDA 12 и cuDNN 9; без них ctranslate2 не находит `cublas64_12.dll`.
