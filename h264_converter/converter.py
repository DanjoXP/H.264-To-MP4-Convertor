import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple


def get_ffmpeg_executable() -> str:
    """Finds available FFmpeg executable from system PATH or imageio-ffmpeg."""
    # Check system PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    # Fallback to imageio-ffmpeg package
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        raise RuntimeError(
            "FFmpeg executable not found. Please install imageio-ffmpeg or add ffmpeg to your system PATH."
        ) from e


def scan_for_264_files(input_path: str) -> List[str]:
    """
    Scans a file or directory for .264 or .h264 video files.
    Returns a sorted list of absolute file paths.
    """
    valid_extensions = {".264", ".h264"}
    files_found = []

    path = Path(input_path)
    if not path.exists():
        return []

    if path.is_file():
        if path.suffix.lower() in valid_extensions:
            files_found.append(str(path.resolve()))
    elif path.is_dir():
        for root, _, files in os.walk(path):
            for file in files:
                ext = Path(file).suffix.lower()
                if ext in valid_extensions:
                    files_found.append(str(Path(root) / file))

    return sorted(files_found)


def calculate_output_path(
    input_file: str,
    input_base_dir: Optional[str],
    output_dir: str,
    preserve_structure: bool = True
) -> str:
    """
    Determines output MP4 filepath for a given input file and destination directory.
    """
    in_path = Path(input_file)
    stem = in_path.stem
    out_dir = Path(output_dir)

    if preserve_structure and input_base_dir and Path(input_base_dir).is_dir():
        try:
            rel_path = in_path.relative_to(Path(input_base_dir))
            target_dir = out_dir / rel_path.parent
        except ValueError:
            target_dir = out_dir
    else:
        target_dir = out_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    return str(target_dir / f"{stem}.mp4")


def count_frames_in_264(input_file: str, ffmpeg_exe: Optional[str] = None) -> int:
    """
    Counts total video frames in a raw .264 elementary stream file using FFmpeg.
    """
    if ffmpeg_exe is None:
        ffmpeg_exe = get_ffmpeg_executable()

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-i", input_file,
        "-map", "0:v:0",
        "-c", "copy",
        "-f", "null",
        "-"
    ]

    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    process = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        startupinfo=startupinfo
    )

    matches = re.findall(r"frame=\s*(\d+)", process.stderr)
    if matches:
        return int(matches[-1])
    return 0


def probe_264_framerate(input_file: str, ffmpeg_exe: Optional[str] = None) -> Optional[float]:
    """
    Probes raw .264 elementary stream for SPS header hardware framerate using ffprobe.
    Returns float FPS if a valid camera framerate is detected (e.g. 5.0, 10.0, 12.0, 15.0, 25.0, 30.0), else None.
    """
    if ffmpeg_exe is None:
        ffmpeg_exe = get_ffmpeg_executable()

    ffprobe_exe = os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    if not os.path.exists(ffprobe_exe):
        ffprobe_exe = shutil.which("ffprobe")

    if ffprobe_exe and os.path.exists(ffprobe_exe):
        cmd = [
            ffprobe_exe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,avg_frame_rate",
            "-of", "default=noprint_wrappers=1:1",
            input_file
        ]
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo)
            for line in res.stdout.splitlines():
                if "=" in line:
                    _, val = line.split("=", 1)
                    if "/" in val:
                        num, den = val.split("/", 1)
                        try:
                            num_f, den_f = float(num), float(den)
                            if den_f > 0:
                                parsed_fps = num_f / den_f
                                if 1.0 <= parsed_fps <= 120.0 and parsed_fps not in (1000.0, 90000.0):
                                    return round(parsed_fps, 3)
                        except ValueError:
                            pass
        except Exception:
            pass

    return None


def _run_ffmpeg_cmd(
    cmd: List[str],
    startupinfo=None,
    cancel_check: Optional[Callable[[], bool]] = None,
    proc_callback: Optional[Callable[[subprocess.Popen], None]] = None
) -> Tuple[int, str]:
    """Runs FFmpeg command with support for process tracking and instant cancellation."""
    import time
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        startupinfo=startupinfo
    )

    if proc_callback:
        proc_callback(process)

    while process.poll() is None:
        if cancel_check and cancel_check():
            try:
                process.kill()
            except Exception:
                pass
            return -1, "Cancelled by user"
        time.sleep(0.05)

    _, stderr = process.communicate()
    return process.returncode, stderr or ""


def convert_264_to_mp4(
    input_file: str,
    output_file: str,
    fps: Optional[float] = None,
    target_duration_sec: Optional[float] = None,
    ffmpeg_exe: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    proc_callback: Optional[Callable[[subprocess.Popen], None]] = None
) -> Tuple[bool, str]:
    """
    Converts a single .264 file to .mp4.
    Supports instant cancellation and active process tracking.
    """
    if cancel_check and cancel_check():
        return False, "Cancelled by user"

    if ffmpeg_exe is None:
        ffmpeg_exe = get_ffmpeg_executable()

    # Make sure parent directory exists
    dir_name = os.path.dirname(output_file)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    calculated_fps_str = None

    # Calculate FPS if target duration is requested
    if target_duration_sec and target_duration_sec > 0 and (fps is None or fps <= 0):
        if progress_callback:
            progress_callback(f"Analyzing frame count for {Path(input_file).name}...")
        total_frames = count_frames_in_264(input_file, ffmpeg_exe=ffmpeg_exe)
        if total_frames > 0:
            calc_fps = total_frames / target_duration_sec
            calculated_fps_str = f"{calc_fps:.4f}"
            if progress_callback:
                progress_callback(
                    f"Counted {total_frames:,} frames -> Calculated FPS: {calculated_fps_str} for {target_duration_sec/3600:.1f}h duration"
                )
    elif fps and fps > 0:
        calculated_fps_str = str(fps)

    # Automatic FPS detection if none provided
    if not calculated_fps_str:
        detected_fps = probe_264_framerate(input_file, ffmpeg_exe=ffmpeg_exe)
        if detected_fps:
            calculated_fps_str = str(detected_fps)
            if progress_callback:
                progress_callback(f"Auto-detected camera hardware FPS: {detected_fps} for {Path(input_file).name}")
        else:
            if progress_callback:
                progress_callback(f"Analyzing frame count for auto-speed detection: {Path(input_file).name}...")
            total_frames = count_frames_in_264(input_file, ffmpeg_exe=ffmpeg_exe)
            if total_frames > 0:
                est_duration = 3600.0 if total_frames >= 1000 else 1800.0
                calc_fps = round(total_frames / est_duration, 4)
                calculated_fps_str = str(calc_fps)
                if progress_callback:
                    progress_callback(
                        f"Auto-fit: {total_frames:,} frames / {est_duration/3600:.1f}h -> Auto FPS: {calculated_fps_str}"
                    )
            else:
                calculated_fps_str = "25"

    fps_args = ["-r", calculated_fps_str] if calculated_fps_str else []

    # Hide console window on Windows
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    # Attempt 1: Fast Stream Copy (-c:v copy) with +genpts and +faststart
    cmd_copy = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "+genpts"
    ] + fps_args + [
        "-i", input_file,
        "-c:v", "copy",
        "-movflags", "+faststart",
        output_file
    ]

    if progress_callback:
        fps_info = f" @ {calculated_fps_str} FPS" if calculated_fps_str else ""
        progress_callback(f"Converting (stream copy{fps_info}): {Path(input_file).name}")

    ret1, err1 = _run_ffmpeg_cmd(cmd_copy, startupinfo, cancel_check, proc_callback)
    if cancel_check and cancel_check():
        return False, "Cancelled by user"

    if ret1 == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
        return True, f"Stream copy successful ({calculated_fps_str or 'auto'} FPS) -> {os.path.basename(output_file)}"

    # Attempt 2: Stream Copy with AnnexB bitstream filter (-bsf:v h264_mp4toannexb)
    cmd_copy_bsf = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "+genpts"
    ] + fps_args + [
        "-i", input_file,
        "-c:v", "copy",
        "-bsf:v", "h264_mp4toannexb",
        "-movflags", "+faststart",
        output_file
    ]

    ret2, err2 = _run_ffmpeg_cmd(cmd_copy_bsf, startupinfo, cancel_check, proc_callback)
    if cancel_check and cancel_check():
        return False, "Cancelled by user"

    if ret2 == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
        return True, f"Stream copy (annexb) successful -> {os.path.basename(output_file)}"

    # Attempt 3: High-quality Transcoding Fallback (libx264 -crf 18)
    if progress_callback:
        progress_callback(f"Transcoding fallback (libx264): {Path(input_file).name}")

    cmd_encode = [
        ffmpeg_exe,
        "-y",
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "+genpts"
    ] + fps_args + [
        "-i", input_file,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_file
    ]

    ret3, err3 = _run_ffmpeg_cmd(cmd_encode, startupinfo, cancel_check, proc_callback)
    if cancel_check and cancel_check():
        return False, "Cancelled by user"

    if ret3 == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
        return True, f"Transcoded successfully -> {os.path.basename(output_file)}"

    return False, f"Failed to convert {os.path.basename(input_file)}: {err3 or 'Unknown error'}"
