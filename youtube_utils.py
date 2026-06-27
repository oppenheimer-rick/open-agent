import subprocess
import json
from pathlib import Path
import time
import sys
import os
import socket
import tempfile
import shutil

# Platform-specific imports for keyboard capture
_IS_WINDOWS = sys.platform == "win32"
if not _IS_WINDOWS:
    import tty
    import termios
    import select

# Cross-platform IPC socket path
MPV_SOCK_PATH = str(Path(tempfile.gettempdir()) / "open_agent_mpv.sock")

# Always store downloads in the openagent install dir, regardless of cwd
DOWNLOADS_DIR = Path.home() / ".openagent" / "downloads"


CURRENT_SONG = None  # Holds dict with 'title', 'filename', 'filepath', 'proc'


def find_media_player(prefer_no_video: bool = False):
    """Locate mpv or vlc on the current platform. Returns (binary, args_prefix) or (None, [])."""
    candidates = []
    if _IS_WINDOWS:
        # Common Windows install paths
        win_paths = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ]
        candidates = [
            ("mpv", ["mpv.exe"]),
            ("vlc", [str(p / "VideoLAN" / "VLC" / "vlc.exe") for p in win_paths]),
        ]
    elif sys.platform == "darwin":
        candidates = [
            ("mpv", ["mpv", "/usr/local/bin/mpv", "/opt/homebrew/bin/mpv"]),
            ("vlc", ["/Applications/VLC.app/Contents/MacOS/VLC", "vlc"]),
        ]
    else:  # Linux
        candidates = [
            ("mpv", ["mpv"]),
            ("vlc", ["cvlc", "vlc"]),  # cvlc = VLC without GUI (for background audio)
        ]

    for player_name, paths in candidates:
        for path in paths:
            resolved = shutil.which(path) or (Path(path).exists() and path)
            if resolved:
                return player_name, str(resolved)
    return None, None

def _tokenize(s: str) -> set:
    """Lowercase word-set from a string, stripping punctuation for fuzzy matching."""
    import re
    return set(re.sub(r"[^\w\s]", "", s.lower()).split())


def fuzzy_find_in_library(query: str, suffix: str) -> str | None:
    """
    Look through DOWNLOADS_DIR for an existing file whose name is a close
    enough match to *query*.  Returns the file path string if found, else None.

    Matching strategy (in order):
      1. Exact stem match (case-insensitive).
      2. Every word in the query appears in the filename.
      3. >=60 % of query words appear in the filename.
    """
    if not DOWNLOADS_DIR.exists():
        return None

    candidates = list(DOWNLOADS_DIR.glob(f"*{suffix}"))
    if not candidates:
        return None

    q_tokens = _tokenize(query)
    if not q_tokens:
        return None

    # Strip common stop-words that clog matching
    stop = {"a", "an", "the", "by", "of", "in", "to", "and", "play",
            "me", "song", "music", "video", "watch", "please"}
    q_tokens -= stop
    if not q_tokens:
        return None

    best_file, best_score = None, 0.0
    for f in candidates:
        f_tokens = _tokenize(f.stem)
        # Exact stem
        if f.stem.lower() == query.lower():
            return str(f)
        # Overlap score
        overlap = len(q_tokens & f_tokens)
        score = overlap / len(q_tokens)
        if score > best_score:
            best_score, best_file = score, f

    if best_score >= 0.6 and best_file:
        return str(best_file)
    return None


def list_library() -> list[str]:
    """Return sorted list of filenames in DOWNLOADS_DIR."""
    if not DOWNLOADS_DIR.exists():
        return []
    files = sorted(DOWNLOADS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.name for f in files if f.is_file()]


def youtube_search(query: str, max_results: int = 5) -> list:
    """Search YouTube and return a list of video information dicts."""
    try:
        cmd = [
            "yt-dlp",
            f"ytsearch{max_results}:{query}",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10.0)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            entries = data.get("entries", [])
            results = []
            for entry in entries:
                if entry:
                    results.append({
                        "title": entry.get("title"),
                        "id": entry.get("id"),
                        "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                        "duration": entry.get("duration"),
                        "uploader": entry.get("uploader")
                    })
            return results
    except Exception as e:
        return [{"error": str(e)}]
    return []

def youtube_fetch_transcript(video_id: str) -> str:
    """Fetch the English transcript of a YouTube video using youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        srt = api.fetch(video_id)
        return " ".join([entry.text for entry in srt])
    except Exception as e:
        return f"Error: Could not fetch transcript for {video_id}: {e}"

def download_media(query_or_url: str, video: bool = True) -> str | None:
    """Download video or audio of a video/search query to DOWNLOADS_DIR."""
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dl_str = str(DOWNLOADS_DIR)

    target = query_or_url
    if not target.startswith(("http://", "https://", "www.youtube.com")):
        target = f"ytsearch1:{query_or_url}"

    suffix = ".mp4" if video else ".mp3"
    try:
        # ── Ask yt-dlp what it would name the file (no download yet) ──────────
        tmpl = f"{dl_str}/%(title)s.%(ext)s"
        if video:
            cmd_filename = ["yt-dlp", "--print", "filename",
                            "-f", "best[ext=mp4]/best",
                            "-o", tmpl, "--no-playlist", target]
        else:
            cmd_filename = ["yt-dlp", "--print", "filename",
                            "-x", "--audio-format", "mp3",
                            "-o", tmpl, "--no-playlist", target]

        res_file = subprocess.run(cmd_filename, capture_output=True, text=True, timeout=12.0)
        expected_filename = None
        if res_file.returncode == 0:
            raw = res_file.stdout.strip().splitlines()[-1].strip()
            expected_path = Path(raw)
            if not video:
                expected_path = expected_path.with_suffix(".mp3")
            expected_filename = str(expected_path)

        # ── Skip download if exact file already exists ────────────────────────
        if expected_filename and Path(expected_filename).exists():
            print(f"  ✓ Already in library: {Path(expected_filename).name}")
            return expected_filename

        # ── Download ──────────────────────────────────────────────────────────
        if video:
            cmd_download = ["yt-dlp", "-f", "best[ext=mp4]/best",
                            "-o", tmpl, "--no-playlist", "--no-warnings", target]
        else:
            cmd_download = ["yt-dlp", "-x", "--audio-format", "mp3",
                            "-o", tmpl, "--no-playlist", "--no-warnings", target]
            
        proc = subprocess.Popen(
            cmd_download,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        print("  [yt-dlp] Downloading...")
        for line in proc.stdout:
            line = line.strip()
            if "[download]" in line and "%" in line:
                sys.stdout.write(f"\r  {line}")
                sys.stdout.flush()
        proc.wait()
        sys.stdout.write("\r  [yt-dlp] Download complete!                           \n")
        sys.stdout.flush()

        if expected_filename and Path(expected_filename).exists():
            return expected_filename

        # Fallback: newest matching file in DOWNLOADS_DIR
        files = list(DOWNLOADS_DIR.glob(f"*{suffix}"))
        if files:
            return str(max(files, key=lambda p: p.stat().st_mtime))
    except Exception:
        pass
    return None

def get_key_nonblocking() -> str | None:
    """Non-blocking key read; cross-platform (Windows + Unix)."""
    if _IS_WINDOWS:
        import msvcrt
        if msvcrt.kbhit():
            return msvcrt.getwch()
        return None
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
        if rlist:
            key = sys.stdin.read(1)
            return key
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return None

def send_mpv_ipc_command(command: list) -> dict | None:
    if _IS_WINDOWS:
        return None  # mpv IPC uses UNIX sockets; not available on Windows
    if not os.path.exists(MPV_SOCK_PATH):
        return None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.2)
        client.connect(MPV_SOCK_PATH)
        payload = json.dumps({"command": command}) + "\n"
        client.sendall(payload.encode())
        response = client.recv(4096).decode()
        client.close()
        for line in response.splitlines():
            if line.strip():
                return json.loads(line)
    except Exception:
        pass
    return None

def is_mpv_paused() -> bool:
    res = send_mpv_ipc_command(["get_property", "pause"])
    if res and res.get("error") == "success":
        return bool(res.get("data"))
    return False

def get_background_player_status() -> dict | None:
    global CURRENT_SONG
    if CURRENT_SONG and CURRENT_SONG.get("proc"):
        if CURRENT_SONG["proc"].poll() is None:
            paused = is_mpv_paused()
            return {
                "title": CURRENT_SONG["title"],
                "filename": CURRENT_SONG["filename"],
                "filepath": CURRENT_SONG["filepath"],
                "paused": paused
            }
        else:
            CURRENT_SONG = None
    return None

def toggle_background_play() -> str:
    status = get_background_player_status()
    if not status:
        return "No player active."
    res = send_mpv_ipc_command(["cycle", "pause"])
    if res and res.get("error") == "success":
        paused = not status["paused"]
        return f"{'Paused' if paused else 'Resumed'} playback."
    return "Failed to toggle play state."

def stop_background_play() -> str:
    global CURRENT_SONG
    status = get_background_player_status()
    if not status:
        return "No player active."
    send_mpv_ipc_command(["quit"])
    if CURRENT_SONG and CURRENT_SONG.get("proc"):
        try:
            CURRENT_SONG["proc"].terminate()
            CURRENT_SONG["proc"].wait(timeout=1.0)
        except Exception:
            pass
    CURRENT_SONG = None
    return "Stopped playback."

def play_song(query_or_url: str) -> str:
    """Download and play the media using mpv."""
    global CURRENT_SONG

    # Terminate any existing player first
    stop_background_play()

    # Determine if video/movie is requested
    q_lower = query_or_url.lower()
    video_keywords = ["video", "watch", "movie", "film", "clip", "mv",
                      "official video", "music video", "show", "trailer"]
    is_video_request = any(kw in q_lower for kw in video_keywords)
    suffix = ".mp4" if is_video_request else ".mp3"

    print(f"\n🔍 Searching for: '{query_or_url}'")

    # ── Show library listing ──────────────────────────────────────────────────
    library = list_library()
    if library:
        print(f"\n  📁 Library ({len(library)} file{'s' if len(library) != 1 else ''}):")
        for name in library[:10]:
            print(f"     • {name}")
        if len(library) > 10:
            print(f"     … and {len(library) - 10} more")
    else:
        print("  📁 Library is empty.")

    # ── Fuzzy check: already in library? ─────────────────────────────────────
    fuzzy_hit = fuzzy_find_in_library(query_or_url, suffix)
    if not fuzzy_hit and not is_video_request:
        # Also check .mp4 for cross-format matches
        fuzzy_hit = fuzzy_find_in_library(query_or_url, ".mp4")

    filepath = None
    if fuzzy_hit:
        print(f"\n  ✓ Found in library: {Path(fuzzy_hit).name}")
        filepath = fuzzy_hit
    else:
        if is_video_request:
            print("  📹 Video playback requested (GUI window will open).")
        else:
            print("  🎵 Not in library — downloading...")

        filepath = download_media(query_or_url, video=is_video_request)
        if not filepath or not Path(filepath).exists():
            # Fallback to opposite format if download failed
            filepath = download_media(query_or_url, video=not is_video_request)
            if not filepath or not Path(filepath).exists():
                return f"ERROR: Failed to download media for query: '{query_or_url}'"
            is_video_request = Path(filepath).suffix == ".mp4"

        
    filename = Path(filepath).name
    title = Path(filepath).stem
    
    # Cross-platform IPC socket path — clean up any stale socket
    socket_path = Path(MPV_SOCK_PATH)
    if socket_path.exists():
        try:
            socket_path.unlink()
        except Exception:
            pass
            
    print(f"\n🎵 Loading: {filename}")
    
    # Locate the best available media player
    player_name, player_bin = find_media_player(prefer_no_video=not is_video_request)
    if not player_bin:
        return "ERROR: No media player found. Please install mpv or VLC."
    
    try:
        if is_video_request:
            # --- VIDEO: GUI window docked on the right ---
            if player_name == "mpv":
                cmd = [
                    player_bin,
                    "--geometry=25%x100%-0+0",
                    "--autofit=25%x100%",
                    "--title=Open-Agent Media Player",
                    f"--input-ipc-server={MPV_SOCK_PATH}",
                    filepath
                ]
            else:  # VLC
                cmd = [player_bin, "--width=400", "--video-on-top", filepath]
        else:
            # --- AUDIO: background, no GUI ---
            if player_name == "mpv":
                cmd = [
                    player_bin,
                    "--no-video",
                    f"--input-ipc-server={MPV_SOCK_PATH}",
                    filepath
                ]
            else:  # cvlc / vlc
                cmd = [player_bin, "--intf", "dummy", "--play-and-exit", filepath]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        CURRENT_SONG = {
            "title": title,
            "filename": filename,
            "filepath": filepath,
            "proc": proc
        }
        
        if is_video_request:
            print("Press [b] to Background, [p] to Pause/Resume, [q] or Ctrl+C to Quit.")
            while proc.poll() is None:
                key = get_key_nonblocking()
                if key:
                    if key in ('b', '\x02'):  # 'b' or Ctrl+B
                        print("\n[Backgrounded] Returning to agent prompt...")
                        return f"Video '{title}' is now playing in the background."
                    elif key in ('p', ' '):  # 'p' or Space
                        res = toggle_background_play()
                        print(f"\n[{res}]")
                    elif key == 'q':
                        print("\n[Stopping playback...]")
                        stop_background_play()
                        break
                time.sleep(0.1)
        else:
            time.sleep(0.5)
            if proc.poll() is None:
                print(f"✓ '{title}' now playing in the background ({player_name}).")
                return f"Playing '{title}' in the background."
            else:
                return f"Finished playing '{title}'."
                
    except KeyboardInterrupt:
        print("\n[Stopping playback...]")
        stop_background_play()
        
    return f"Playback of '{title}' finished or stopped."

def interactive_menu():
    status = get_background_player_status()
    if not status:
        print("\n  🎵 No media is currently playing in the background.")
        return
        
    print(f"\n  🎵 Background Player: {status['title']} ({'Paused' if status['paused'] else 'Playing'})")
    print("  Commands: [p] Pause/Resume  |  [s] Stop  |  [f] Foreground  |  [Enter] Keep in Background")
    
    sys.stdout.write("  Choice: ")
    sys.stdout.flush()
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        choice = sys.stdin.read(1)
    except Exception:
        choice = '\n'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        
    print(choice)
    
    if choice == 'p':
        res = toggle_background_play()
        print(f"  {res}")
    elif choice == 's':
        res = stop_background_play()
        print(f"  {res}")
    elif choice == 'f':
        title = status["title"]
        print(f"  Bringing '{title}' to foreground...")
        print("  Press [b] to Background, [p] to Pause/Resume, [q] or Ctrl+C to Quit.")
        proc = CURRENT_SONG["proc"]
        try:
            while proc.poll() is None:
                key = get_key_nonblocking()
                if key:
                    if key in ('b', '\x02'):
                        print("\n[Backgrounded] Returning to agent prompt...")
                        break
                    elif key in ('p', ' '):
                        res = toggle_background_play()
                        print(f"\n[{res}]")
                    elif key == 'q':
                        print("\n[Stopping playback...]")
                        stop_background_play()
                        break
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n[Stopping playback...]")
            stop_background_play()
