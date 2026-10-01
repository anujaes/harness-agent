"""OCR tools backed by macOS Vision framework (Windows.Media.Ocr on Windows)."""
from concurrent.futures import ThreadPoolExecutor
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
from typing import Any

from ..constants import (
    CWD, MAX_PARALLEL_TOOLS, OCR_MAX_FILES_DEFAULT, OCR_MAX_FILES_CAP,
    OCR_CHARS_PER_IMAGE, OCR_CHARS_PER_IMAGE_CAP, OCR_SCAN_CHARS, OCR_WORKER_MIN,
)
from ..path_resolve import robust_resolve

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp"}

# ── dedup guard ───────────────────────────────────────────────────────────
# Prevents read_images_text from re-scanning a directory that was already
# scanned in the same session. The model should use read_image_text(path)
# for individual files if it needs more detail.
_scanned_directories: set = set()
# ──────────────────────────────────────────────────────────────────────────

IMPORTANT_TEXT_HINTS = (
    "aadhaar", "aadhar", "voter", "election", "identity", "identification",
    "driving", "driver", "license", "licence", "passport", "pan", "ssn",
    "social security", "date of birth", "dob", "government", "address",
    "resume", "curriculum vitae", "experience", "education", "skills",
)


def _resolve_path(path: str) -> pathlib.Path:
    return robust_resolve(path, CWD)


def _clamp_int(value, default: int, min_value: int, max_value: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(min_value, min(max_value, n))


def read_image_text(path: str) -> str:
    """Extract all text from an image file with on-device OCR (macOS Vision
    framework, or Windows.Media.Ocr on Windows)."""
    p = _resolve_path(path)
    if not p.exists():
        return f"ERROR: {path} not found"
    if not p.is_file():
        return f"ERROR: {path} is not a file"
    if sys.platform == "win32":
        return _read_image_text_windows(p)

    swift_code = f'''
import Vision
import Foundation

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest {{ req, err in
    guard let obs = req.results as? [VNRecognizedTextObservation] else {{ return }}
    for o in obs {{
        if let top = o.topCandidates(1).first {{
            print(top.string)
        }}
    }}
}}
request.recognitionLevel = .accurate
let handler = VNImageRequestHandler(url: url, options: [:])
try? handler.perform([request])
'''

    result = subprocess.run(
        ["swift", "-e", swift_code, str(p)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = result.stdout.strip()
    if not output:
        stderr = result.stderr.strip()
        return f"ERROR: No text found in image. stderr: {stderr}" if stderr else "No text detected in image."
    return output


# ── Windows backend (Windows.Media.Ocr via pywinrt) ──────────────────────

_WIN_OCR_TIMEOUT = 60.0
_WIN_LANG_HINT = ("Settings → Time & language → Language & region → add a language "
                  "(its optional 'Optical character recognition' feature)")


class _OcrSetupError(Exception):
    """Windows OCR can't run at all (packages or language pack missing)."""


def _run_coroutine(factory) -> Any:
    """Run ``factory()``'s coroutine to completion from sync code.

    Uses ``asyncio.run`` when this thread has no running loop; otherwise (e.g.
    called from inside an event loop) runs it on a short-lived worker thread
    so the caller's loop is never re-entered.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(factory(), _WIN_OCR_TIMEOUT))
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="win-ocr") as ex:
        future = ex.submit(lambda: asyncio.run(asyncio.wait_for(factory(), _WIN_OCR_TIMEOUT)))
        return future.result(timeout=_WIN_OCR_TIMEOUT + 5)


def _win_ocr_engine():
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as e:
        raise _OcrSetupError(
            "Windows OCR needs the WinRT bindings: pip install winrt-runtime winrt-Windows.Media.Ocr "
            "winrt-Windows.Graphics.Imaging winrt-Windows.Storage winrt-Windows.Storage.Streams "
            f"winrt-Windows.Globalization winrt-Windows.Foundation ({e})"
        ) from e
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        # The profile's languages may lack OCR data while another one has it.
        langs = list(OcrEngine.available_recognizer_languages or [])
        if langs:
            engine = OcrEngine.try_create_from_language(langs[0])
    if engine is None:
        raise _OcrSetupError(f"no Windows OCR language pack is installed — {_WIN_LANG_HINT}")
    return engine


async def _win_ocr_file(path: pathlib.Path) -> str:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage import FileAccessMode, StorageFile

    engine = _win_ocr_engine()
    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    try:
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
    finally:
        stream.close()
    return "\n".join(line.text for line in result.lines if line.text.strip())


def _png_copy_for_ocr(path: pathlib.Path, max_edge: int) -> pathlib.Path | None:
    """A PNG copy the WinRT decoder accepts (long edge ≤ ``max_edge``);
    None when Pillow can't read ``path`` either."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im.load()
            img = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    except Exception:
        return None
    if max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge))
    fd, tmp = tempfile.mkstemp(prefix="jarvis-ocr-", suffix=".png")
    os.close(fd)
    img.save(tmp, "PNG")
    return pathlib.Path(tmp)


def _read_image_text_windows(p: pathlib.Path) -> str:
    try:
        from winrt.windows.media.ocr import OcrEngine

        max_edge = int(OcrEngine.max_image_dimension or 10000)
    except Exception:
        max_edge = 10000
    from .screenshot import image_size

    size = image_size(p)
    too_big = size is not None and max(size) > max_edge
    tmp: pathlib.Path | None = None
    try:
        text = None
        if not too_big:
            try:
                text = _run_coroutine(lambda: _win_ocr_file(p))
            except _OcrSetupError:
                raise
            except Exception:
                text = None  # undecodable here (HEIC without codec, odd TIFF …) or too big
        if text is None:
            tmp = _png_copy_for_ocr(p, max_edge)
            if tmp is None:
                return (f"ERROR: Windows OCR can't decode {p.name} ({p.suffix or 'no extension'}). "
                        "Convert it to PNG or JPEG first (HEIC needs the HEIF Image Extensions "
                        "from the Microsoft Store).")
            text = _run_coroutine(lambda: _win_ocr_file(tmp))
    except _OcrSetupError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR: Windows OCR failed: {type(e).__name__}: {e}"
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return text.strip() or "No text detected in image."


def _discover_images(directory: str, pattern: str) -> list[pathlib.Path]:
    root = _resolve_path(directory)
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.suffix.lower() in IMAGE_EXTENSIONS else []
    return sorted(
        p for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(CWD))
    except ValueError:
        return str(path)


def _score_text(text: str, keywords: list[str] | None) -> int:
    haystack = text.lower()
    terms = [k.lower() for k in (keywords or []) if k] or list(IMPORTANT_TEXT_HINTS)
    score = sum(4 for term in terms if term in haystack)
    score += min(6, sum(1 for token in ("id", "no", "number", "name") if token in haystack))
    return score


def read_images_text(
    paths: list[str] | None = None,
    directory: str = ".",
    pattern: str = "**/*",
    max_files: int = 80,
    max_workers: int | None = None,
    max_chars_per_image: int = 800,
    include_empty: bool = False,
    keywords: list[str] | None = None,
) -> str:
    """OCR many images concurrently and return compact per-file text previews."""
    max_files = _clamp_int(max_files, OCR_MAX_FILES_DEFAULT, 1, OCR_MAX_FILES_CAP)
    max_chars_per_image = _clamp_int(max_chars_per_image, OCR_CHARS_PER_IMAGE, 80, OCR_CHARS_PER_IMAGE_CAP)
    worker_count = _clamp_int(max_workers, min(20, MAX_PARALLEL_TOOLS), 1, MAX_PARALLEL_TOOLS)

    if paths:
        images = []
        for raw in paths[:max_files]:
            p = _resolve_path(raw)
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(p)
    else:
        images = _discover_images(directory, pattern)[:max_files]

    if not images:
        return "No image files found. Supported: PNG, JPG, JPEG, HEIC, TIFF, BMP."

    # ── dedup guard ─────────────────────────────────────────────────────
    # If this directory (+ pattern) was already scanned, refuse to re-scan.
    # The model should use read_image_text() for individual files instead.
    if not paths and directory not in ("", ".", "./"):
        scan_key = str(_resolve_path(directory)) + "::" + pattern
        if scan_key in _scanned_directories:
            file_list = "\n".join(f"  • {_display_path(p)}" for p in images[:10])
            more = f"  … and {len(images) - 10} more" if len(images) > 10 else ""
            return (
                f"[DEDUP — already scanned] All {len(images)} images in "
                f"{directory} were already processed in a previous call.\n"
                f"Use read_image_text('<path>') for individual files if you "
                f"need full text.\n"
                f"Previously scanned files:\n{file_list}{more}"
            )
    # ────────────────────────────────────────────────────────────────────

    total = len(images)
    lock = threading.Lock()
    prog = {"done": 0}

    def ocr_one_tracked(path: pathlib.Path) -> tuple[pathlib.Path, str]:
        from ..repl.turn_progress import report_turn_phase

        text = read_image_text(str(path))
        with lock:
            prog["done"] += 1
            report_turn_phase(f"OCR: {prog['done']}/{total} — {_display_path(path)}")

        return path, text

    rows = []
    with ThreadPoolExecutor(max_workers=min(worker_count, len(images))) as ex:
        for index, (path, text) in enumerate(ex.map(ocr_one_tracked, images)):
            clean = " ".join(text.split())
            if not include_empty and (
                clean == "No text detected in image."
                or clean.startswith("ERROR: No text found")
            ):
                continue
            score = _score_text(clean, keywords)
            if len(clean) > max_chars_per_image:
                clean = clean[:max_chars_per_image].rstrip() + "..."
            label = "LIKELY IMPORTANT" if score else "TEXT"
            rows.append((score, index, f"FILE: {_display_path(path)}\n{label}: {clean}"))

    skipped = len(images) - len(rows)
    rows.sort(key=lambda row: (-row[0], row[1]))
    important = sum(1 for score, _, _ in rows if score > 0)
    header = (
        f"OCR scanned {len(images)} image(s) with {min(worker_count, len(images))} worker(s)."
        + (f" Prioritized {important} likely important result(s)." if important else "")
        + (f" Suppressed {skipped} empty/no-text result(s)." if skipped else "")
    )
    result = header + ("\n\n" + "\n\n".join(row for _, _, row in rows) if rows else "\nNo text detected in scanned images.")

    # Record directory+pattern as scanned for dedup guard
    if not paths and directory not in ("", ".", "./"):
        _scanned_directories.add(str(_resolve_path(directory)) + "::" + pattern)

    return result
