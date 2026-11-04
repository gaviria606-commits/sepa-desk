"""Run research jobs (news scan, daily research) with the Claude Code CLI in headless mode.

On claude.ai these were scheduled tasks. Locally, the "Find news (Claude)" button and
scripts/daily.sh start `claude -p <prompt>` in this project folder. Claude reads and
writes the desk's data through `python3 desk.py db ...` and uses web search.

Set SEPA_CLAUDE to the CLI path if `claude` isn't on your PATH.
"""
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_TOOLS = "Bash(python3 desk.py:*),Bash(python desk.py:*),Bash(py desk.py:*),WebSearch,WebFetch,Read"
REPORTS = os.path.join(ROOT, "reports")
_running = {}


def py_cmd():
    """The interpreter name Claude should type in this machine's shell."""
    if os.name == "nt":
        return "python"
    return "python3"


def claude_cmd():
    return os.environ.get("SEPA_CLAUDE") or shutil.which("claude")


def run_prompt(name, timeout=1800):
    """Run prompts/<name>.md with Claude Code. Returns (ok, output_tail)."""
    exe = claude_cmd()
    if not exe:
        return False, "Claude Code CLI ('claude') not found. Install Claude Code or set SEPA_CLAUDE."
    with open(os.path.join(ROOT, "prompts", f"{name}.md")) as fh:
        prompt = fh.read().replace("python3 desk.py", f"{py_cmd()} desk.py")
    try:
        p = subprocess.run([exe, "-p", prompt, "--allowedTools", ALLOWED_TOOLS],
                           cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (("\n" + p.stderr) if p.returncode else "")
        return p.returncode == 0, out[-4000:]
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout // 60} minutes."


def save_report(text, day=None):
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"{day or datetime.now().strftime('%Y-%m-%d')}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def run_news_blocking(store):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.update("app/news", {"status": "running", "startedAt": now, "error": ""})
    ok, out = run_prompt("news-scan")
    cur = (store.get("app/news") or {}).get("data", {})
    if not ok:
        store.update("app/news", {"status": "error", "error": out.strip().splitlines()[-1] if out.strip() else "unknown error"})
    elif cur.get("status") in ("running", "requested"):
        ok = False
        store.update("app/news", {"status": "error", "error": "Claude finished without writing results."})
    return ok, out


def start_news(store):
    """Background news scan, mirroring the claude.ai 'SEPA Desk news scan' task."""
    if _running.get("news") and _running["news"].is_alive():
        return {"started": False, "reason": "already running"}

    def job():
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.update("app/news", {"status": "running", "startedAt": now, "error": ""})
        ok, out = run_prompt("news-scan")
        cur = (store.get("app/news") or {}).get("data", {})
        if not ok:
            store.update("app/news", {"status": "error", "error": out.strip().splitlines()[-1] if out.strip() else "unknown error"})
        elif cur.get("status") in ("running", "requested"):
            store.update("app/news", {"status": "error", "error": "Claude finished without writing results. See the terminal output."})

    t = threading.Thread(target=job, daemon=True)
    _running["news"] = t
    t.start()
    return {"started": True}
