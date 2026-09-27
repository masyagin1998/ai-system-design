#!/usr/bin/env -S python3 -E -S
"""ai-log: append the AI dialogue of this repo to ai-logs/, straight from Claude Code and Codex
hooks (no transcript parsing).

ai_log.py hook claude|codex         (stdin: hook JSON; prints nothing, always exits 0)
ai_log.py clean                    (empty ai-logs/ except README.md)
ai_log.py scan PATH... | scan -     (secret patterns, home paths, files > 50 MB; exit 1 on
                                     findings; "-" reads NUL/newline-separated paths)
"""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
TOOL_NAMES = {"claude": "Claude", "codex": "Codex"}
# SessionStart.source -> the line it leaves in the session file ("" = none: the file header).
SESSION_NOTES = {
    "startup": "",
    "clear": "новый контекст после `/clear`",
    "compact": "контекст сжат",
    "resume": "сессия продолжена",
}
PROMPTS_HEADER = (
    "# Промпты\n\nВсе мои реплики агентам (Codex, Claude Code) по времени: промпты, ответы на "
    "вопросы агента, одобрения и отклонения планов. У каждой — этап таймера и ссылка на сессию.\n\n"
)
LONG_PROMPT_LINES = 40  # longer prompts (pasted RFCs, logs) show 20 lines, the rest collapsed
REJECTED = "doesn't want to proceed"
FEEDBACK_RE = re.compile(r"the user said:\n(.*?)(?:\n\nNote: The user's next message|\Z)", re.S)
SCAN_MAX_BYTES = 50 * 1024 * 1024
# Line pragma for intentional fixtures in source files; never honoured inside ai-logs/.
SCAN_ALLOW = "ai-logs-scan: allow"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".whl", ".so", ".pyc",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".mov", ".tar", ".gz", ".xz", ".bz2", ".7z", ".jar",
}  # fmt: skip


def out_dir() -> Path:
    # AI_LOGS_OUT exists for tests; real runs always write to <repo>/ai-logs.
    return Path(os.environ.get("AI_LOGS_OUT") or ROOT / "ai-logs")


def local_file(name: str) -> Path:
    """Gitignored hook diagnostics (.local/): the error log and the AI_LOGS_DEBUG dump."""
    d = Path(os.environ.get("AI_LOGS_LOCAL") or ROOT / ".local")
    d.mkdir(parents=True, exist_ok=True)
    return d / name


# ---------- redaction ----------

PATTERNS = {
    "anthropic": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    # A digit is required: "ask-for-a-workflow-..." slugs look like keys.
    "openai": r"(?<![A-Za-z0-9])sk-(?:proj-)?(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{24,}",
    "aws": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}",
    "gitlab": r"\bglpat-[A-Za-z0-9_-]{20,}",
    "slack": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "google": r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])",
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.(?:[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?)?",
    "pem": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
}
BEARER = re.compile(r"(?i)(authorization:\s*(?:bearer|api-key|token)\s+)([A-Za-z0-9._~+/=-]{16,})")
KV = re.compile(
    r"(?i)((?:PASSWORD|PASSWD|SECRET|TOKEN|API[-_]?KEY|ACCESS[-_]?KEY)[A-Z0-9_]*)"
    r"([\"']?\s*[=:]\s*[\"']?)(?!\$\{|<|\*)([^\s'\"`(){}\[\],;]{8,})"
)
DSN = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@\"'`]*:)([^@\s/\"'`]{1,200})(@)")
ENV_KEY = re.compile(r"(?i)pass|secret|token|key")
# `key=settings.S3_KEY` is code: an attribute chain without digits or from a lowercase root.
ATTR_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
PLACEHOLDER = re.compile(r"^(\$\{.*\}|<.*>|\*+|\{.*\}|x+|\.+)$", re.I)
HASH_PREFIXES = ("$argon2", "$2a$", "$2b$", "$2y$", "$scrypt$", "pbkdf2")
DEV_DEFAULTS = {"password", "pass", "secret", "postgres", "guest", "changeme", "example",
                "rustfsadmin", "dev-only-jwt-secret-change-in-prod"}  # fmt: skip


def _env_file(p: Path) -> dict[str, str]:
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            out[k.removeprefix("export ").strip()] = v.split(" #", 1)[0].strip().strip("'\"")
    return out


class Redactor:
    def __init__(self, root: Path = ROOT):
        self.home = str(Path.home())
        self.allow = {v for v in _env_file(root / ".env.example").values() if v} | DEV_DEFAULTS
        literals = set()
        for src in (_env_file(root / ".env"), _env_file(root / ".env.local"), os.environ):
            for k, v in src.items():
                # Paths and flags under "key"-ish names (GNOME_KEYRING_CONTROL=/run/...) aren't
                # secrets, and redacting them would shred ordinary text.
                if not ENV_KEY.search(k) or len(v) < 8 or v in self.allow or v.isdigit():
                    continue
                if not v.startswith(("/", "~", ".")) and v.lower() not in ("true", "false"):
                    literals.add(v)
        for p in (Path.home() / ".gitconfig", root / ".git" / "config"):
            try:  # the committer's email stays out of committed logs
                literals |= set(re.findall(r"(?m)^\s*email\s*=\s*(\S+@\S+)\s*$", p.read_text()))
            except OSError:
                pass
        deny = os.environ.get("AI_LOGS_DENYLIST") or Path.home() / ".config/ai-logs/redact.txt"
        try:  # the author's private literals, one per line
            lines = [s.strip() for s in Path(deny).read_text().splitlines()]
            literals |= {s for s in lines if s and not s.startswith("#")}
        except OSError:
            pass
        by_len = sorted(literals, key=len, reverse=True)
        self._lit = re.compile("|".join(map(re.escape, by_len))) if literals else None
        self._pats = [(k, re.compile(v)) for k, v in PATTERNS.items()]
        slug = re.sub(r"[^A-Za-z0-9]", "-", self.home)  # Claude's project dir (-home-u-repo)
        home_re = rf"{re.escape(self.home)}(?![A-Za-z0-9_-])|{re.escape(slug)}(?![A-Za-z0-9])"
        self._home = re.compile(home_re) if self.home not in ("", "/") else None

    def text(self, s: str, hits: list[str] | None = None) -> str:
        hits = [] if hits is None else hits

        def hit(kind: str, repl: str) -> str:
            hits.append(kind)
            return repl

        if self._lit is not None:
            s = self._lit.sub(lambda m: hit("private", "[REDACTED:private]"), s)
        for name, rx in self._pats:
            s = rx.sub(lambda m, n=name: hit(n, f"[REDACTED:{n}]"), s)
        s = BEARER.sub(lambda m: m.group(1) + hit("bearer", "[REDACTED:bearer]"), s)
        s = KV.sub(lambda m: self._kv(m, hit), s)
        s = DSN.sub(lambda m: self._dsn(m, hit), s)
        if self._home is not None:
            s = self._home.sub(lambda m: hit("home-path", "~"), s)
        return s

    def kinds(self, s: str) -> list[str]:
        hits: list[str] = []
        self.text(s, hits)
        return sorted(set(hits))

    def _kv(self, m: re.Match, hit) -> str:
        v = m.group(3)
        looks_secret = (
            len(v) >= 20
            or bool(re.search(r"\d", v) and re.search(r"[A-Za-z]", v))
            or bool(re.search(r"(?i)pass|secret", m.group(1)))  # passphrase: only letters, under 20
        )
        if (
            v in self.allow
            or v.startswith("[REDACTED")
            or (v.startswith("/") and len(v) < 20)  # a path (tokenUrl="/auth/login") is not one
            or PLACEHOLDER.match(v)
            or not looks_secret
            or v.startswith(HASH_PREFIXES)
            or (ATTR_RE.fullmatch(v) and (not re.search(r"\d", v) or v[0].islower()))
            or m.string[m.end() : m.end() + 1] == "("  # issue_token(user): a call, i.e. code
        ):
            return m.group(0)
        return m.group(1) + m.group(2) + hit("kv", "[REDACTED:kv]")

    def _dsn(self, m: re.Match, hit) -> str:
        pw = m.group(2)
        # 1-3 characters (u:p@) is a placeholder in docs and tests, not a credential.
        if len(pw) < 4 or pw in self.allow or PLACEHOLDER.match(pw) or pw.startswith("[REDACTED"):
            return m.group(0)
        return m.group(1) + hit("dsn", "[REDACTED:dsn]") + m.group(3)


# ---------- hook ----------


def training_active() -> bool:
    """Логируем только в запущенной тренировке и её ветке."""
    try:
        sys.path.insert(0, str(TOOLS))
        import timer

        state = timer.load()
        if state is None or state.get("paused_at") is not None or timer.elapsed(state) >= timer.TOTAL:
            return False
        branch = (ROOT / ".local" / "interview-branch").read_text().strip()
        current = subprocess.run(
            ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True, timeout=2
        )
        return bool(branch) and current.returncode == 0 and current.stdout.strip() == branch
    except Exception:
        return False


def phase_tag(now: float) -> str:
    """`Реализация с AI › Ключевые ручки · T+42:10` from the timer, or "" when it is not running."""
    try:
        sys.path.insert(0, str(TOOLS))
        import timer

        state = timer.load()
        if state is None:
            return ""
        t = timer.elapsed(state, now)
        stage, step = timer.where(t)
    except Exception:
        return ""
    return f"{stage.name} › {step.name} · T+{timer.fmt(t)}"


def quote(text: str) -> str:
    """Human text as a blockquote; a long paste keeps 20 lines visible and folds the rest."""
    lines = [f"> {ln}".rstrip() for ln in text.strip("\n").splitlines()] or [">"]
    if len(lines) <= LONG_PROMPT_LINES:
        return "\n".join(lines)
    head, rest = "\n".join(lines[:20]), "\n".join(lines[20:])
    return head + "\n\n" + details(f"ещё {len(lines) - 20} строк", rest)


def details(summary: str, body: str) -> str:
    return f"<details><summary>{summary}</summary>\n\n{body.strip()}\n\n</details>"


def _answer(a) -> str:
    return ", ".join(map(str, a)) if isinstance(a, list) else str(a)


def answers_md(tool_input: dict, resp) -> str | None:
    resp = resp if isinstance(resp, dict) else {}
    answers = resp.get("answers") if isinstance(resp.get("answers"), dict) else {}
    notes = resp.get("annotations") if isinstance(resp.get("annotations"), dict) else {}
    if not answers:
        return None
    heads = {
        q.get("question"): q.get("header")
        for q in tool_input.get("questions") or []
        if isinstance(q, dict)
    }
    out = []
    for q, a in answers.items():
        note = notes.get(q) if isinstance(notes.get(q), dict) else {}
        typed = note.get("notes") if isinstance(note.get("notes"), str) else ""
        line = f"- **{heads.get(q) or 'Q'}**: {q}\n  → **{_answer(a)}**"
        out.append(line + (f" (note: {typed.strip()})" if typed.strip() else ""))
    return "\n".join(out)


def entries(tool: str, p: dict) -> list[tuple[str, str, bool]]:
    """(heading, body, is_human_input) for one hook payload; [] when there is nothing to log."""
    ev = p.get("hook_event_name")
    name = TOOL_NAMES[tool]
    ti = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
    tool_name = p.get("tool_name") or ""
    if ev == "SessionStart":
        note = SESSION_NOTES.get(p.get("source") or "startup", f"session {p.get('source')}")
        return [("", f"_{note}_", False)] if note else []
    if ev == "UserPromptSubmit":
        prompt = str(p.get("prompt") or "")
        if prompt.lstrip().startswith(("<task-notification", "<system-reminder")):
            return []  # Claude Code присылает так завершения фоновых задач, это не я
        return [("Я", quote(prompt), True)]
    if ev == "Stop":
        return [(name, str(p.get("last_assistant_message") or "_(без текста)_").strip(), False)]
    if ev == "SubagentStop":
        msg = str(p.get("last_assistant_message") or "").strip()
        kind = p.get("agent_type") or "agent"
        return [("", details(f"← отчёт субагента <code>{kind}</code>", msg), False)] if msg else []
    if ev == "PreToolUse" and tool_name in ("Agent", "Task"):
        head = f"> **→ Субагент** `{ti.get('subagent_type') or 'general-purpose'}`: "
        head += str(ti.get("description") or "").strip()
        return [("", head + "\n\n" + details("Задание", str(ti.get("prompt") or "")), False)]
    if ev == "PostToolUse" and tool_name == "AskUserQuestion":
        md = answers_md(ti, p.get("tool_response"))
        return [("Мой ответ", md, True)] if md else []
    if ev == "PostToolUse" and tool_name == "ExitPlanMode":
        resp = p.get("tool_response") if isinstance(p.get("tool_response"), dict) else {}
        plan = str(resp.get("plan") or ti.get("plan") or "")  # the plan as approved (or edited)
        return [("Я одобрил план", details("План", plan), True)]
    if ev == "PostToolUseFailure":
        err = str(p.get("error") or p.get("tool_response") or "")
        if REJECTED not in err:
            return []
        m = FEEDBACK_RE.search(err)
        fb = quote(m.group(1)) if m else "_(без комментария)_"
        return [(f"Я отклонил `{tool_name or 'вызов'}`", fb, True)]
    return []


def session_file(tool: str, sid: str) -> Path:
    sessions = out_dir() / "sessions"
    id8 = re.sub(r"[^A-Za-z0-9]", "", sid)[-8:] or "unknown"  # у UUIDv7 начало — время
    found = sorted(sessions.glob(f"*-{tool}-{id8}.md"))
    return found[0] if found else sessions / f"{datetime.now():%Y%m%d-%H%M}-{tool}-{id8}.md"


def append(path: Path, text: str, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # parallel subagents finish concurrently
        if os.fstat(f.fileno()).st_size == 0:
            f.write(header)
        f.write(text)


def cmd_hook(tool: str, p: dict) -> None:
    if os.environ.get("AI_LOGS_DEBUG") == "1":  # smoke test: which fields did the hook get?
        with open(local_file("ai-log-payloads.jsonl"), "a") as f:
            f.write(json.dumps({"tool": tool, "event": p.get("hook_event_name"), "payload": p}))
            f.write("\n")
    items = entries(tool, p)
    if not items:
        return
    red = Redactor()
    now = time.time()
    hhmm = datetime.fromtimestamp(now).strftime("%H:%M")
    tag = phase_tag(now)
    when = " · ".join(filter(None, [hhmm, tag]))
    sf = session_file(tool, str(p.get("session_id") or ""))
    header = f"# Сессия {TOOL_NAMES[tool]} `{sf.stem.rsplit('-', 1)[-1]}`\n\n"
    header += f"Начало: {datetime.now():%Y-%m-%d %H:%M}\n\n"
    for heading, body, human in items:
        block = f"### {heading} · {when}\n\n{body}\n\n" if heading else f"{body}\n\n"
        append(sf, red.text(block), header)
        if human:
            link = f"[{sf.stem}](sessions/{sf.name})"
            block = f"### {heading} · {when} · {link}\n\n{body}\n\n"
            append(out_dir() / "PROMPTS.md", red.text(block), PROMPTS_HEADER)


def hook_main(tool: str) -> None:
    """Never raises, never prints: hook stdout enters the agent's context and exit code 2
    blocks the prompt."""
    if os.environ.get("AI_LOGS_DISABLE") == "1" or tool not in TOOL_NAMES or not training_active():
        return
    try:
        raw = sys.stdin.read() if sys.stdin and not sys.stdin.isatty() else ""
        p = json.loads(raw) if raw.strip() else {}
        if isinstance(p, dict):
            cmd_hook(tool, p)
    except Exception as e:
        try:
            with open(local_file("ai-log-errors.log"), "a") as f:
                f.write(f"{datetime.now().isoformat()} {tool}: {type(e).__name__}: {e}\n")
        except OSError:
            pass


# ---------- clean ----------


def cmd_clean() -> int:
    out = out_dir()
    shutil.rmtree(out / "sessions", ignore_errors=True)
    (out / "PROMPTS.md").unlink(missing_ok=True)
    print(f"ai-log: cleaned {out}")
    return 0


# ---------- scan ----------


def _iter_files(paths: list[str]):
    for base in paths:
        p = Path(base)
        if SKIP_DIRS.intersection(p.parts[:-1]):
            continue
        if p.is_file():
            yield p
        elif p.is_dir():
            for dp, dns, fns in os.walk(p):
                dns[:] = [d for d in dns if d not in SKIP_DIRS]
                yield from (Path(dp) / fn for fn in fns)


def scan_paths(paths: list[str]) -> list[str]:
    red = Redactor()
    logs = out_dir().resolve()
    findings = []
    for f in _iter_files(paths):
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size > SCAN_MAX_BYTES:
            findings.append(f"{f}: {size / 1e6:.1f} MB exceeds the 50 MB limit")
            continue
        if f.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = f.read_bytes()
        if b"\0" in data[:8192]:
            continue
        text = data.decode("utf-8", "replace")
        if not red.kinds(text):
            continue
        allow = logs not in f.resolve().parents
        per_line = []
        for n, line in enumerate(text.splitlines(), 1):
            if not (allow and SCAN_ALLOW in line):
                per_line += [f"{f}:{n}: {k}" for k in red.kinds(line)]
        if "pem" in red.kinds(text) and not any(x.endswith(": pem") for x in per_line):
            per_line.append(f"{f}: pem (multi-line private key)")
        findings += per_line
    return findings


def cmd_scan(paths: list[str]) -> int:
    if paths == ["-"]:
        data = sys.stdin.buffer.read()
        sep = b"\0" if b"\0" in data else b"\n"
        paths = [x.decode("utf-8", "surrogateescape") for x in data.split(sep) if x.strip()]
    findings = scan_paths(paths)
    for line in findings[:200]:
        print(line)
    if len(findings) > 200:
        print(f"... and {len(findings) - 200} more")
    print(f"ai-log scan: {len(findings)} finding(s) in {len(paths)} path(s)")
    return 1 if findings else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ai_log.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook").add_argument("tool", choices=sorted(TOOL_NAMES))
    sub.add_parser("clean")
    sub.add_parser("scan").add_argument("paths", nargs="+")
    if argv[:1] == ["hook"]:  # no argparse errors (exit 2) on the hook path
        hook_main(argv[1] if len(argv) > 1 else "")
        return 0
    args = ap.parse_args(argv)
    if args.cmd == "clean":
        return cmd_clean()
    return cmd_scan(args.paths)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
