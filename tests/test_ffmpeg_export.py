from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pivotcut.domain.models import Frame, Project, SceneSettings
from pivotcut.services import ffmpeg_export

pytestmark = pytest.mark.usefixtures("qapp")


def make_project(frame_count: int = 2, exposure: int = 2, fps: int = 24) -> Project:
    frames = [Frame(id=f"f-{i}", duration=1, label=f"Frame {i}") for i in range(frame_count)]
    return Project(scene_settings=SceneSettings(width=64, height=48), fps=fps, exposure=exposure, frames=frames)


def _fake_ffmpeg_binary(tmp_path: Path) -> Path:
    """A stand-in path used only as a string in mocked commands: never executed for real."""
    return tmp_path / "ffmpeg"


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


# -- find_ffmpeg ------------------------------------------------------------------


def test_find_ffmpeg_returns_path_on_path() -> None:
    with patch("shutil.which", return_value="/usr/local/bin/ffmpeg"):
        assert ffmpeg_export.find_ffmpeg() == Path("/usr/local/bin/ffmpeg")


def test_find_ffmpeg_falls_back_to_homebrew_apple_silicon_path() -> None:
    with patch("shutil.which", return_value=None), patch.object(Path, "is_file", return_value=True):
        assert ffmpeg_export.find_ffmpeg() == ffmpeg_export._HOMEBREW_APPLE_SILICON_FFMPEG


def test_find_ffmpeg_returns_none_when_not_found_anywhere() -> None:
    with patch("shutil.which", return_value=None), patch.object(Path, "is_file", return_value=False):
        assert ffmpeg_export.find_ffmpeg() is None


# -- export_mp4: ffmpeg not found --------------------------------------------------


def test_export_mp4_raises_typed_error_when_ffmpeg_not_found(tmp_path: Path) -> None:
    project = make_project()
    with patch("pivotcut.services.ffmpeg_export.find_ffmpeg", return_value=None):
        with pytest.raises(ffmpeg_export.FfmpegNotFoundError):
            ffmpeg_export.export_mp4(project, tmp_path / "out.mp4")


def test_export_mp4_error_message_mentions_brew_install(tmp_path: Path) -> None:
    project = make_project()
    with patch("pivotcut.services.ffmpeg_export.find_ffmpeg", return_value=None):
        with pytest.raises(ffmpeg_export.FfmpegNotFoundError, match="brew install ffmpeg"):
            ffmpeg_export.export_mp4(project, tmp_path / "out.mp4")


# -- export_mp4: command construction ----------------------------------------------


def test_export_mp4_builds_expected_ffmpeg_command(tmp_path: Path) -> None:
    project = make_project(frame_count=2, exposure=2, fps=30)
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)
    captured_command: list[str] = []

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        captured_command.extend(command)
        output_path.write_bytes(b"fake-mp4-bytes")
        return _FakeCompletedProcess(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)

    assert captured_command[0] == str(fake_ffmpeg)
    assert captured_command[1] == "-y"
    assert "-framerate" in captured_command
    assert captured_command[captured_command.index("-framerate") + 1] == "30"
    assert "-i" in captured_command
    assert captured_command[captured_command.index("-i") + 1].endswith("frame_%06d.png")
    assert "-c:v" in captured_command
    assert captured_command[captured_command.index("-c:v") + 1] == "libx264"
    assert "-pix_fmt" in captured_command
    assert captured_command[captured_command.index("-pix_fmt") + 1] == "yuv420p"
    assert "-movflags" in captured_command
    assert captured_command[captured_command.index("-movflags") + 1] == "+faststart"
    assert "-crf" in captured_command
    assert captured_command[captured_command.index("-crf") + 1] == "18"
    assert captured_command[-1] == str(output_path)


# -- export_mp4: subprocess error handling -----------------------------------------


def test_export_mp4_raises_on_nonzero_exit_code(tmp_path: Path) -> None:
    project = make_project()
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        return _FakeCompletedProcess(returncode=1, stderr="ffmpeg: unknown option")

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ffmpeg_export.FfmpegExecutionError, match="unknown option"):
            ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)


def test_export_mp4_raises_when_subprocess_run_raises_oserror(tmp_path: Path) -> None:
    project = make_project()
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    with patch("subprocess.run", side_effect=OSError("permission denied")):
        with pytest.raises(ffmpeg_export.FfmpegExecutionError):
            ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)


def test_export_mp4_raises_if_output_file_missing_after_success(tmp_path: Path) -> None:
    project = make_project()
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        # ffmpeg reports success but never actually wrote the file.
        return _FakeCompletedProcess(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ffmpeg_export.FfmpegExecutionError, match="missing or empty"):
            ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)


def test_export_mp4_raises_if_output_file_is_empty_after_success(tmp_path: Path) -> None:
    project = make_project()
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        output_path.write_bytes(b"")
        return _FakeCompletedProcess(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(ffmpeg_export.FfmpegExecutionError, match="missing or empty"):
            ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)


# -- export_mp4: success path -------------------------------------------------------


def test_export_mp4_success_returns_output_path(tmp_path: Path) -> None:
    project = make_project()
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        output_path.write_bytes(b"fake-mp4-bytes")
        return _FakeCompletedProcess(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        result = ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)

    assert result == output_path
    assert output_path.stat().st_size > 0


def test_export_mp4_reports_render_and_encode_progress_phases(tmp_path: Path) -> None:
    project = make_project(frame_count=2, exposure=2)
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)
    phases: list[str] = []

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        output_path.write_bytes(b"fake-mp4-bytes")
        return _FakeCompletedProcess(returncode=0)

    def on_progress(phase: str, done: int, total: int) -> None:
        phases.append(phase)

    with patch("subprocess.run", side_effect=fake_run):
        ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg, progress_callback=on_progress)

    assert "render" in phases
    assert "encode" in phases


# -- export_mp4: cancellation -------------------------------------------------------


def test_export_mp4_cancelled_before_encode_never_invokes_subprocess(tmp_path: Path) -> None:
    project = make_project(frame_count=4, exposure=3)
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    with patch("subprocess.run") as mock_run:
        with pytest.raises(ffmpeg_export.ExportCancelledError):
            ffmpeg_export.export_mp4(
                project, output_path, ffmpeg_path=fake_ffmpeg, cancel_requested=lambda: True
            )
        mock_run.assert_not_called()


def test_export_mp4_temp_directory_cleaned_up_on_cancellation(tmp_path: Path) -> None:
    project = make_project(frame_count=4, exposure=3)
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)
    captured_temp_dirs: list[Path] = []

    original_save = ffmpeg_export.save_png_sequence

    def spying_save(proj, out_dir, **kwargs):  # noqa: ANN001 - test double
        captured_temp_dirs.append(out_dir)
        return original_save(proj, out_dir, **kwargs)

    with patch("pivotcut.services.ffmpeg_export.save_png_sequence", side_effect=spying_save):
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ffmpeg_export.ExportCancelledError):
                ffmpeg_export.export_mp4(
                    project, output_path, ffmpeg_path=fake_ffmpeg, cancel_requested=lambda: True
                )
            mock_run.assert_not_called()

    assert len(captured_temp_dirs) == 1
    assert not captured_temp_dirs[0].exists()


def test_export_mp4_temp_directory_cleaned_up_on_ffmpeg_failure(tmp_path: Path) -> None:
    project = make_project()
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)
    captured_temp_dirs: list[Path] = []

    original_save = ffmpeg_export.save_png_sequence

    def spying_save(proj, out_dir, **kwargs):  # noqa: ANN001 - test double
        captured_temp_dirs.append(out_dir)
        return original_save(proj, out_dir, **kwargs)

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        return _FakeCompletedProcess(returncode=1, stderr="boom")

    with patch("pivotcut.services.ffmpeg_export.save_png_sequence", side_effect=spying_save):
        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(ffmpeg_export.FfmpegExecutionError):
                ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)

    assert len(captured_temp_dirs) == 1
    assert not captured_temp_dirs[0].exists()


# -- export_mp4: renders with opaque background for ffmpeg -------------------------


def test_export_mp4_renders_png_frames_with_opaque_background(tmp_path: Path) -> None:
    from PySide6.QtGui import QImage

    project = make_project(frame_count=1, exposure=1)
    project.scene_settings.background_color = "#00000000"  # fully transparent
    output_path = tmp_path / "out.mp4"
    fake_ffmpeg = _fake_ffmpeg_binary(tmp_path)

    observed_alpha: dict[str, int] = {}

    def fake_run(command, capture_output, text, check):  # noqa: ANN001 - test double
        # Read the actual rendered frame while the temp dir still exists
        # (before the export_mp4's TemporaryDirectory context manager
        # cleans it up) to confirm force_opaque_background really flattened
        # the configured fully-transparent background before ffmpeg sees it.
        input_pattern = command[command.index("-i") + 1]
        first_frame = Path(input_pattern).parent / "frame_000001.png"
        image = QImage(str(first_frame))
        observed_alpha["value"] = image.pixelColor(0, 0).alpha()
        output_path.write_bytes(b"fake-mp4-bytes")
        return _FakeCompletedProcess(returncode=0)

    with patch("subprocess.run", side_effect=fake_run):
        ffmpeg_export.export_mp4(project, output_path, ffmpeg_path=fake_ffmpeg)

    assert observed_alpha["value"] == 255
