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
