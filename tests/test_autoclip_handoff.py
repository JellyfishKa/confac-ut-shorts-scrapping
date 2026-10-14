import subprocess
from pathlib import Path

import pytest

from scripts import autoclip_handoff


def _fake_files(monkeypatch, *files):
    existing = {Path(file).resolve() for file in files}
    monkeypatch.setattr(Path, "is_file", lambda self: self.resolve() in existing)


def test_autoclip_handoff_invokes_official_produce_without_shell(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    downloads = root / "downloads"
    video = downloads / "selected video.mp4"
    srt = root / "checked.srt"
    executable = root / "autoclip.exe"
    _fake_files(monkeypatch, video, srt, executable)
    monkeypatch.setattr(autoclip_handoff.shutil, "which", lambda name: str(executable))
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, "1.5.6", "")
        return subprocess.CompletedProcess(args, 0, '{"status":"completed","project_id":"123"}', "")

    monkeypatch.setattr(autoclip_handoff.subprocess, "run", run)
    payload = autoclip_handoff.run_handoff(
        video=str(video), downloads_dir=str(downloads), platform="tiktok",
        srt=str(srt), autoclip=str(executable),
    )
    assert payload["project_id"] == "123"
    assert calls[0][0] == [str(executable), "--version"]
    assert calls[1][0] == [str(executable), "produce", str(video), "--platform", "tiktok",
                           "--json", "--srt", str(srt)]
    assert all(call[1].get("shell") is not True for call in calls)


def test_autoclip_rejects_video_outside_downloads_before_launch(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    downloads = root / "downloads"
    external = root / "external.mp4"
    _fake_files(monkeypatch, external)
    monkeypatch.setattr(autoclip_handoff.subprocess, "run", lambda *args, **kwargs: pytest.fail("should not launch"))
    with pytest.raises(ValueError, match="папке загрузок"):
        autoclip_handoff.run_handoff(
            video=str(external), downloads_dir=str(downloads), platform="youtube_shorts",
        )


def test_autoclip_requires_executable(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    video = root / "downloads" / "selected.mp4"
    _fake_files(monkeypatch, video)
    monkeypatch.setattr(autoclip_handoff.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="не найден"):
        autoclip_handoff.run_handoff(
            video=str(video), downloads_dir=str(video.parent), platform="youtube_shorts",
        )


def test_autoclip_failure_is_not_reported_as_success(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    video = root / "downloads" / "selected.mp4"
    executable = root / "autoclip.exe"
    _fake_files(monkeypatch, video, executable)
    monkeypatch.setattr(autoclip_handoff.shutil, "which", lambda name: str(executable))

    def run(args, **kwargs):
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, "1.5.6", "")
        return subprocess.CompletedProcess(args, 1, "", "model unavailable")

    monkeypatch.setattr(autoclip_handoff.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="кодом 1"):
        autoclip_handoff.run_handoff(
            video=str(video), downloads_dir=str(video.parent), platform="youtube_shorts",
        )


def test_autoclip_project_gemini_key_stays_out_of_command(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    video = root / "downloads" / "selected.mp4"
    executable = root / "autoclip.exe"
    _fake_files(monkeypatch, video, executable)
    monkeypatch.setattr(autoclip_handoff.shutil, "which", lambda name: str(executable))
    monkeypatch.setattr(autoclip_handoff, "dotenv_values", lambda path: {"GEMINI_API_KEY": "test-secret"})
    monkeypatch.setattr(autoclip_handoff, "GEMINI_DATA_DIR", root / "data" / "test-autoclip-gemini")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        output = "1.5.7" if args[1] == "--version" else '{"ok":true}'
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(autoclip_handoff.subprocess, "run", run)
    autoclip_handoff.run_handoff(
        video=str(video), downloads_dir=str(video.parent), platform="youtube_shorts",
        use_project_gemini=True,
    )
    assert all("test-secret" not in " ".join(args) for args, _ in calls)
    assert calls[1][1]["env"]["GEMINI_API_KEY"] == "test-secret"
    assert calls[1][1]["env"]["LLM_PROVIDER"] == "gemini"
    assert calls[1][1]["env"]["LLM_MODEL"] == "gemini-3.5-flash-lite"
    assert calls[1][1]["env"]["AUTOCLIP_DATA_DIR"] == str(autoclip_handoff.GEMINI_DATA_DIR)
