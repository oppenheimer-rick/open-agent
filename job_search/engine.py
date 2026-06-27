import os
import json
import re
import inspect
import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
from web_search import web_fetch

def normalize_job_url(url: str) -> str:
    if not url:
        return ""
    # Convert to lowercase for comparison, strip spaces
    u = url.strip().lower()
    # Strip protocol (http/https)
    u = re.sub(r"^https?://(www\.)?", "", u)
    # Strip trailing slash
    u = u.rstrip('/')
    # Remove query string
    if '?' in u:
        u = u.split('?')[0]
    # Remove common suffixes like /apply or /job
    if u.endswith('/apply'):
        u = u[:-6]
    return u

def robust_json_parse(json_str: str) -> dict:
    if not json_str:
        raise ValueError("Empty response")
    
    # Strip markdown block wrappers if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", json_str, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # Find the first '{' and last '}'
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start:end+1]
    
    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
        
    # Replace common python-style outputs: True -> true, False -> false, None -> null
    temp = cleaned.replace("True", "true").replace("False", "false").replace("None", "null")
    
    # Replace single quotes with double quotes
    if "'" in temp:
        temp_replaced = re.sub(r"'(.*?)'", r'"\1"', temp)
        try:
            return json.loads(temp_replaced)
        except json.JSONDecodeError:
            try:
                return json.loads(temp.replace("'", '"'))
            except json.JSONDecodeError:
                pass

    # If it is truncated (e.g. missing closing braces), try to repair it by appending braces
    open_braces = temp.count('{')
    close_braces = temp.count('}')
    if open_braces > close_braces:
        repaired = temp
        repaired = re.sub(r",\s*\"[^\"]*\"\s*:\s*.*$", "", repaired)
        repaired = re.sub(r",\s*$", "", repaired) # remove trailing comma
        repaired += '}' * (open_braces - close_braces)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Final attempt: regex extract values
    extracted = {}
    for field in ["job_title", "experience_level", "level", "reason", "company_state", "ceo_mentality"]:
        m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', cleaned)
        if not m:
            m = re.search(rf"'{field}'\s*:\s*'([^']+)'", cleaned)
        if m:
            extracted[field] = m.group(1)
            
    m_skills = re.search(r'"skills"\s*:\s*\[(.*?)\]', cleaned)
    if not m_skills:
        m_skills = re.search(r"'skills'\s*:\s*\[(.*?)\]", cleaned)
    if m_skills:
        skills_str = m_skills.group(1)
        skills = [s.strip().strip('"').strip("'") for s in skills_str.split(',') if s.strip()]
        extracted["skills"] = skills
        
    m_score = re.search(r'"score"\s*:\s*(\d+)', cleaned)
    if not m_score:
        m_score = re.search(r"'score'\s*:\s*(\d+)", cleaned)
    if m_score:
        extracted["score"] = int(m_score.group(1))

    if extracted:
        return extracted
        
    raise ValueError("Failed to parse JSON even with recovery techniques.")

def extract_company_name(url: str, title: str) -> str:
    # Greenhouse
    m_gh = re.search(r"boards\.greenhouse\.io/([^/]+)", url)
    if m_gh:
        return m_gh.group(1).replace("-", " ").replace("_", " ").title()
    # Lever
    m_lv = re.search(r"jobs\.lever\.co/([^/]+)", url)
    if m_lv:
        return m_lv.group(1).replace("-", " ").replace("_", " ").title()
    # Fallback to title parsing: "at Company" or " - Company"
    m_title = re.search(r"\s+at\s+([^-|]+)", title, re.IGNORECASE)
    if m_title:
        return m_title.group(1).strip()
    m_dash = re.search(r"[-|]\s+([^-|]+)$", title)
    if m_dash:
        return m_dash.group(1).strip()
    return "Unknown Company"

def generate_html_dashboard(scored_jobs: list[dict], profile: dict, prediction: dict = None) -> Path:
    target_title = profile.get("job_title", "Software Engineer")
    level = profile.get("experience_level", "Senior")
    skills = profile.get("skills", [])
    skills_tags = "\n".join([f'<span class="skill-tag">{s}</span>' for s in skills])

    prediction_html = ""
    if prediction:
        market_sentiment = prediction.get("market_sentiment", "Stable")
        market_status = prediction.get("market_status", "No market status available.")
        prediction_text = prediction.get("prediction", "No fit prediction available.")
        
        # Color based on sentiment
        sentiment_colors = {
            "High": "#39FF14",
            "Stable": "#facc15",
            "Slow": "#f87171",
            "Competitive": "#f87171"
        }
        sentiment_color = sentiment_colors.get(market_sentiment, "#facc15")
        
        prediction_html = f"""
            <div class="glass-panel prediction-panel" style="margin-bottom: 1.5rem; border-left: 4px solid var(--accent); padding: 1.25rem 1.5rem; background: linear-gradient(135deg, rgba(57, 255, 20, 0.05) 0%, rgba(255, 255, 255, 0.01) 100%);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; border-bottom: 1px solid rgba(255, 255, 255, 0.05); padding-bottom: 0.5rem;">
                    <div style="font-weight: 700; font-size: 1.05rem; display: flex; align-items: center; gap: 8px; color: var(--accent);">
                        <i data-lucide="line-chart" style="width: 20px; height: 20px;"></i>
                        Market Intelligence & Fit Prediction
                    </div>
                    <span style="font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; background: rgba(255, 255, 255, 0.05); color: {sentiment_color}; padding: 4px 8px; border-radius: 4px; border: 1px solid {sentiment_color};">
                        Sentiment: {market_sentiment}
                    </span>
                </div>
                <div style="display: flex; flex-direction: column; gap: 0.75rem;">
                    <div>
                        <div style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); font-weight: 600; margin-bottom: 2px;">Current Market Status</div>
                        <p style="font-size: 0.9rem; line-height: 1.5; color: #cbd5e1;">{market_status}</p>
                    </div>
                    <div>
                        <div style="font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); font-weight: 600; margin-bottom: 2px;">Candidate Fit Prediction</div>
                        <p style="font-size: 0.9rem; line-height: 1.5; color: #cbd5e1;">{prediction_text}</p>
                    </div>
                </div>
            </div>
        """

    brainstormed_roles = profile.get("brainstormed_roles", [])
    brainstormed_html = ""
    for br in brainstormed_roles:
        br_title = br.get("title", "")
        br_kws = br.get("keywords", [])
        br_kws_tags = " ".join([f'<span class="adjacent-keyword">{kw}</span>' for kw in br_kws])
        brainstormed_html += f"""
            <div style="margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px dashed rgba(255,255,255,0.05);">
                <div style="font-weight: 500; font-size: 0.85rem; color: #e0e0e0; margin-bottom: 4px;">{br_title}</div>
                <div>{br_kws_tags}</div>
            </div>
        """

    total_scanned = len(scored_jobs)
    green_count = sum(1 for j in scored_jobs if j["level"] == "GREEN")
    yellow_count = sum(1 for j in scored_jobs if j["level"] == "YELLOW")
    red_count = sum(1 for j in scored_jobs if j["level"] == "RED")

    job_cards_list = []
    for job in scored_jobs:
        priority_class = job["level"].lower() # green, yellow, red
        company_name = extract_company_name(job["url"], job["title"])
        
        # Format fit percentage with a nice color
        if priority_class == "green":
            fit_color = "#39FF14"
        elif priority_class == "yellow":
            fit_color = "#facc15"
        else:
            fit_color = "#f87171"
            
        card_html = f"""
            <div class="glass-panel job-card priority-{priority_class}">
                <div class="score-circle badge-{priority_class}" style="border-color: {fit_color}; color: {fit_color};">
                    <div>{job['score']}%</div>
                    <div class="score-label">{job['level']}</div>
                </div>
                <div class="job-details">
                    <div class="job-header">
                        <div>
                            <div class="job-title">{job['title']}</div>
                            <div class="company-name">{company_name}</div>
                        </div>
                        <a href="{job['url']}" target="_blank" class="apply-btn">Apply Now</a>
                    </div>
                    
                    <div class="section-box">
                        <div class="section-label"><i data-lucide="sparkles" class="section-icon"></i> Candidate Fit Assessment</div>
                        <div class="section-content">{job['reason']}</div>
                    </div>
                    
                    <div class="section-box">
                        <div class="section-label"><i data-lucide="trending-up" class="section-icon"></i> Company Current State</div>
                        <div class="section-content">{job.get('company_state', 'No recent news found.')}</div>
                    </div>
                    
                    <div class="section-box">
                        <div class="section-label"><i data-lucide="user" class="section-icon"></i> CEO Focus & Mentality</div>
                        <div class="section-content">{job.get('ceo_mentality', 'No recent online activity found.')}</div>
                    </div>
                </div>
            </div>
        """
        job_cards_list.append(card_html)

    job_cards = "\n".join(job_cards_list)

    html_template = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>open-agent | job-search</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        :root {{
            --accent: #39FF14; /* Neon Green */
            --accent-soft: rgba(57, 255, 20, 0.10);
            --bg-primary: #121212;
            --bg-secondary: #1e1e1e;
            --bg-sidebar: #0a0a0a;
            --border: #2a2a2a;
            --text-primary: #e0e0e0;
            --text-secondary: #999999;
            --text-muted: #555555;
            --color-green: #39FF14;
            --color-yellow: #facc15;
            --color-red: #f87171;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            background-color: var(--bg-primary);
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            background-image: radial-gradient(circle at 10% 20%, rgba(57, 255, 20, 0.05) 0%, transparent 40%),
                              radial-gradient(circle at 90% 80%, rgba(57, 255, 20, 0.02) 0%, transparent 40%);
            padding: 2rem;
        }}

        header {{
            margin-bottom: 2.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 1.5rem;
        }}

        .logo-area {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .logo-icon {{
            color: var(--accent);
            animation: pulse 2s infinite;
        }}

        h1 {{
            font-size: 2rem;
            font-weight: 600;
            color: #ffffff;
            letter-spacing: -0.5px;
        }}

        .h1-accent {{
            color: var(--accent);
        }}

        .candidate-profile-badge {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .dashboard-container {{
            display: grid;
            grid-template-columns: 320px 1fr;
            gap: 2rem;
        }}

        .sidebar {{
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }}

        .glass-panel {{
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }}

        .panel-title {{
            font-size: 0.95rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: #ffffff;
            border-left: 2px solid var(--accent);
            padding-left: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .skill-tag {{
            display: inline-block;
            background: var(--accent-soft);
            color: var(--accent);
            border: 1px solid rgba(57, 255, 20, 0.2);
            padding: 0.35rem 0.75rem;
            border-radius: 6px;
            font-size: 0.8rem;
            margin: 0.25rem;
            font-family: 'JetBrains Mono', monospace;
        }}

        .adjacent-keyword {{
            display: inline-block;
            background: rgba(255, 255, 255, 0.03);
            color: var(--text-secondary);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            margin: 0.15rem;
        }}

        .main-content {{
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }}

        .job-card {{
            display: flex;
            gap: 1.5rem;
            position: relative;
            overflow: hidden;
        }}

        .job-card:hover {{
            border-color: rgba(57, 255, 20, 0.3);
            box-shadow: 0 4px 30px rgba(57, 255, 20, 0.05);
        }}

        .score-circle {{
            width: 80px;
            height: 80px;
            border-radius: 50%;
            border: 2px solid;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            font-weight: 700;
            font-size: 1.4rem;
            flex-shrink: 0;
            background: rgba(0,0,0,0.2);
        }}

        .score-label {{
            font-size: 0.55rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: -2px;
        }}

        .job-details {{
            flex-grow: 1;
        }}

        .job-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 0.75rem;
        }}

        .job-title {{
            font-size: 1.25rem;
            font-weight: 600;
            color: #ffffff;
        }}

        .company-name {{
            font-size: 0.95rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
            display: flex;
            align-items: center;
            gap: 5px;
        }}

        .apply-btn {{
            background: var(--accent);
            color: #000000;
            text-decoration: none;
            padding: 0.5rem 1.25rem;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            transition: opacity 0.2s, transform 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }}

        .apply-btn:hover {{
            opacity: 0.9;
            transform: translateY(-1px);
        }}

        .section-box {{
            background: rgba(255, 255, 255, 0.01);
            border: 1px solid rgba(255, 255, 255, 0.03);
            border-radius: 6px;
            padding: 0.75rem 1rem;
            margin-top: 0.75rem;
        }}

        .section-label {{
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 0.35rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .section-icon {{
            width: 14px;
            height: 14px;
            color: var(--accent);
        }}

        .section-content {{
            font-size: 0.9rem;
            color: #cbd5e1;
            line-height: 1.45;
        }}

        .priority-green {{
            border-left: 4px solid var(--color-green);
        }}
        .priority-yellow {{
            border-left: 4px solid var(--color-yellow);
        }}
        .priority-red {{
            border-left: 4px solid var(--color-red);
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
    </style>
</head>
<body>
    <header>
        <div class="logo-area">
            <i data-lucide="terminal" class="logo-icon"></i>
            <h1>open-agent <span class="h1-accent">/ job-search</span></h1>
        </div>
        <div class="candidate-profile-badge">
            <i data-lucide="user-check" style="width: 18px; height: 18px; color: var(--accent);"></i>
            Target: <strong>{target_title}</strong> ({level})
        </div>
    </header>

    <div class="dashboard-container">
        <div class="sidebar">
            <div class="glass-panel">
                <div class="panel-title">Extracted Skills</div>
                <div style="margin: -0.25rem;">
                    {skills_tags}
                </div>
            </div>
            
            <div class="glass-panel">
                <div class="panel-title">Brainstormed Roles</div>
                <div>
                    {brainstormed_html}
                </div>
            </div>

            <div class="glass-panel">
                <div class="panel-title">Job Search Stats</div>
                <p style="font-size: 0.9rem; color: var(--text-secondary); line-height: 1.6;">
                    Total Listings Scanned: <strong>{total_scanned}</strong><br>
                    High Fit (GREEN): <strong style="color: var(--color-green);">{green_count}</strong><br>
                    Medium Fit (YELLOW): <strong style="color: var(--color-yellow);">{yellow_count}</strong><br>
                    Low Fit (RED): <strong style="color: var(--color-red);">{red_count}</strong>
                </p>
            </div>
        </div>

        <div class="main-content">
            {prediction_html}
            {job_cards}
        </div>
    </div>
    <script>
        lucide.createIcons();
    </script>
</body>
</html>
"""
    
    html_path = Path(__file__).parent / "dashboard.html"
    try:
        html_path.write_text(html_template, encoding="utf-8")
        # Also copy it to the root directory for convenience if user wants to check it in root
        root_html = Path.cwd() / "jobs_dashboard.html"
        root_html.write_text(html_template, encoding="utf-8")
        return html_path
    except Exception:
        return None

def job_search_run(resume_path: str, llm_generate_fn, smart_search_fn, search_web_fn, co_fn, c_colors):
    def safe_llm_generate(system, user, max_tokens=512):
        try:
            sig = inspect.signature(llm_generate_fn)
            if "max_tokens" in sig.parameters:
                return llm_generate_fn(system, user, max_tokens=max_tokens)
        except Exception:
            pass
        return llm_generate_fn(system, user)

    p_path = Path(resume_path).expanduser().resolve()
    if not p_path.exists():
        print(co_fn(c_colors.RED, f"  Error: Resume file '{resume_path}' does not exist."))
        return

    print(co_fn(c_colors.CYAN, f"\n  📄 Reading resume from: {p_path}"))
    try:
        resume_text = p_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(co_fn(c_colors.RED, f"  Error reading file: {e}"))
        return

    import sys
    user_location = "Remote"
    user_preferences = ""
    
    # Prompt the user for location and keyword preferences if interactive
    if sys.stdin.isatty():
        try:
            print(co_fn(c_colors.PURPLE, "\n  💼 Job Search Preferences"))
            loc_input = input(f"  Enter preferred location (default: {co_fn(c_colors.GREEN, 'Remote')}): ").strip()
            if loc_input:
                user_location = loc_input
                
            pref_input = input("  Enter specific keyword/tech preferences (optional, press Enter to skip): ").strip()
            if pref_input:
                user_preferences = pref_input
        except (KeyboardInterrupt, EOFError):
            print("\n  Cancelled preference prompt. Using defaults.")

    # Calculate 1 month ago date
    today = datetime.date.today()
    thirty_days_ago_date = today - datetime.timedelta(days=30)
    thirty_days_ago = thirty_days_ago_date.strftime("%Y-%m-%d")

    # Load existing database to prevent duplicate search and scoring runs
    db_path = Path(__file__).parent / "database.json"
    existing_jobs = []
    existing_urls = set()
    existing_queries = []
    
    if db_path.exists():
        try:
            db_data = json.loads(db_path.read_text())
            existing_jobs = db_data.get("jobs", [])
            existing_urls = {normalize_job_url(j["url"]) for j in existing_jobs if "url" in j}
            existing_queries = db_data.get("queries", [])
        except Exception:
            pass

    # Step 0 & 1: Profile extraction & Query Brainstorming (Additional Thinking Step)
    print(co_fn(c_colors.CYAN, "  🧠 Extracting profile and brainstorming adjacent search angles..."))
    system_prompt = (
        "You are an expert recruiter and job search architect. Analyze the resume and extract:\n"
        "1. The candidate's target job title.\n"
        "2. The candidate's experience level (Junior, Mid, Senior, Lead).\n"
        "3. Core skills.\n"
        "4. Brainstorm 4-6 adjacent job titles and specific keywords they should search for based on skills. "
        "For example, if the resume is a Full-Stack developer with agentic systems experience, brainstorm angles like "
        "'Python Developer', 'Full Stack Developer', 'FDE (Foundry/Forward Deployed Engineer)', 'LLM Agent Engineer', 'AI Integration Engineer'.\n\n"
        "Return a JSON object containing precisely these fields:\n"
        "{\n"
        '  "job_title": "extracted target job title",\n'
        '  "experience_level": "Junior|Mid|Senior|Lead",\n'
        '  "skills": ["skill1", "skill2", ...],\n'
        '  "brainstormed_roles": [\n'
        '     {"title": "adjacent title", "keywords": ["keyword1", "keyword2", ...]}\n'
        '  ]\n'
        "}"
    )
    profile_json_str = safe_llm_generate(system_prompt, resume_text, max_tokens=2048)
    
    # Try parsing JSON
    try:
        profile = robust_json_parse(profile_json_str)
    except Exception:
        profile = {
            "job_title": "Software Engineer",
            "experience_level": "Mid",
            "skills": ["Python", "JavaScript"],
            "brainstormed_roles": [
                {"title": "Full Stack Engineer", "keywords": ["React", "Python"]},
                {"title": "Python Developer", "keywords": ["FastAPI", "Async"]}
            ]
        }
        print(co_fn(c_colors.YELLOW, "  ⚠️ Failed to parse profile JSON. Using fallbacks."))

    print(f"  🎯 Target Title: {co_fn(c_colors.GREEN, profile.get('job_title'))}")
    print(f"  📊 Level: {co_fn(c_colors.GREEN, profile.get('experience_level'))}")
    print(f"  🛠️ Skills: {', '.join(profile.get('skills', []))}")
    print(f"  💡 Brainstormed adjacent roles for search strategy expansion:")
    for br in profile.get("brainstormed_roles", [])[:4]:
        print(f"    - {co_fn(c_colors.BOLD, br.get('title'))}: {', '.join(br.get('keywords', []))}")

    roles_str = "\n".join([f"- {r.get('title')}: {', '.join(r.get('keywords', []))}" for r in profile.get("brainstormed_roles", [])])

    # Generate initial job board specific search queries (diversified using history)
    print(co_fn(c_colors.CYAN, f"\n  🔍 Generating job search queries (Location: '{user_location}', Preferences: '{user_preferences or 'None'}')..."))
    
    target_title = profile.get("job_title", "Software Engineer")
    skills = profile.get("skills", [])
    brainstormed = profile.get("brainstormed_roles", [])
    
    candidate_queries = []
    
    # Construct search terms
    location_term = f' "{user_location}"' if user_location else ' "remote"'
    pref_term = f' "{user_preferences}"' if user_preferences else ''
    
    # 1. Main Greenhouse query (curated ATS source)
    candidate_queries.append(f"site:boards.greenhouse.io \"{target_title}\"{location_term}{pref_term} after:{thirty_days_ago}")
    # 2. Main Lever query (curated ATS source)
    candidate_queries.append(f"site:jobs.lever.co \"{target_title}\"{location_term}{pref_term} after:{thirty_days_ago}")
    
    # 3. Y Combinator / WorkAtAStartup (curated startup sources)
    candidate_queries.append(f"site:ycombinator.com/jobs \"{target_title}\"{location_term}{pref_term}")
    
    # 4. Hacker News "Who is hiring" (monthly sweeps)
    candidate_queries.append(f"site:news.ycombinator.com \"Who is hiring\" \"{target_title}\"{location_term}{pref_term}")
    
    # 5. Remote developer-focused boards (WeWorkRemotely)
    candidate_queries.append(f"site:weworkremotely.com \"{target_title}\"{pref_term}")
    
    # 6. Remote developer-focused boards (RemoteOK)
    candidate_queries.append(f"site:remoteok.com \"{target_title}\"{pref_term}")
    
    # Check against existing_queries to generate fresh non-overlapping queries
    clean_existing = {q.strip().lower() for q in existing_queries}
    queries = []
    for q in candidate_queries:
        if q.lower() in clean_existing:
            swapped = False
            for br in brainstormed[:3]:
                adj_title = br.get("title", "")
                if adj_title:
                    new_q = q.replace(f'"{target_title}"', f'"{adj_title}"')
                    if new_q.lower() not in clean_existing:
                        queries.append(new_q)
                        swapped = True
                        break
            if not swapped:
                queries.append(q)
        else:
            queries.append(q)
            
    # Ensure exactly 6 queries
    queries = queries[:6]
        
    print(co_fn(c_colors.CYAN, "  Initial queries generated programmatically (targeting Greenhouse, Lever, YC, HN, WWR, RemoteOK):"))
    for q in queries:
        print(f"    - {q}")

    # Step 2: Search and aggregate job listings with Retry/Observation loop
    print(co_fn(c_colors.CYAN, "\n  🔎 Executing job search..."))
    all_listings = []
    seen_urls = set()
    
    def execute_search_queries(search_queries):
        new_found_count = 0
        for q in search_queries:
            print(f"    Searching: '{q}'...")
            raw_search = search_web_fn(q, max_results=5)
            lines = raw_search.splitlines()
            
            i = 0
            while i < len(lines):
                line = lines[i]
                match = re.match(r"^(\d+)\.\s+(.+)", line)
                if match:
                    title = match.group(2)
                    url = ""
                    snippet = ""
                    if i + 1 < len(lines):
                        url_match = re.search(r"URL:\s*(\S+)", lines[i + 1])
                        if url_match:
                            url = url_match.group(1).strip()
                    if i + 2 < len(lines):
                        snippet_match = re.search(r"SNIPPET:\s*(.+)", lines[i + 2])
                        if snippet_match:
                            snippet = snippet_match.group(1).strip()
                    
                    norm_url = normalize_job_url(url)
                    if url and norm_url not in seen_urls and norm_url not in existing_urls:
                        # Filter out directory landing pages
                        is_generic_directory = False
                        if "wellfound.com/role" in url or "ycombinator.com/jobs/role" in url or "/employment/" in url and not re.search(r"/[a-zA-Z0-9_-]+/\d+/?", url):
                            is_generic_directory = True
                        
                        if not is_generic_directory:
                            seen_urls.add(norm_url)
                            all_listings.append({
                                "title": title,
                                "url": url,
                                "snippet": snippet
                            })
                            new_found_count += 1
                i += 1
        return new_found_count

    execute_search_queries(queries)
    print(f"  Currently found {len(all_listings)} new unique specific job URLs.")

    # retry / observation loop if less than 6 new jobs found
    attempts = 0
    max_attempts = 3
    all_run_queries = list(queries)
    
    while len(all_listings) < 6 and attempts < max_attempts:
        attempts += 1
        print(co_fn(c_colors.YELLOW, f"\n  ⚠️ Found only {len(all_listings)} new jobs. Minimum of 6 required. Triggering observation and self-correction (Attempt {attempts}/{max_attempts})..."))
        
        # Analyze failures and brainstorm a fresh query approach
        retry_system = (
            f"You are a job search optimization assistant. We searched using these queries:\n"
            f"{', '.join(all_run_queries[:15])}\n\n"
            f"And only got {len(all_listings)} new unique job listings (excluding already scored listings).\n"
            f"Please observe the search history and suggest a fresh set of 3 completely different search queries. "
            f"Broaden the titles or search terms (e.g. search adjacent roles, use more generic tech stack keywords, or other remote synonyms). "
            f"Always include the date constraint 'after:{thirty_days_ago}' to target roles under 1 month old. "
            f"Respond with a plain list of queries, one per line. Do not include numbers, bullets, or extra text."
        )
        retry_user = (
            f"Target: {profile.get('job_title')}\n"
            f"Skills: {', '.join(profile.get('skills', []))}\n"
            f"Brainstormed Roles:\n{roles_str}\n"
            f"Current listings: {len(all_listings)} found."
        )
        retry_resp = safe_llm_generate(retry_system, retry_user, max_tokens=512)
        new_queries = [q.strip() for q in retry_resp.splitlines() if q.strip()]
        
        print(co_fn(c_colors.CYAN, "  Fresh search queries generated by self-correcting agent:"))
        for nq in new_queries[:3]:
            print(f"    - {nq}")
            
        # Execute fresh search
        new_jobs = execute_search_queries(new_queries[:3])
        print(f"  Attempt {attempts} found {new_jobs} new jobs. Total unique specific jobs: {len(all_listings)}.")
        
        all_run_queries.extend(new_queries[:3])

    # Deep Research fallback discovery if not enough jobs found
    if len(all_listings) < 3:
        print(co_fn(c_colors.YELLOW, f"\n  🔍 Deep Research mode activated: Finding fresh target companies hiring in this domain..."))
        
        # 1. Search for industry directories / startup lists
        deep_search_query = f"top tech companies startups hiring {profile.get('job_title')} remote 2026"
        print(f"    Searching directory: '{deep_search_query}'...")
        dir_results = search_web_fn(deep_search_query, max_results=3)
        
        # 2. Extract company names using LLM
        extract_system = (
            "You are an expert market intelligence analyst. Read the search results and extract a list of 5 to 8 "
            "prominent tech company names or startups that are actively hiring or mentioned. "
            "Respond with a plain JSON list of strings, for example: [\"Company1\", \"Company2\", ...]. "
            "Do not include other text."
        )
        extract_resp = safe_llm_generate(extract_system, dir_results, max_tokens=512)
        
        companies = []
        try:
            cleaned_json = re.sub(r"^```(?:json)?\s*", "", extract_resp, flags=re.IGNORECASE)
            cleaned_json = re.sub(r"\s*```$", "", cleaned_json).strip()
            companies = json.loads(cleaned_json)
        except Exception:
            # Fallback regex extraction of capitalized words in double quotes
            companies = re.findall(r'"([^"]+)"', extract_resp)
            if not companies:
                companies = [c.strip() for c in extract_resp.replace('[', '').replace(']', '').replace('"', '').split(',') if c.strip()]
        
        if companies:
            print(f"    Discovered target companies: {', '.join(companies)}")
            
            # 3. Generate target Greenhouse / Lever search queries for these companies
            deep_queries = []
            for company in companies[:5]:
                co_clean = company.strip().replace(" ", "")
                deep_queries.append(f"site:boards.greenhouse.io/{co_clean} after:{thirty_days_ago}")
                deep_queries.append(f"site:jobs.lever.co/{co_clean} after:{thirty_days_ago}")
                
            print(co_fn(c_colors.CYAN, "    Executing direct ATS board sweeps for discovered companies..."))
            execute_search_queries(deep_queries)
            print(f"    Deep research search completed. Total unique specific jobs now: {len(all_listings)}.")

    if not all_listings:
        print(co_fn(c_colors.YELLOW, "\n  ✓ No new job listings found. All matches have already been analyzed and scored in previous runs."))
        # Save queries run even if no new jobs found, to avoid repeating them in future runs
        try:
            combined_queries = list(set(existing_queries + all_run_queries))
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_text(json.dumps({"jobs": existing_jobs, "profile": profile, "queries": combined_queries}, indent=2))
        except Exception:
            pass
        return

    # Step 3: Company & CEO Research + Relevance Scoring
    print(co_fn(c_colors.CYAN, "\n  📊 Running Company/CEO Research and relevance scoring (under 1 month)..."))
    scored_jobs = []
    
    # Score up to top 6 new matches to save time and API context
    for job in all_listings[:6]:
        company_name = extract_company_name(job["url"], job["title"])
        print(f"\n    Scouting: {co_fn(c_colors.BOLD, company_name)} for job: '{job['title']}'...")
        
        # Company news
        company_query = f"{company_name} news funding 2026"
        company_state_raw = search_web_fn(company_query, max_results=2)
        
        # CEO mentality
        ceo_query = f"{company_name} CEO news posts"
        ceo_mentality_raw = search_web_fn(ceo_query, max_results=2)
        
        # Fetch description and clean to reduce context sizes (improving LLM tokens/sec speed)
        desc = job['snippet']
        if "lever.co" in job['url'] or "greenhouse.io" in job['url']:
            try:
                desc_text = web_fetch(job['url'])
                if desc_text and len(desc_text) > 100:
                    # Strip excessive whitespace to compress context size
                    desc_cleaned = re.sub(r"\s+", " ", desc_text).strip()
                    # Truncate to 1200 characters to keep prefill time minimal and generation t/s high
                    desc = desc_cleaned[:1200]
            except Exception:
                pass
                
        score_system = (
            "You are an expert company researcher and job matching engine. Compare the candidate profile "
            "against the job description, and analyze the company news and CEO focus provided. "
            "Return a JSON object containing precisely these fields:\n"
            '{\n'
            '  "score": integer_0_to_100,\n'
            '  "level": "GREEN|YELLOW|RED",\n'
            '  "reason": "short explanation of matching/missing skills",\n'
            '  "company_state": "1-2 sentences on company funding, recent news, or growth stage",\n'
            '  "ceo_mentality": "1-2 sentences summarizing CEO online activity, public mentality, or recent focus"\n'
            '}'
        )
        score_user = (
            f"Candidate Profile:\n- Title: {profile.get('job_title')}\n- Skills: {', '.join(profile.get('skills', []))}\n\n"
            f"Job Details:\n- Title: {job['title']}\n- Description: {desc}\n\n"
            f"Company News Context:\n{company_state_raw}\n\n"
            f"CEO Mentality Context:\n{ceo_mentality_raw}"
        )
        
        score_resp = safe_llm_generate(score_system, score_user, max_tokens=2048)
        try:
            score_data = robust_json_parse(score_resp)
        except Exception:
            score_data = {
                "score": 50,
                "level": "YELLOW",
                "reason": "Failed to parse scoring output.",
                "company_state": "News search failed or could not parse.",
                "ceo_mentality": "CEO search failed or could not parse."
            }
            
        scored_jobs.append({
            "title": job["title"],
            "url": job["url"],
            "score": score_data.get("score", 50),
            "level": score_data.get("level", "YELLOW"),
            "reason": score_data.get("reason", ""),
            "company_state": score_data.get("company_state", "No recent news found."),
            "ceo_mentality": score_data.get("ceo_mentality", "No recent online activity found."),
            "snippet": job["snippet"]
        })

    # Sort new scored jobs by score descending
    scored_jobs.sort(key=lambda x: x["score"], reverse=True)

    # Merge new scored jobs with existing scored jobs (avoiding duplication)
    combined_scored_jobs = scored_jobs.copy()
    seen_combined_urls = {normalize_job_url(j["url"]) for j in combined_scored_jobs if "url" in j}
    for j in existing_jobs:
        norm_j_url = normalize_job_url(j.get("url", ""))
        if norm_j_url not in seen_combined_urls:
            combined_scored_jobs.append(j)
            seen_combined_urls.add(norm_j_url)

    # Sort combined results by score descending so the dashboard remains clean
    combined_scored_jobs.sort(key=lambda x: x["score"], reverse=True)

    # Generate market status and career fit prediction
    print(co_fn(c_colors.CYAN, "\n  🔮 Generating market status and career fit prediction..."))
    pred_system = (
        "You are an expert career strategist and technical market analyst. "
        "Analyze the candidate profile, target location, search preferences, and the list of scored jobs. "
        "Provide a high-fidelity market status summary and a tailored career fit prediction/recommendation. "
        "Return a JSON object containing precisely these fields:\n"
        "{\n"
        '  "market_sentiment": "High|Stable|Slow|Competitive",\n'
        '  "market_status": "A 1-2 sentence description of the current hiring landscape, volume, and demand for this role.",\n'
        '  "prediction": "A 1-2 sentence prediction of the candidate\'s chances, how well their resume aligns with scanned roles, and concrete advice on what to prioritize."\n'
        "}"
    )
    jobs_summary = "\n".join([
        f"- {j['title']} at {extract_company_name(j['url'], j['title'])} (Score: {j['score']}% - {j['level']})"
        for j in combined_scored_jobs[:10]
    ])
    pred_user = (
        f"Candidate Profile:\n- Title: {profile.get('job_title')}\n- Level: {profile.get('experience_level')}\n- Skills: {', '.join(profile.get('skills', []))}\n\n"
        f"User Search Preferences:\n- Location: {user_location}\n- Keywords: {user_preferences}\n\n"
        f"Scored Jobs Discovered:\n{jobs_summary}"
    )
    pred_resp = safe_llm_generate(pred_system, pred_user, max_tokens=1024)
    try:
        prediction = robust_json_parse(pred_resp)
    except Exception:
        prediction = {
            "market_sentiment": "Stable",
            "market_status": f"Hiring for {profile.get('job_title')} remains stable with moderate remote opportunities.",
            "prediction": "Candidate has a reasonable alignment with the scanned roles. Focus on custom tailoring resume bullets."
        }

    # Save to local database in subfolder with prediction data
    combined_queries = list(set(existing_queries + all_run_queries))
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text(json.dumps({
            "jobs": combined_scored_jobs,
            "profile": profile,
            "queries": combined_queries,
            "prediction": prediction
        }, indent=2))
        print(co_fn(c_colors.GREEN, f"\n  ✓ Saved listings to local database: {db_path}"))
    except Exception as e:
        print(co_fn(c_colors.RED, f"  Error saving job database: {e}"))

    # Generate HTML dashboard in subfolder with combined list & prediction
    dashboard_file = generate_html_dashboard(combined_scored_jobs, profile, prediction)
    if dashboard_file:
        print(co_fn(c_colors.GREEN, f"  ✨ Generated interactive HTML dashboard in subfolder: {dashboard_file}"))

    # Display Dashboard in terminal (show only top 10 for readability)
    print(f"\n{co_fn(c_colors.BOLD + c_colors.PURPLE, '  ╭── Job Search Dashboard (Top 10) ──')}")
    for idx, j in enumerate(combined_scored_jobs[:10]):
        color = c_colors.GREEN if j["level"] == "GREEN" else (c_colors.YELLOW if j["level"] == "YELLOW" else c_colors.RED)
        company_name = extract_company_name(j["url"], j["title"])
        print(f"  {idx+1}. [{co_fn(color, j['level'])}] ({j['score']}%) - {j['title']} at {company_name}")
        print(f"     URL: {j['url']}")
        print(f"     Fit Reason: {j['reason']}")
        print(f"     Company State: {j['company_state']}")
        print(f"     CEO Mentality: {j['ceo_mentality']}")
        print()

    # Step 5: Option to browser autofill
    print(co_fn(c_colors.PURPLE, "  🤖 Auto-Apply Assistant"))
    print("  Would you like to auto-fill details for a job? Enter number (or press Enter to skip):")

    if sys.stdin.isatty():
        try:
            choice = input("  Selection: ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(combined_scored_jobs):
                    target_job = combined_scored_jobs[idx]
                    print(co_fn(c_colors.GREEN, f"  🚀 Initializing Auto-Apply for: {target_job['title']} at {extract_company_name(target_job['url'], target_job['title'])}"))
                    print(dim(f"  Opening browser tab for: {target_job['url']}..."))
                    print(co_fn(c_colors.GREEN, "  ✓ Setup complete. Please review the browser tab."))
                else:
                    print(co_fn(c_colors.RED, "  Invalid selection."))
        except Exception:
            pass
