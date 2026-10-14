"""Opt-in handoff of a downloaded MP4 to an installed AutoClip CLI.

AutoClip is a separate application. See its official CLI guide:
https://github.com/zhouxiaoka/autoclip/blob/main/docs/CLI_AND_MCP.md
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels", "douyin", "xiaohongshu", "bilibili")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS = PROJECT_ROOT / "downloads"
PROJECT_ENV = PROJECT_ROOT / ".env"
GEMINI_DATA_DIR = PROJECT_ROOT / "data" / "autoclip-gemini"


def _input_file(value: str, *, suffix: str, within: Path | None = None) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != suffix:
        raise ValueError(f"Нужен существующий файл {suffix}: {value}")
    if within is not None and not path.is_relative_to(within.resolve()):
        raise ValueError(f"MP4 должен находиться в папке загрузок: {within}")
    return path


def _executable(value: str) -> Path:
    located = shutil.which(value)
    if not located:
        raise ValueError("AutoClip CLI не найден. Установите его отдельно и передайте --autoclip путь к autoclip.exe.")
    path = Path(located).resolve()
    if not path.is_file() or (os.name == "nt" and path.suffix.lower() != ".exe"):
        raise ValueError("Нужен исполняемый файл AutoClip CLI (autoclip.exe на Windows).")
    return path


def _check_gemini_data_dir() -> None:
    settings_file = GEMINI_DATA_DIR / "settings.json"
    if not settings_file.exists():
        return
    try:
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        api = saved.get("api") or {}
        provider = api.get("api_provider") or api.get("llm_provider") or saved.get("api_provider") or saved.get("llm_provider")
        model = api.get("api_model") or api.get("model_name") or saved.get("api_model") or saved.get("model_name")
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError("Не удалось проверить настройки отдельного каталога AutoClip Gemini.") from exc
    if (provider and provider != "gemini") or (model and model != "gemini-3.5-flash-lite"):
        raise ValueError("В отдельном каталоге AutoClip сохранена другая модель. Проверьте data/autoclip-gemini/settings.json.")


def run_handoff(*, video: str, downloads_dir: str, platform: str,
                srt: str | None = None, autoclip: str = "autoclip",
                use_project_gemini: bool = False) -> dict:
    if platform not in PLATFORMS:
        raise ValueError(f"Неподдерживаемая целевая площадка: {platform}")
    download_root = Path(downloads_dir).expanduser().resolve()
    video_path = _input_file(video, suffix=".mp4", within=download_root)
    srt_path = _input_file(srt, suffix=".srt") if srt else None
    executable = _executable(autoclip)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if use_project_gemini:
        key = str(dotenv_values(PROJECT_ENV).get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "")
        if not key:
            raise ValueError("GEMINI_API_KEY не найден в .env проекта или окружении.")
        _check_gemini_data_dir()
        env.update(GEMINI_API_KEY=key, AUTOCLIP_API_KEY=key,
                   LLM_PROVIDER="gemini", LLM_MODEL="gemini-3.5-flash-lite",
                   AUTOCLIP_DATA_DIR=str(GEMINI_DATA_DIR))

    version = subprocess.run([str(executable), "--version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", env=env,
                             timeout=30, check=False)
    if version.returncode != 0:
        raise RuntimeError("AutoClip CLI найден, но не запускается. Проверьте отдельную установку.")

    command = [str(executable), "produce", str(video_path), "--platform", platform, "--json"]
    if srt_path:
        command.extend(["--srt", str(srt_path)])
    print("Запускаю AutoClip по вашему явному запросу. Его настроенная модель или транскрибация может использовать облачный сервис.",
          file=sys.stderr)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env, timeout=7200, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"AutoClip завершился с кодом {result.returncode}. Проверьте его настройки модели, "
            "транскрибации и журналы AutoClip."
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AutoClip завершился без ожидаемого JSON-ответа; результат нужно проверить вручную.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("AutoClip вернул неожиданный формат результата.")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Передать скачанный MP4 в отдельно установленный AutoClip")
    parser.add_argument("video", help="Локальный MP4 из папки downloads")
    parser.add_argument("--downloads-dir", default=str(DEFAULT_DOWNLOADS), help="Папка загрузок приложения")
    parser.add_argument("--srt", help="Необязательные проверенные субтитры SRT")
    parser.add_argument("--platform", choices=PLATFORMS, default="youtube_shorts")
    parser.add_argument("--autoclip", default="autoclip", help="Путь к AutoClip CLI")
    parser.add_argument("--use-project-gemini", action="store_true",
                        help="Использовать GEMINI_API_KEY из .env проекта для модели AutoClip")
    args = parser.parse_args(argv)
    try:
        payload = run_handoff(video=args.video, downloads_dir=args.downloads_dir,
                              platform=args.platform, srt=args.srt, autoclip=args.autoclip,
                              use_project_gemini=args.use_project_gemini)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
