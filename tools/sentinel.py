import re
from pathlib import Path
from ui.console import trunc
from tools.builtin import outline_file

def sentinel_map_codebase() -> str:
    """
    Architect-Sentinel: Automated codebase mapping.
    Scans the project to build a structural 'Global Blueprint'.
    Includes personal context and dynamic skill suggestions.
    """
    cwd = Path.cwd()
    python_files = []
    js_files = []
    html_files = []
    ignored_dirs = {".git", "venv", "node_modules", "__pycache__", ".pi-lens", ".ruff_cache"}
    
    import os
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for f in files:
            path = Path(root) / f
            if f.endswith(".py"):
                python_files.append(path)
            elif f.endswith((".js", ".ts", ".jsx", ".tsx")):
                js_files.append(path)
            elif f.endswith(".html"):
                html_files.append(path)

    parts = ["--- ARCHITECT-SENTINEL GLOBAL BLUEPRINT ---"]

    # Inject User Biography if exists
    bio_path = cwd / "memory" / "BIOGRAPHY.md"
    if bio_path.exists():
        bio_content = bio_path.read_text(encoding="utf-8").strip()
        if bio_content:
            parts.append("\n👤 USER CONTEXT (BIOGRAPHY):")
            parts.append(trunc(bio_content, 1000))
            parts.append("----------------------------\n")

    parts.append(f"Project Root: {cwd}")

    # Detect project type
    project_type = "Unknown"
    if (cwd / "setup.py").exists() or (cwd / "pyproject.toml").exists():
        project_type = "Python Package"
    elif (cwd / "package.json").exists():
        project_type = "Node.js"
    elif (cwd / "Cargo.toml").exists():
        project_type = "Rust"
    elif (cwd / "go.mod").exists():
        project_type = "Go"
    elif (cwd / "Makefile").exists() and (cwd / "Dockerfile").exists():
        project_type = "DevOps/Infra"
    parts.append(f"Project Type: {project_type}")
    parts.append(f"Composition: {len(python_files)} Python, {len(js_files)} JS/TS, {len(html_files)} HTML files.")

    # Detect frameworks/libs from imports
    all_py_text = ""
    for f in python_files[:20]:
        try:
            all_py_text += f.read_text(errors="ignore").lower() + "\n"
        except Exception:
            pass

    frameworks = []
    if "flask" in all_py_text:
        frameworks.append("Flask")
    if "fastapi" in all_py_text:
        frameworks.append("FastAPI")
    if "django" in all_py_text:
        frameworks.append("Django")
    if "pytest" in all_py_text:
        frameworks.append("pytest")
    if "tensorflow" in all_py_text or "keras" in all_py_text:
        frameworks.append("TensorFlow/Keras")
    if "torch" in all_py_text:
        frameworks.append("PyTorch")
    if "transformers" in all_py_text:
        frameworks.append("HuggingFace Transformers")
    if "playwright" in all_py_text or "selenium" in all_py_text:
        frameworks.append("Browser Automation")
    if "httpx" in all_py_text or "requests" in all_py_text:
        frameworks.append("HTTP Client (httpx/requests)")

    if frameworks:
        parts.append(f"Detected Frameworks: {', '.join(frameworks)}")

    # Dynamic Skill Suggestions
    skills = []
    if any(cwd.glob("**/*.html")) or any(cwd.glob("**/*.jsx")):
        skills.append("Frontend-Development")
    if "fastapi" in all_py_text or "flask" in all_py_text:
        skills.append("API-Development")
    if any(cwd.glob("**/docker-compose.yml")) or (cwd / "Dockerfile").exists():
        skills.append("Docker-Orchestration")
    if any(cwd.glob("**/*.tf")) or any(cwd.glob("**/helm/")):
        skills.append("Infrastructure-as-Code")
    if any(cwd.glob("**/*.{yml,yaml}")) and any(cwd.glob("**/*.py")):
        skills.append("CI/CD-Pipeline")
    if "pytest" in all_py_text or any(cwd.glob("**/test_*.py")):
        skills.append("Python-Testing")
    if "torch" in all_py_text or "tensorflow" in all_py_text:
        skills.append("ML-Model-Training")

    if skills:
        parts.append("\n💡 SUGGESTED SKILLS (Load with `load_skill`):")
        for s in skills:
            parts.append(f"- {s}")
        parts.append("")

    # Map top-level structure
    for p in sorted(cwd.glob("*")):
        if p.name.startswith(".") or "venv" in p.name or p.name == "__pycache__":
            continue
        if p.is_dir():
            sub = [f.name for f in p.glob("*") if not f.name.startswith(".") and f.name != "__pycache__"]
            parts.append(
                f"📁 {p.name}/: {', '.join(sub[:10])}{'...' if len(sub) > 10 else ''}"
            )
        else:
            parts.append(f"📄 {p.name}")

    if Path("loop.py").exists():
        parts.append("\nCore Logic (loop.py) symbols:")
        parts.append(outline_file("loop.py"))

    return "\n".join(parts)
