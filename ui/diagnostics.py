import os
import re
import platform
import getpass
import threading
import subprocess
import httpx
from datetime import datetime
from pathlib import Path

from ui.console import C, co, dim, trunc
from core.config import config_load
import providers

def fetch_hn_top_stories() -> list:
    """Fetch the top 3 newest stories from Hacker News."""
    stories = []
    try:
        with httpx.Client(timeout=0.6) as client:
            resp = client.get("https://hacker-news.firebaseio.com/v0/newstories.json")
            if resp.status_code == 200:
                new_ids = resp.json()[:3]
                for item_id in new_ids:
                    try:
                        item_resp = client.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
                        if item_resp.status_code == 200:
                            data = item_resp.json()
                            stories.append({
                                "title": data.get("title", "No Title"),
                                "url": data.get("url", f"https://news.ycombinator.com/item?id={item_id}")
                            })
                    except Exception:
                        pass
    except Exception:
        pass
    return stories

def fetch_random_wikipedia_article() -> dict | None:
    """Fetch an interesting Wikipedia article from the curated "Unusual Articles" page."""
    try:
        import random
        from bs4 import BeautifulSoup
        
        headers = {"User-Agent": "open-agent/1.0 (contact: github.com/oppenheimer-rick/open-agent)"}
        with httpx.Client(timeout=1.5, headers=headers) as client:
            r = client.get("https://en.wikipedia.org/wiki/Wikipedia:Unusual_articles")
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                tables = soup.find_all("table", class_="wikitable")
                all_rows = []
                for table in tables:
                    all_rows.extend(table.find_all("tr")[1:])
                if all_rows:
                    row = random.choice(all_rows)
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        link_el = cells[0].find("a")
                        if link_el:
                            title = link_el.get("title", "Article")
                            url = f"https://en.wikipedia.org{link_el.get('href')}"
                            desc = cells[1].get_text().strip()
                            words = desc.split()
                            extracted_lines = []
                            for idx in range(0, len(words), 8):
                                extracted_lines.append(" ".join(words[idx:idx+8]))
                            return {
                                "title": title,
                                "url": url,
                                "extract_lines": extracted_lines[:4]
                            }
    except Exception:
        pass
    return None

def jarvis_system_check() -> str:
    # 1. Greetings
    hour = datetime.now().hour
    if 5 <= hour < 12:
        greeting = "Good morning, Sir. Diagnostics are green."
    elif 12 <= hour < 17:
        greeting = "Good afternoon, Sir. Core temperature is nominal."
    elif 17 <= hour < 22:
        greeting = "Good evening, Sir. All systems operating within standard parameters."
    else:
        greeting = "Working late, Sir? The Mark XLIII armor is on standby."

    # 2. Battery / Arc Reactor Status
    battery_status = "Arc Reactor Core: 100% (Stable)"
    try:
        bat_dir = Path("/sys/class/power_supply")
        if bat_dir.exists():
            for b in bat_dir.glob("BAT*"):
                cap_file = b / "capacity"
                status_file = b / "status"
                if cap_file.exists():
                    cap = cap_file.read_text().strip()
                    status = status_file.read_text().strip() if status_file.exists() else "Discharging"
                    battery_status = f"Arc Reactor Core: {cap}% ({status})"
                    break
    except Exception:
        pass

    # 3. Workspace Integrity Check (Git Status)
    git_status = "Clean (nominal)"
    try:
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=0.5)
        if res.returncode == 0:
            modified = [line for line in res.stdout.splitlines() if line.strip()]
            if modified:
                git_status = f"{len(modified)} files modified (uncommitted)"
    except Exception:
        pass

    # 4. System Load average (Linux only)
    load_str = "N/A"
    if hasattr(os, "getloadavg"):
        try:
            load = os.getloadavg()
            load_str = f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
        except Exception:
            pass
            
    # 5. Memory Usage
    mem_str = "N/A"
    try:
        if Path("/proc/meminfo").exists():
            meminfo = Path("/proc/meminfo").read_text()
            total_match = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
            avail_match = re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo)
            if total_match and avail_match:
                total_gb = int(total_match.group(1)) / 1024 / 1024
                avail_gb = int(avail_match.group(1)) / 1024 / 1024
                used_gb = total_gb - avail_gb
                mem_str = f"{used_gb:.1f}GB / {total_gb:.1f}GB used"
    except Exception:
        pass

    # 6. Check local LLM status
    llm_status = "OFFLINE"
    llm_base = providers.BASE_URL
    try:
        with httpx.Client(timeout=1.0) as client:
            resp = client.get(f"{llm_base}/models" if "llama" in llm_base or "localhost" in llm_base else f"{llm_base}")
            if resp.status_code in (200, 401, 404):
                llm_status = "ONLINE"
    except Exception:
        pass
        
    hn_stories = []
    wiki_article = None
    
    def _fetch_hn():
        nonlocal hn_stories
        try:
            hn_stories.extend(fetch_hn_top_stories())
        except Exception:
            pass
            
    def _fetch_wiki():
        nonlocal wiki_article
        try:
            wiki_article = fetch_random_wikipedia_article()
        except Exception:
            pass
            
    t_hn = threading.Thread(target=_fetch_hn)
    t_wiki = threading.Thread(target=_fetch_wiki)
    
    t_hn.start()
    t_wiki.start()
    
    # Wait at most 250ms to keep boot zero-latency
    t_hn.join(timeout=0.25)
    t_wiki.join(timeout=0.25)
    
    # Format JARVIS boot screen
    lines = []
    lines.append(co(C.BOLD + C.CYAN, "  🤖 J.A.R.V.I.S. Diagnostics Protocol"))
    lines.append(dim("  " + "─" * 74))
    lines.append(f"  {greeting}")
    lines.append(f"  • {co(C.BOLD, 'Power Source:')}     {battery_status}")
    lines.append(f"  • {co(C.BOLD, 'Load & Memory:')}    {load_str}  ·  {mem_str}")
    lines.append(f"  • {co(C.BOLD, 'Workspace:')}        {git_status}")
    lines.append(f"  • {co(C.BOLD, 'Neural Link:')}      Local LLM Backend: {co(C.GREEN if llm_status == 'ONLINE' else C.RED, llm_status)}")
    lines.append(f"  • {co(C.BOLD, 'Security Grid:')}    Nominal. Encryption active.")
    
    if hn_stories:
        lines.append("")
        lines.append(co(C.BOLD + C.PURPLE, "  📰 Hacker News Intelligence Report:"))
        for idx, story in enumerate(hn_stories):
            lines.append(f"    {idx+1}. {co(C.BOLD, story['title'])}")
            lines.append(dim(f"       {story['url']}"))
            
    if wiki_article:
        lines.append("")
        lines.append(co(C.BOLD + C.PURPLE, f"  📚 Random Wikipedia Article: {wiki_article['title']}"))
        lines.append(dim(f"     {wiki_article['url']}"))
        for w_line in wiki_article["extract_lines"]:
            lines.append(f"     {w_line}")

    lines.append(dim("  " + "─" * 74))
    return "\n".join(lines)

def check_and_summarize_obsidian_vault(force_scan=False, silent=False) -> str:
    """Scans Obsidian Vault for latest notes and returns insights."""
    config = config_load()
    vault_path_str = os.environ.get("OBSIDIAN_VAULT") or config.get("obsidian_vault_path")
    if not vault_path_str:
        return ""

    vault_path = Path(vault_path_str).expanduser().resolve()
    if not vault_path.exists():
        return f"Obsidian Vault path does not exist: {vault_path_str}"
    if not vault_path.is_dir():
        return f"Obsidian Vault path is not a directory: {vault_path_str}"

    if not silent:
        print(f"\n{co(C.CYAN, '  🔎 Scanning Obsidian Vault:')} {vault_path}")
    
    md_files = []
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.endswith('.md') and not f.startswith('.'):
                md_files.append(Path(root) / f)

    if not md_files:
        return "No markdown notes found in the Obsidian Vault."

    md_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    latest_files = md_files[:3]

    lines = []
    lines.append("╭──────────────────────────────────────────────────────────────────────────╮")
    lines.append(f"│  {co(C.BOLD + C.PURPLE, 'OBSIDIAN VAULT INSIGHTS (Latest Modified Notes)')}                     │")
    lines.append("├──────────────────────────────────────────────────────────────────────────┤")
    for idx, fp in enumerate(latest_files):
        rel_path = fp.relative_to(vault_path)
        path_line = f"  • {rel_path}"
        lines.append(f"│ {co(C.BOLD + C.CYAN, path_line.ljust(72))} │")
        if idx < len(latest_files) - 1:
            lines.append("│                                                                          │")
    lines.append("╰──────────────────────────────────────────────────────────────────────────╯")

    insight_str = "\n".join(lines)
    return insight_str
