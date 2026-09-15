import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


_PUBLIC_OPTIONS = ['--ignore-config', '--no-cache-dir', '--no-playlist', '--skip-download']


def _video_url(url: str) -> str:
    url = url.strip()
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
            or parsed.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}):
        raise ValueError('Use a public YouTube video URL without credentials.')
    return url


def _ytdlp_cmd() -> list[str] | None:
    """Return the base command for invoking yt-dlp, or None if unavailable.

    Prefers ``python -m yt_dlp`` so the dependency declared in pyproject is used
    even under isolated runners (uvx/pipx) where the console script may not be on
    PATH; falls back to a ``yt-dlp`` binary on PATH.
    """
    try:
        import yt_dlp  # noqa: F401

        return [sys.executable, "-m", "yt_dlp", *_PUBLIC_OPTIONS]
    except Exception:
        pass
    if shutil.which("yt-dlp") is not None:
        return ["yt-dlp", *_PUBLIC_OPTIONS]
    return None


_YTDLP_MISSING = (
    "yt-dlp is not installed. Install it with: pip install yt-dlp\n"
    "Alternatively, paste the video description/transcript text directly."
)

# Windows: never let a child attach to this server's console.
#
# Diagnosed 2026-08-09 after every yt-dlp call timed out while the identical command
# ran fine from any shell. The child process WAS created, but sat at 0s CPU with its
# loader threads waiting on EventPairLow — the LPC handshake with the console host —
# until the timeout killed it, leaking a frozen process per attempt. Cause: the
# harness launches this server with a console whose conhost.exe never finishes
# initializing (2 threads, ~0.01s CPU, alive but inert), so any child inheriting that
# console blocks forever attaching to it. Nothing to do with YouTube, yt-dlp's
# version, or network.
#
# CREATE_NO_WINDOW gives the child its own (windowless) console instead of the
# parent's broken one. stdin=DEVNULL is matching hygiene: a server's stdin is the MCP
# pipe and no child of ours should ever read it.
_NO_CONSOLE_FLAGS = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a child process detached from this server's console. See above."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        creationflags=_NO_CONSOLE_FLAGS,
    )


def _tail(text: str | None, n: int = 12) -> str:
    """Last ``n`` non-blank lines of some captured output, for diagnostics."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _timeout_message(kind: str, url: str, exc: subprocess.TimeoutExpired) -> str:
    """Turn an opaque timeout into a diagnosable one by surfacing yt-dlp's partial
    output. ``TimeoutExpired`` carries whatever was captured before the process was
    killed; showing the tail reveals which stage stalled (webpage / player API /
    timedtext download). The attributes may be bytes or None depending on progress.
    """
    def _decode(b: object) -> str:
        if b is None:
            return ""
        return b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)

    partial = _tail(_decode(exc.stderr) or _decode(exc.stdout))
    detail = f"\nLast yt-dlp output before the stall:\n{partial}" if partial else ""
    return (
        f"Timed out fetching YouTube {kind} for: {url}\n"
        "The request did not complete; transcript availability is unknown." + detail
    )

# Links worth extracting from a build guide description
_LINK_PATTERNS = {
    "pobb.in": re.compile(r"https?://pobb\.in/\S+"),
    "poedb.tw": re.compile(r"https?://poedb\.tw/\S+PathOfBuilding\?id=\S+"),
    "pastebin": re.compile(r"https?://pastebin\.com/\S+"),
    "mobalytics": re.compile(r"https?://mobalytics\.gg/(?:poe|poe-2)/\S+"),
    "maxroll": re.compile(r"https?://maxroll\.gg/(?:poe|poe2)/\S+"),
    "poe_forum": re.compile(r"https?://www\.pathofexile\.com/forum/view-thread/\d+"),
}


def fetch_youtube_description(url: str) -> str:
    """Fetch the title and description from a YouTube video URL using yt-dlp.

    Extracts any Path of Building links (pobb.in, poedb.tw, pastebin) and
    common guide site links (Mobalytics, Maxroll) found in the description.
    Requires yt-dlp to be installed (pip install yt-dlp).

    Args:
        url: YouTube video URL (e.g. https://www.youtube.com/watch?v=...)
    """
    url = _video_url(url)
    ytdlp = _ytdlp_cmd()
    if ytdlp is None:
        return _YTDLP_MISSING

    try:
        title_result = _run(
            [*ytdlp, "--get-title", "--force-ipv4", "--socket-timeout", "20",
             "--no-warnings", "--", url], timeout=30
        )
        desc_result = _run(
            [*ytdlp, "--get-description", "--force-ipv4", "--socket-timeout", "20",
             "--no-warnings", "--", url], timeout=30
        )
    except subprocess.TimeoutExpired as e:
        return _timeout_message("description", url, e)
    except Exception as e:
        return f"Failed to run yt-dlp: {e}"

    if desc_result.returncode != 0:
        err = desc_result.stderr.strip()
        return f"yt-dlp failed for {url}: {err or 'unknown error'}"

    title = title_result.stdout.strip()
    description = desc_result.stdout.strip()

    # Extract links
    found: dict[str, list[str]] = {}
    for label, pattern in _LINK_PATTERNS.items():
        matches = [m.rstrip(".,)") for m in pattern.findall(description)]
        if matches:
            found[label] = list(dict.fromkeys(matches))  # dedupe, preserve order

    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    if found:
        lines.append("## Extracted Links")
        for label, links in found.items():
            for link in links:
                lines.append(f"- **{label}:** {link}")
        lines.append("")

    lines.append("## Description")
    lines.append(description)

    return "\n".join(lines)


def fetch_youtube_transcript(url: str, include_timestamps: bool = False) -> str:
    """Fetch the auto-generated transcript from a YouTube video using yt-dlp.

    Returns the full spoken transcript as clean text, optionally with
    timestamps. Useful for extracting build theory reasoning that creators
    explain verbally but don't write in the description.

    Chapter markers from the video description are shown at the top so you
    can navigate to specific sections (gear, passive tree, skill gems, etc.).

    Args:
        url: YouTube video URL (e.g. https://www.youtube.com/watch?v=...)
        include_timestamps: If True, include time markers (MM:SS) inline.
                            Default False returns clean readable prose.
    """
    url = _video_url(url)
    ytdlp = _ytdlp_cmd()
    if ytdlp is None:
        return _YTDLP_MISSING

    # Get title and chapter list from description first (lightweight)
    try:
        title_result = _run(
            [*ytdlp, "--get-title", "--force-ipv4", "--socket-timeout", "20",
             "--no-warnings", "--", url], timeout=15
        )
        desc_result = _run(
            [*ytdlp, "--get-description", "--force-ipv4", "--socket-timeout", "20",
             "--no-warnings", "--", url], timeout=15
        )
        title = title_result.stdout.strip()
        description = desc_result.stdout.strip()
    except Exception:
        title = ""
        description = ""

    # Extract chapter timestamps from description
    chapters = re.findall(r'(\d+:\d+(?::\d+)?)\s+(.+)', description)

    # Download transcript to temp dir
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "transcript")
        # --socket-timeout makes a stalled connection fail fast instead of
        # blocking forever; bounded --retries then recovers. Without these,
        # yt-dlp's defaults (no socket timeout, 10 retries) can hang past the
        # wrapper timeout on an intermittently stalled timedtext download.
        try:
            result = _run(
                [*ytdlp, "--write-auto-subs", "--sub-lang", "en",
                 "--sub-format", "json3", "--skip-download", "--no-warnings",
                 "--force-ipv4", "--socket-timeout", "20", "--retries", "3",
                 "-o", out_path, "--", url], timeout=120
            )
        except subprocess.TimeoutExpired as e:
            return _timeout_message("transcript", url, e)
        if result.returncode != 0:
            err = result.stderr.strip()
            return f"yt-dlp failed to fetch transcript: {err or 'unknown error'}"

        # Find the downloaded file
        import glob
        files = glob.glob(os.path.join(tmpdir, "*.json3"))
        if not files:
            return "No transcript available for this video (auto-captions may be disabled)."

        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)

    # Parse json3 events into text segments with optional timestamps
    segments = []
    for event in data.get("events", []):
        t_ms = event.get("tStartMs", 0)
        text = "".join(s.get("utf8", "") for s in event.get("segs", [])).strip()
        if text and text != "\n":
            segments.append((t_ms, text))

    if not segments:
        return "Transcript was empty or could not be parsed."

    # Build output
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    if chapters:
        lines.append("## Chapters")
        for ts, name in chapters:
            lines.append(f"- {ts} — {name}")
        lines.append("")

    lines.append("## Transcript")

    if include_timestamps:
        for t_ms, text in segments:
            secs = int(t_ms / 1000)
            mm, ss = divmod(secs, 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {text}")
    else:
        # Join into clean prose, deduplicating overlapping auto-caption fragments
        words = []
        prev = ""
        for _, text in segments:
            text = re.sub(r'\s+', ' ', text).strip()
            if text and text != prev:
                words.append(text)
                prev = text
        lines.append(" ".join(words))

    total_chars = sum(len(t) for _, t in segments)
    lines.append(f"\n*Transcript: ~{total_chars:,} characters*")

    return "\n".join(lines)
