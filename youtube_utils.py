import subprocess
import json
from pathlib import Path
import time
import sys
import os
import tty
import termios
import select
import socket

CURRENT_SONG = None  # Holds dict with 'title', 'filename', 'filepath', 'proc'

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
    """Download video or audio of a video/search query."""
    download_dir = Path("downloads")
    download_dir.mkdir(exist_ok=True)
    
    target = query_or_url
    if not target.startswith(("http://", "https://", "www.youtube.com")):
        target = f"ytsearch1:{query_or_url}"
        
    suffix = ".mp4" if video else ".mp3"
    try:
        # Get target filename first
        if video:
            cmd_filename = [
                "yt-dlp",
                "--print", "filename",
                "-f", "best[ext=mp4]/best",
                "-o", "downloads/%(title)s.%(ext)s",
                "--no-playlist",
                target
            ]
        else:
            cmd_filename = [
                "yt-dlp",
                "--print", "filename",
                "-x", "--audio-format", "mp3",
                "-o", "downloads/%(title)s.%(ext)s",
                "--no-playlist",
                target
            ]
        res_file = subprocess.run(cmd_filename, capture_output=True, text=True, timeout=10.0)
        expected_filename = None
        if res_file.returncode == 0:
            expected_filename = res_file.stdout.strip()
            if expected_filename:
                expected_filename = expected_filename.splitlines()[-1]
                expected_path = Path(expected_filename)
                if not video:
                    expected_path = expected_path.with_suffix(".mp3")
                expected_filename = str(expected_path)
        
        # Run the actual download
        if video:
            cmd_download = [
                "yt-dlp",
                "-f", "best[ext=mp4]/best",
                "-o", "downloads/%(title)s.%(ext)s",
                "--no-playlist",
                "--no-warnings",
                target
            ]
        else:
            cmd_download = [
                "yt-dlp",
                "-x", "--audio-format", "mp3",
                "-o", "downloads/%(title)s.%(ext)s",
                "--no-playlist",
                "--no-warnings",
                target
            ]
            
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
        sys.stdout.write("\r  [yt-dlp] Download completed!                           \n")
        sys.stdout.flush()
        
        if expected_filename and Path(expected_filename).exists():
            return expected_filename
            
        # Fallback: check downloads folder
        files = list(download_dir.glob(f"*{suffix}"))
        if files:
            newest = max(files, key=lambda p: p.stat().st_mtime)
            return str(newest)
    except Exception:
        pass
    return None

def get_key_nonblocking() -> str | None:
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
    socket_path = "/tmp/open_agent_mpv.sock"
    if not os.path.exists(socket_path):
        return None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.2)
        client.connect(socket_path)
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
    video_keywords = ["video", "watch", "movie", "film", "clip", "mv", "official video", "music video", "show", "trailer"]
    is_video_request = any(kw in q_lower for kw in video_keywords)
    
    print(f"\n🔍 Searching for: '{query_or_url}'")
    if is_video_request:
        print("📹 Video playback requested (GUI window will open).")
    else:
        print("🎵 Audio playback requested (will play in background).")
        
    filepath = download_media(query_or_url, video=is_video_request)
    if not filepath or not Path(filepath).exists():
        # Fallback to opposite format if download failed
        filepath = download_media(query_or_url, video=not is_video_request)
        if not filepath or not Path(filepath).exists():
            return f"ERROR: Failed to download media for query: '{query_or_url}'"
        is_video_request = Path(filepath).suffix == ".mp4"
        
    filename = Path(filepath).name
    title = Path(filepath).stem
    
    # Clear preexisting IPC socket
    socket_path = Path("/tmp/open_agent_mpv.sock")
    if socket_path.exists():
        try:
            socket_path.unlink()
        except Exception:
            pass
            
    print(f"\n🎵 Loading: {filename}")
    
    try:
        if is_video_request:
            # GUI window docked on the right side of desktop screen
            cmd = [
                "mpv",
                "--geometry=25%x100%-0+0",
                "--autofit=25%x100%",
                "--title=Open-Agent Media Player",
                "--input-ipc-server=/tmp/open_agent_mpv.sock",
                filepath
            ]
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
                    elif key == 'q':  # 'q'
                        print("\n[Stopping playback...]")
                        stop_background_play()
                        break
                time.sleep(0.1)
        else:
            # Audio-only background playback with --no-video
            cmd = [
                "mpv",
                "--no-video",
                "--input-ipc-server=/tmp/open_agent_mpv.sock",
                filepath
            ]
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
            
            # Give it a brief moment to start
            time.sleep(0.5)
            if proc.poll() is None:
                print(f"✓ '{title}' started playing in the background.")
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
