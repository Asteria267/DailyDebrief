Python 3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)] on win32
Type "help", "copyright", "credits" or "license()" for more information.
"""
================================================================================
  DailyDebrief — Day 13  ✈️
================================================================================
  Usage:
      python daily_debrief.py               # debrief the last 24 hours
      python daily_debrief.py --since 8     # last 8 hours only
      python daily_debrief.py --model llama3
      python daily_debrief.py --no-llm      # just show collected data, no AI

  Prerequisites:
      pip install ollama gitpython rich psutil
      ollama pull qwen2.5:3b
      ollama serve    ← separate terminal

  Sensor streams collected:
      ✈  git      — commits, diff stats, changed files, branch state
      ✈  shell    — commands run, error patterns, repeated-command frustration
      ✈  files    — recently modified files, hot-file churn detection
      ✈  packages — pip installs found in shell history

  Unique extras:
      • Frustration Score  — counts errors, repeated commands, ^C patterns
      • Timeline           — every event from all streams merged by timestamp
      • Streak tracking    — saves to ~/.debriefs/; shows consecutive day count
      • Smart compression  — each stream is pre-summarised before hitting the LLM
                             so you never blow the context window

  Hardware concept — Flight Data Recorder (FDR) pattern:
      SENSORS → COMPRESSION → STORAGE → ANALYSIS → REPORT
      Identical pattern in: telemetry systems, observability stacks,
      embedded logging, APM dashboards, black-box recorders.
      The FDR doesn't know what's important — it records everything.
      The debrief is the post-flight analysis.
================================================================================
"""

import argparse, json, os, re, subprocess, sys, time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── optional imports with graceful fallback ───────────────────────────────────
try:
    import git as gitpython
    HAS_GIT = True
except ImportError:
    HAS_GIT = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

import ollama
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


# ─── config ───────────────────────────────────────────────────────────────────
DEFAULT_MODEL   = "qwen2.5:3b"
DEBRIEF_DIR     = Path.home() / ".debriefs"
MAX_SHELL_LINES = 300     # cap on how many history lines we read
MAX_FILES       = 40      # cap on file entries sent to LLM
FRUSTRATION_KEYWORDS = {
    "error", "fatal", "traceback", "permission denied", "no such file",
    "command not found", "segfault", "killed", "timeout", "refused",
    "not found", "failed", "exception", "undefined", "cannot",
}

console = Console()


# ═══════════════════════════════════════════════════════════════════════════════
#  SENSOR 1 — GIT
# ═══════════════════════════════════════════════════════════════════════════════

def collect_git(since_dt: datetime) -> dict:
    """
    Walk up from cwd to find a git repo.
    Returns structured data: commits, total diff stats, changed files, branch.
    Falls back cleanly if no repo is found or gitpython isn't installed.
    """
    result = {
        "found": False, "branch": None, "commits": [],
        "total_insertions": 0, "total_deletions": 0,
        "changed_files": [], "uncommitted_changes": 0,
    }

    if not HAS_GIT:
        result["error"] = "gitpython not installed"
        return result

    try:
        repo = gitpython.Repo(search_parent_directories=True)
    except gitpython.exc.InvalidGitRepositoryError:
        # No git repo found — create a dummy one for demo purposes
        result["error"] = "no git repo found in current directory tree"
        return result

    result["found"]  = True
    result["branch"] = repo.active_branch.name if not repo.head.is_detached else "detached"

    # Count uncommitted changes
    result["uncommitted_changes"] = len(repo.index.diff(None)) + len(repo.untracked_files)

    # Commits since `since_dt`
    for commit in repo.iter_commits():
        commit_dt = datetime.fromtimestamp(commit.committed_date)
        if commit_dt < since_dt:
            break
        stats = commit.stats.total
        result["commits"].append({
            "hash":        commit.hexsha[:7],
            "message":     commit.message.strip().split("\n")[0][:80],
            "time":        commit_dt.strftime("%H:%M"),
            "files":       stats["files"],
            "insertions":  stats["insertions"],
            "deletions":   stats["deletions"],
        })
        result["total_insertions"] += stats["insertions"]
        result["total_deletions"]  += stats["deletions"]
        for fname in commit.stats.files:
            if fname not in result["changed_files"]:
                result["changed_files"].append(fname)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  SENSOR 2 — SHELL HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

def _find_history_file() -> Optional[Path]:
    """Check all common shell history locations in priority order."""
    candidates = [
        Path.home() / ".zsh_history",
        Path.home() / ".bash_history",
        Path.home() / ".histfile",
        Path(os.environ.get("HISTFILE", "")),
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def _parse_history(path: Path) -> list[str]:
    """
    Parse raw history file into a list of command strings.
    Handles both plain bash format and zsh extended format (`: timestamp:0;cmd`).
    """
    lines = []
    try:
        raw = path.read_text(errors="ignore").splitlines()
    except Exception:
        return []

    for line in raw[-MAX_SHELL_LINES:]:
        # zsh extended history: ": 1700000000:0;actual command"
        if line.startswith(": ") and ";」" not in line:
            parts = line.split(";", 1)
            if len(parts) == 2:
                lines.append(parts[1].strip())
                continue
        if line.startswith(":") and line.count(":") >= 2:
            parts = line.split(";", 1)
            if len(parts) == 2:
                lines.append(parts[1].strip())
                continue
        if line.strip() and not line.startswith("#"):
            lines.append(line.strip())
    return lines


def collect_shell(since_dt: datetime) -> dict:
    """
    Reads shell history and computes:
    - commands run (deduplicated counts)
    - error keyword frequency
    - repeated command frustration (same command 3+ times in a row)
    - pip installs
    - frustration score: LOW / MEDIUM / HIGH / CRITICAL
    """
    result = {
        "found": False, "history_file": None,
        "total_commands": 0, "unique_commands": 0,
        "top_commands": [], "error_hits": 0,
        "frustration_score": "LOW", "pip_installs": [],
        "repeated_runs": [],   # commands run 3+ times
        "raw_sample": [],      # last 20 commands for LLM context
    }

    path = _find_history_file()
    if not path:
        result["error"] = "no shell history file found"
        return result

    result["found"]        = True
    result["history_file"] = str(path)
    commands               = _parse_history(path)

    if not commands:
        result["error"] = "history file empty or unreadable"
        return result

    result["total_commands"]  = len(commands)
    result["unique_commands"] = len(set(commands))
    result["raw_sample"]      = commands[-20:]

    # Top commands
    counts = Counter(commands)
    result["top_commands"] = [
        {"cmd": cmd, "count": n}
        for cmd, n in counts.most_common(8)
    ]

    # Repeated runs (frustration indicator)
    repeated = [(cmd, n) for cmd, n in counts.items() if n >= 3]
    result["repeated_runs"] = sorted(repeated, key=lambda x: -x[1])[:5]

    # Error keyword scan
    error_hits = 0
    for cmd in commands:
        low = cmd.lower()
        if any(kw in low for kw in FRUSTRATION_KEYWORDS):
            error_hits += 1
    result["error_hits"] = error_hits

    # Pip installs
    pip_cmds = [c for c in commands if re.match(r"pip\d?\s+install", c)]
    result["pip_installs"] = list(dict.fromkeys(pip_cmds))[:10]

    # Frustration score
    ratio = error_hits / max(len(commands), 1)
    max_repeats = max((n for _, n in result["repeated_runs"]), default=0)
    if ratio > 0.25 or max_repeats >= 8:
        result["frustration_score"] = "CRITICAL"
    elif ratio > 0.15 or max_repeats >= 5:
        result["frustration_score"] = "HIGH"
    elif ratio > 0.07 or max_repeats >= 3:
        result["frustration_score"] = "MEDIUM"
    else:
        result["frustration_score"] = "LOW"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  SENSOR 3 — FILE MODIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def collect_files(since_dt: datetime) -> dict:
    """
    Walk common directories for recently modified files.
    Detects "hot files" — modified multiple times (= active iteration).
    Groups by extension.
    """
    result = {
        "total_modified": 0,
        "files": [],          # [{path, mtime, ext}]
        "hot_files": [],      # modified 2+ times (same name, different mtimes)
        "by_extension": {},
    }

    search_dirs = [Path.cwd(), Path.home() / "Desktop",
                   Path.home() / "Documents", Path.home()]
    seen = set()
    all_files = []
    since_ts = since_dt.timestamp()

    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for p in base.rglob("*"):
                if p in seen or not p.is_file():
                    continue
                # skip hidden dirs, node_modules, .git, __pycache__
                parts = p.parts
                if any(part.startswith(".") or part in
                       {"node_modules", "__pycache__", "venv", ".venv", "env"}
                       for part in parts):
                    continue
                try:
                    mtime = p.stat().st_mtime
                except (PermissionError, OSError):
                    continue
                if mtime >= since_ts:
                    seen.add(p)
                    all_files.append({"path": p, "mtime": mtime, "ext": p.suffix})
        except (PermissionError, OSError):
            continue

    all_files.sort(key=lambda x: -x["mtime"])
    result["total_modified"] = len(all_files)

    # Detect hot files (same stem appears multiple times across dirs)
    stem_counts = Counter(f["path"].stem for f in all_files)
    result["hot_files"] = [
        stem for stem, count in stem_counts.items() if count >= 2
    ][:5]

    # By extension
    for f in all_files:
        ext = f["ext"] or "no ext"
        result["by_extension"][ext] = result["by_extension"].get(ext, 0) + 1

    # Stringify paths for serialisation
    result["files"] = [
        {"path": str(f["path"].relative_to(Path.home()) if
                     f["path"].is_relative_to(Path.home()) else f["path"]),
         "mtime": datetime.fromtimestamp(f["mtime"]).strftime("%H:%M"),
         "ext":   f["ext"]}
        for f in all_files[:MAX_FILES]
    ]

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  SENSOR 4 — SYSTEM SNAPSHOT  (optional, needs psutil)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_system() -> dict:
    """Lightweight system state: uptime, memory pressure, disk usage."""
    if not HAS_PSUTIL:
        return {"found": False, "error": "psutil not installed"}
    try:
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = datetime.fromtimestamp(psutil.boot_time())
        uptime_h = (datetime.now() - boot).seconds // 3600
        return {
            "found":          True,
            "uptime_hours":   uptime_h,
            "memory_pct":     mem.percent,
            "disk_used_pct":  disk.percent,
            "process_count":  len(psutil.pids()),
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
#  COMPRESSION — summarise each stream before sending to LLM
# ═══════════════════════════════════════════════════════════════════════════════

def compress_for_llm(git: dict, shell: dict, files: dict, system: dict,
                     since_hours: int) -> str:
    """
    Builds a compact text summary of all sensor streams.
    The goal: give the LLM maximum signal, minimum noise, within ~800 tokens.
    """
    lines = [f"=== ACTIVITY REPORT (last {since_hours}h) ===\n"]

    # Git
    if git["found"] and git["commits"]:
        lines.append("── GIT ──")
        lines.append(f"Branch: {git['branch']}  |  "
                     f"+{git['total_insertions']} / -{git['total_deletions']} lines  |  "
                     f"{git['uncommitted_changes']} uncommitted changes")
        for c in git["commits"][:6]:
            lines.append(f"  [{c['time']}] {c['hash']}  {c['message']}  "
                         f"({c['files']} files, +{c['insertions']}/-{c['deletions']})")
        if git["changed_files"]:
            lines.append(f"  Key files: {', '.join(git['changed_files'][:6])}")
    elif not git["found"]:
        lines.append(f"── GIT ── {git.get('error','no data')}")
    else:
        lines.append("── GIT ── no commits in this window")
    lines.append("")

    # Shell
    if shell["found"]:
        lines.append("── SHELL ──")
        lines.append(f"Commands: {shell['total_commands']} total  |  "
                     f"{shell['unique_commands']} unique  |  "
                     f"Frustration: {shell['frustration_score']}  |  "
                     f"Error keywords: {shell['error_hits']}x")
        if shell["top_commands"]:
            top = [f"{x['cmd'][:30]} ({x['count']}x)" for x in shell["top_commands"][:5]]
            lines.append(f"  Most run: {', '.join(top)}")
        if shell["repeated_runs"]:
            rep = [f"{cmd[:25]} ({n}x)" for cmd, n in shell["repeated_runs"][:3]]
            lines.append(f"  Repeated (frustration signals): {', '.join(rep)}")
        if shell["pip_installs"]:
            lines.append(f"  Installed: {', '.join(shell['pip_installs'][:5])}")
        if shell["raw_sample"]:
            lines.append(f"  Last commands: {' | '.join(shell['raw_sample'][-8:])}")
    else:
        lines.append(f"── SHELL ── {shell.get('error','no data')}")
    lines.append("")

    # Files
    lines.append("── FILES ──")
    lines.append(f"Modified: {files['total_modified']} files")
    if files["hot_files"]:
        lines.append(f"  Hot files (iterated on): {', '.join(files['hot_files'])}")
    if files["by_extension"]:
        exts = sorted(files["by_extension"].items(), key=lambda x: -x[1])
        lines.append(f"  By type: {', '.join(f'{e}({n})' for e,n in exts[:6])}")
    if files["files"]:
        recent = [f"{f['path']} @{f['mtime']}" for f in files["files"][:8]]
        lines.append(f"  Recent: {', '.join(recent)}")
    lines.append("")

    # System
    if system.get("found"):
        lines.append("── SYSTEM ──")
        lines.append(f"Uptime: {system['uptime_hours']}h  |  "
                     f"Memory: {system['memory_pct']}%  |  "
                     f"Disk: {system['disk_used_pct']}%  |  "
                     f"Processes: {system['process_count']}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM — DEBRIEF GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

DEBRIEF_PROMPT = """You are a senior engineer writing a daily debrief for a developer.
Analyse the activity data and write EXACTLY 5 sections.
Each section: 1-2 sentences. Be specific — use actual file names, commit messages, and tool names from the data. No generic filler.

{context}

Reply with ONLY this JSON — no markdown, no code fences, no explanation:
{{
  "built":    "What was actually shipped or meaningfully progressed today.",
  "broke":    "What failed, errored, or caused friction. If nothing broke, say so honestly.",
  "learned":  "The most interesting or surprising technical insight from today's work.",
  "next":     "The single most important thing to tackle tomorrow, based on today's momentum.",
  "oneliner": "One punchy sentence that captures today's essence — like a commit message for the whole day."
}}"""


def run_debrief(model: str, context: str) -> Optional[dict]:
    """
    Send compressed context to LLM, parse JSON response.
    Returns dict with 5 keys or None on failure.
    raw_text initialised before try block to avoid silent reference errors.
    """
    raw_text = ""
    prompt   = DEBRIEF_PROMPT.format(context=context)
    try:
        r        = ollama.chat(model=model, messages=[
            {"role": "user", "content": prompt}
        ])
        raw_text = r["message"]["content"].strip()
        # strip markdown fences if model adds them
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$",        "", raw_text).strip()
        data     = json.loads(raw_text)
        required = {"built", "broke", "learned", "next", "oneliner"}
        if not required.issubset(data.keys()):
            raise ValueError(f"Missing keys: {required - data.keys()}")
        return data
    except json.JSONDecodeError:
        console.print(f"[red]LLM returned invalid JSON.[/red]")
        console.print(f"[dim]Raw: {raw_text[:300]}[/dim]")
        return None
    except Exception as e:
        console.print(f"[red]LLM error: {e}[/red]")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  STREAK TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def load_streak() -> int:
    """Count consecutive days with saved debriefs up to and including today."""
    if not DEBRIEF_DIR.exists():
        return 0
    streak, day = 0, datetime.now().date()
    while True:
        path = DEBRIEF_DIR / f"{day}.json"
        if path.exists():
            streak += 1
            day -= timedelta(days=1)
        else:
            break
    return streak


def save_debrief(debrief: dict, git: dict, shell: dict, files: dict) -> Path:
    """Save today's full debrief to ~/.debriefs/YYYY-MM-DD.json."""
    DEBRIEF_DIR.mkdir(exist_ok=True)
    today = datetime.now().date().isoformat()
    path  = DEBRIEF_DIR / f"{today}.json"
    payload = {
        "date":    today,
        "debrief": debrief,
        "stats": {
            "commits":        len(git.get("commits", [])),
            "insertions":     git.get("total_insertions", 0),
            "deletions":      git.get("total_deletions", 0),
            "commands":       shell.get("total_commands", 0),
            "files_modified": files.get("total_modified", 0),
            "frustration":    shell.get("frustration_score", "N/A"),
        }
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  RICH RENDERER — the beautiful output
# ═══════════════════════════════════════════════════════════════════════════════

FRUSTRATION_COLOR = {
    "LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"
}

def _streak_bar(n: int, width: int = 20) -> str:
    filled = min(n, width)
    return "█" * filled + "░" * (width - filled) + f"  {n} day{'s' if n != 1 else ''}"


def _sensor_table(git: dict, shell: dict, files: dict, system: dict) -> Table:
    t = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
    t.add_column("sensor", style="dim", width=10)
    t.add_column("status", width=6)
    t.add_column("summary")

    # git row
    if git["found"] and git["commits"]:
        t.add_row(
            "✈  git",
            "[green]●[/green]",
            f"[bold]{len(git['commits'])} commit{'s' if len(git['commits'])!=1 else ''}[/bold]  "
            f"[green]+{git['total_insertions']}[/green] / [red]-{git['total_deletions']}[/red] lines  "
            f"branch: [cyan]{git['branch']}[/cyan]"
        )
    elif not git["found"]:
        t.add_row("✈  git", "[dim]○[/dim]", f"[dim]{git.get('error','—')}[/dim]")
    else:
        t.add_row("✈  git", "[yellow]●[/yellow]", "[dim]no commits in window[/dim]")

    # shell row
    if shell["found"]:
        fc  = FRUSTRATION_COLOR[shell["frustration_score"]]
        top = shell["top_commands"][0]["cmd"][:20] if shell["top_commands"] else "—"
        t.add_row(
            "✈  shell",
            "[green]●[/green]",
            f"[bold]{shell['total_commands']} commands[/bold]  "
            f"frustration: [{fc}]{shell['frustration_score']}[/{fc}]  "
            f"most run: [cyan]{top}[/cyan]"
        )
    else:
        t.add_row("✈  shell", "[dim]○[/dim]", f"[dim]{shell.get('error','—')}[/dim]")

    # files row
    hot = ", ".join(files["hot_files"][:3]) if files["hot_files"] else "none"
    t.add_row(
        "✈  files",
        "[green]●[/green]" if files["total_modified"] else "[dim]○[/dim]",
        f"[bold]{files['total_modified']} modified[/bold]  "
        f"hot files: [cyan]{hot}[/cyan]"
    )

    # packages row
    if shell.get("pip_installs"):
        t.add_row(
            "✈  packages",
            "[green]●[/green]",
            "  ".join(f"[magenta]{p}[/magenta]" for p in shell["pip_installs"][:4])
        )

    # system row
    if system.get("found"):
        mem_color = "red" if system["memory_pct"] > 85 else "yellow" if system["memory_pct"] > 70 else "green"
        t.add_row(
            "✈  system",
            "[green]●[/green]",
            f"uptime [cyan]{system['uptime_hours']}h[/cyan]  "
            f"mem [{mem_color}]{system['memory_pct']}%[/{mem_color}]  "
            f"disk {system['disk_used_pct']}%"
        )

    return t


def render(debrief: Optional[dict], git: dict, shell: dict, files: dict,
           system: dict, since_hours: int, saved_path: Optional[Path],
           streak: int, model: str, elapsed: float):
    """Full rich terminal output — the flight debrief readout."""

    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    console.print()

    # ── header ────────────────────────────────────────────────────────────────
    console.print(Panel(
        Text.assemble(
            ("  ✈  DAILY DEBRIEF\n", "bold yellow"),
            (f"  BuildCored Orcas  •  Day 13  •  {now}", "dim"),
        ),
        box=box.DOUBLE,
        border_style="yellow",
        padding=(0, 2),
    ))

    # ── sensor streams ────────────────────────────────────────────────────────
    console.print(Panel(
        _sensor_table(git, shell, files, system),
        title="[dim]SENSOR STREAMS[/dim]",
        border_style="dim",
        box=box.ROUNDED,
    ))

    if debrief is None:
        console.print("[red]No debrief generated — LLM failed or --no-llm passed.[/red]")
        console.print()
        return

    # ── 4-quadrant panels ─────────────────────────────────────────────────────
    def make_panel(title: str, content: str, color: str) -> Panel:
        return Panel(
            Text(content, style="white"),
            title=f"[bold {color}]{title}[/bold {color}]",
            border_style=color,
            box=box.ROUNDED,
            padding=(1, 2),
            width=42,
        )

    top_row = Columns([
        make_panel("🔨  WHAT YOU BUILT", debrief["built"],   "cyan"),
        make_panel("💥  WHAT BROKE",     debrief["broke"],   "red"),
    ], equal=True)

    bottom_row = Columns([
        make_panel("💡  WHAT YOU LEARNED", debrief["learned"], "green"),
        make_panel("🚀  WHAT'S NEXT",      debrief["next"],    "magenta"),
    ], equal=True)

    console.print(top_row)
    console.print(bottom_row)

    # ── one-liner ──────────────────────────────────────────────────────────────
    console.print(Panel(
        Text.assemble(
            ('"', "dim yellow"),
            (debrief["oneliner"], "bold white"),
            ('"', "dim yellow"),
        ),
        title="[bold yellow]✨  TODAY IN ONE LINE[/bold yellow]",
        border_style="yellow",
        box=box.HEAVY,
        padding=(1, 4),
    ))

    # ── footer ────────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [dim]Streak:[/dim]  [yellow]{_streak_bar(streak)}[/yellow]")
    if saved_path:
        console.print(f"  [dim]Saved  :[/dim]  [dim]{saved_path}[/dim]")
    console.print(f"  [dim]Model  :[/dim]  [dim]{model}  ({elapsed:.1f}s)[/dim]")
    console.print()


def render_raw(git: dict, shell: dict, files: dict, system: dict,
               since_hours: int):
    """--no-llm mode: just dump the collected data clearly."""
    console.print(Rule("[yellow]Collected Sensor Data[/yellow]"))
    console.print_json(json.dumps({
        "git":   {k: v for k, v in git.items()   if k != "commits"} | {"commit_count": len(git.get("commits",[]))},
        "shell": {k: v for k, v in shell.items() if k != "raw_sample"},
        "files": {k: v for k, v in files.items() if k != "files"} | {"sample_files": files["files"][:5]},
        "system": system,
    }, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="DailyDebrief — flight data recorder for your dev day")
    ap.add_argument("--model",   "-m", default=DEFAULT_MODEL)
    ap.add_argument("--since",   "-s", type=int, default=24,
                    help="Hours to look back (default 24)")
    ap.add_argument("--no-llm",        action="store_true",
                    help="Collect data only — skip LLM, print raw sensor output")
    args = ap.parse_args()

    since_dt = datetime.now() - timedelta(hours=args.since)

    # ── preflight ─────────────────────────────────────────────────────────────
    if not args.no_llm:
        console.print(f"\n  [dim]Connecting to ollama ({args.model})…[/dim]",
                      end="", highlight=False)
        try:
            available = [m["model"] for m in ollama.list().get("models", [])]
            base      = args.model.split(":")[0]
            if not any(base in m for m in available):
                console.print(f"\n\n  [red]Model '{args.model}' not found.[/red]")
                console.print(f"  Run:  [yellow]ollama pull {args.model}[/yellow]\n")
                sys.exit(1)
            console.print(" [green]✓[/green]")
        except Exception as e:
            console.print(f"\n  [red]Can't reach ollama — {e}[/red]")
            console.print("  Run: [yellow]ollama serve[/yellow] in another terminal.\n")
            sys.exit(1)

    # ── data collection ───────────────────────────────────────────────────────
    console.print(f"\n  [dim]Collecting sensor streams (last {args.since}h)…[/dim]")

    with console.status("[dim]git…[/dim]"):
        git = collect_git(since_dt)
    console.print(f"  [green]✓[/green] git       — "
                  f"{len(git.get('commits',[]))} commits")

    with console.status("[dim]shell history…[/dim]"):
        shell = collect_shell(since_dt)
    console.print(f"  [green]✓[/green] shell     — "
                  f"{shell.get('total_commands', 0)} commands  "
...                   f"frustration: {shell.get('frustration_score','N/A')}")
... 
...     with console.status("[dim]file modifications…[/dim]"):
...         files = collect_files(since_dt)
...     console.print(f"  [green]✓[/green] files     — "
...                   f"{files['total_modified']} modified")
... 
...     system = collect_system()
...     if system.get("found"):
...         console.print(f"  [green]✓[/green] system    — "
...                       f"mem {system['memory_pct']}%  uptime {system['uptime_hours']}h")
... 
...     if args.no_llm:
...         render_raw(git, shell, files, system, args.since)
...         return
... 
...     # ── LLM ───────────────────────────────────────────────────────────────────
...     context = compress_for_llm(git, shell, files, system, args.since)
...     console.print(f"\n  [dim]Sending to {args.model}…[/dim]")
...     t0      = time.time()
... 
...     with console.status(f"[dim]{args.model} is thinking…[/dim]"):
...         debrief = run_debrief(args.model, context)
... 
...     elapsed = time.time() - t0
... 
...     # ── save & streak ─────────────────────────────────────────────────────────
...     saved_path = None
...     if debrief:
...         saved_path = save_debrief(debrief, git, shell, files)
...     streak = load_streak()
... 
...     # ── render ────────────────────────────────────────────────────────────────
...     render(debrief, git, shell, files, system,
...            args.since, saved_path, streak, args.model, elapsed)
... 
... 
... if __name__ == "__main__":
