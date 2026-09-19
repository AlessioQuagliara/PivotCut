"""MP4 export via an external FFmpeg binary (H.264, web-ready).

FFmpeg is invoked as a subprocess (``subprocess.run``, no shell) — this
project has no FFmpeg Python bindings as a dependency. This module never
assumes FFmpeg is installed: callers must check :func:`find_ffmpeg` (or
handle :class:`FfmpegNotFoundError`) and keep PNG sequence export available
as a fallback.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from pivotcut.domain.models import Project
from pivotcut.services.export_renderer import save_png_sequence

_HOMEBREW_APPLE_SILICON_FFMPEG = Path("/opt/homebrew/bin/ffmpeg")


class FfmpegNotFoundError(Exception):
    """Raised when no usable ffmpeg binary can be located."""


class FfmpegExecutionError(Exception):
    """Raised when ffmpeg runs but exits with a non-zero status, or produces no usable output."""


class ExportCancelledError(Exception):
    """Raised when the export was cancelled before (or during) the FFmpeg phase."""


def find_ffmpeg() -> Path | None:
    """Locate an ffmpeg binary: ``PATH`` first, then the Apple Silicon Homebrew prefix."""
    found = shutil.which("ffmpeg")
    if found is not None:
        return Path(found)
    if _HOMEBREW_APPLE_SILICON_FFMPEG.is_file():
        return _HOMEBREW_APPLE_SILICON_FFMPEG
    return None


def export_mp4(
    project: Project,
    output_path: Path,
    fps: int | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    project_dir: Path | None = None,
    ffmpeg_path: Path | None = None,
) -> Path:
    """Render the project's exposure-expanded PNG sequence and encode it to H.264 MP4.

    Renders PNG frames into a ``tempfile.TemporaryDirectory`` (always
    cleaned up, including on error/cancel) with an opaque background
    (yuv420p/H.264 has no alpha channel), then invokes::

        ffmpeg -y -framerate {fps} -i frame_%06d.png -c:v libx264 \\
               -pix_fmt yuv420p -movflags +faststart -crf 18 {output_path}

    ``progress_callback(phase, done, total)`` is called with phase
    ``"render"`` while writing PNG frames, and phase ``"encode"`` once
    before and once after invoking ffmpeg (ffmpeg gives no reliable
    machine-readable percentage without parsing its stderr, which this
    milestone does not attempt).

    Raises:
        FfmpegNotFoundError: no ffmpeg binary found (PNG export remains available).
        ExportCancelledError: cancelled before or during the FFmpeg phase.
        FfmpegExecutionError: ffmpeg exited non-zero, or produced no/empty output.
        OSError: filesystem errors writing frames or the final file.
    """
    resolved_ffmpeg = ffmpeg_path if ffmpeg_path is not None else find_ffmpeg()
    if resolved_ffmpeg is None:
        raise FfmpegNotFoundError(
            "ffmpeg was not found on PATH or at /opt/homebrew/bin/ffmpeg. "
            "PNG sequence export remains available. Install ffmpeg with: brew install ffmpeg"
        )

    effective_fps = fps if fps is not None else project.fps

    with tempfile.TemporaryDirectory(prefix="pivotcut-export-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        def _render_progress(done: int, total: int) -> None:
            if progress_callback is not None:
                progress_callback("render", done, total)

        save_png_sequence(
            project,
            temp_dir,
            progress_callback=_render_progress,
            cancel_requested=cancel_requested,
            project_dir=project_dir,
            force_opaque_background=True,
        )

        if cancel_requested is not None and cancel_requested():
            raise ExportCancelledError("Export cancelled before the FFmpeg encoding phase.")

        if progress_callback is not None:
            progress_callback("encode", 0, 1)

        command = [
            str(resolved_ffmpeg),
            "-y",
            "-framerate",
            str(effective_fps),
            "-i",
            str(temp_dir / "frame_%06d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-crf",
            "18",
            str(output_path),
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise FfmpegExecutionError(f"Could not run ffmpeg: {exc}") from exc

        if result.returncode != 0:
            stderr_tail = "\n".join(result.stderr.strip().splitlines()[-20:])
            raise FfmpegExecutionError(f"ffmpeg exited with status {result.returncode}.\n{stderr_tail}")

        if progress_callback is not None:
            progress_callback("encode", 1, 1)

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FfmpegExecutionError(f"ffmpeg reported success but the output file is missing or empty: {output_path}")

    return output_path
