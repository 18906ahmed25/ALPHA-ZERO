from __future__ import annotations

import base64
import html
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import requests
import streamlit as st

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # Neon/PostgreSQL is optional; SQLite is the local fallback.
    psycopg = None
    dict_row = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# ============================================================
# ALPHA ZERO | single-file app
# ============================================================
APP_NAME = "ALPHA ZERO"
DEFAULT_MODEL = "gpt-6-luna"
WORKSPACE_DIR = Path(os.getenv("ALPHA_ZERO_WORKSPACE", "alpha_zero_workspace")).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
MAX_ACTION_TEXT = 120_000
MAX_READ_TEXT = 18_000
GITHUB_API = "https://api.github.com"

st.set_page_config(
    page_title="ALPHA ZERO | Agent Command Center",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded",
)


# -----------------------------
# Config and secrets
# -----------------------------
def setting(name: str, default: str = "") -> str:
    """Read Streamlit Secrets first, then environment variables."""
    try:
        value = st.secrets.get(name, default)
        if value is not None and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    value = os.getenv(name, default)
    return str(value).strip() if value is not None else default


MODEL_NAME = setting("OPENAI_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
OPENAI_API_KEY = setting("OPENAI_API_KEY")
NEON_DATABASE_URL = setting("NEON_DATABASE_URL") or setting("DATABASE_URL")
NAMESPACE = setting("ALPHA_ZERO_NAMESPACE", "personal") or "personal"
SQLITE_PATH = Path(setting("ALPHA_ZERO_SQLITE_PATH", "alpha_zero_memory.db")).resolve()
GITHUB_TOKEN = setting("GITHUB_TOKEN")
GITHUB_OWNER = setting("GITHUB_OWNER")
GITHUB_REPO = setting("GITHUB_REPO")
APP_API_BASE_URL = setting("APP_API_BASE_URL").rstrip("/")
APP_API_BEARER_TOKEN = setting("APP_API_BEARER_TOKEN")
APP_API_ALLOWED_PATHS = [
    p.strip().rstrip("/") for p in setting("APP_API_ALLOWED_PATHS", "").split(",") if p.strip()
]
APP_WEBHOOK_URL = setting("APP_WEBHOOK_URL")
TRANSCRIPTION_MODEL = setting("AUDIO_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"


# -----------------------------
# Twenty specialist agents
# -----------------------------
AGENTS: dict[str, dict[str, str]] = {
    "researcher": {"name": "Research Scout", "icon": "🔎", "focus": "Gather facts, compare sources, and return concise research findings."},
    "planner": {"name": "Strategy Planner", "icon": "🧭", "focus": "Break goals into ordered, testable steps with dependencies."},
    "developer": {"name": "Software Builder", "icon": "🧑‍💻", "focus": "Design and write reliable software, scripts, and code files."},
    "qa_tester": {"name": "QA Tester", "icon": "🧪", "focus": "Create test cases, identify edge cases, and validate expected behavior."},
    "data_analyst": {"name": "Data Analyst", "icon": "📊", "focus": "Analyze structured information, metrics, patterns, and calculations."},
    "writer": {"name": "Content Writer", "icon": "✍️", "focus": "Write clear, useful, audience-appropriate content."},
    "fact_checker": {"name": "Fact Checker", "icon": "✅", "focus": "Check assumptions, label uncertainty, and flag unsupported claims."},
    "security": {"name": "Security Analyst", "icon": "🛡️", "focus": "Review secrets handling, permissions, threat surfaces, and security risks."},
    "api_integrator": {"name": "API Integrator", "icon": "🔌", "focus": "Plan and validate API integrations, auth, request bodies, and error handling."},
    "database": {"name": "Database Engineer", "icon": "🗄️", "focus": "Design persistence, schema, migrations, and reliable data access."},
    "ui_ux": {"name": "UI/UX Designer", "icon": "🎨", "focus": "Improve interface clarity, accessibility, visual hierarchy, and workflows."},
    "automation": {"name": "Automation Engineer", "icon": "⚙️", "focus": "Design safe repeatable workflows and automation triggers."},
    "documenter": {"name": "Documentation Writer", "icon": "📚", "focus": "Produce setup instructions, README content, guides, and examples."},
    "debugger": {"name": "Debugger", "icon": "🧰", "focus": "Trace failures, identify likely root causes, and propose minimal fixes."},
    "project_manager": {"name": "Project Manager", "icon": "📌", "focus": "Track deliverables, dependencies, blockers, and milestones."},
    "product_strategist": {"name": "Product Strategist", "icon": "💡", "focus": "Translate user needs into practical product behavior and priorities."},
    "defensive_cyber": {"name": "Defensive Cybersecurity", "icon": "🔐", "focus": "Recommend defensive security checks, monitoring, and hardening."},
    "file_manager": {"name": "Workspace Manager", "icon": "🗂️", "focus": "Organize project files and safely read or create workspace artifacts."},
    "browser_researcher": {"name": "Web Research Agent", "icon": "🌐", "focus": "Use web search for current documentation, public facts, and references."},
    "final_reporter": {"name": "Final Report Editor", "icon": "📝", "focus": "Combine worker results into a direct report with outcomes and next steps."},
}

TOOL_DESCRIPTIONS = {
    "web_search": "Search the public web using OpenAI's hosted web-search tool. Arguments: {query: string}. Read-only.",
    "workspace_list": "List files under the ALPHA ZERO runtime workspace. Arguments: {path?: relative directory}.",
    "workspace_read": "Read a text file inside the ALPHA ZERO runtime workspace. Arguments: {path: relative path}.",
    "workspace_write": "Create a new UTF-8 text/code file inside the runtime workspace. Arguments: {path: relative path, content: string}. New files may be created automatically; overwrites need approval.",
    "workspace_delete": "Delete a file inside the runtime workspace. Arguments: {path: relative path}. Always requires explicit approval.",
    "github_read": "Read repo metadata or a path in the configured GitHub repository. Secrets: GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO. Arguments: {path?: relative path; blank means repo metadata}.",
    "github_create_issue": "Create an issue in the configured GitHub repository. Arguments: {title: string, body: string}. Always requires approval.",
    "github_write_file": "Create or update a file in the configured GitHub repository. Arguments: {path: relative path, content: string, message: commit message}. Always requires approval.",
    "app_api_request": "Call a configured app/service API at APP_API_BASE_URL. Arguments: {method: GET|POST|PATCH|DELETE, path: relative API path, query?: object, body?: object}. Path must match APP_API_ALLOWED_PATHS. GET is read-only; other methods require approval.",
    "webhook_post": "POST a JSON payload to APP_WEBHOOK_URL. Arguments: {payload: object}. Always requires approval.",
}
READ_ONLY_TOOLS = {"web_search", "workspace_list", "workspace_read", "github_read"}
WRITE_TOOLS = {"workspace_write", "workspace_delete", "github_create_issue", "github_write_file", "app_api_request", "webhook_post"}


# -----------------------------
# Database: Neon first, SQLite fallback
# -----------------------------
def _create_schema(conn: Any, backend: str) -> None:
    if backend == "postgres":
        statements = [
            "CREATE TABLE IF NOT EXISTS az_messages (id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE INDEX IF NOT EXISTS az_messages_session_idx ON az_messages(namespace, session_id, id)",
            "CREATE TABLE IF NOT EXISTS az_runs (id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT NOT NULL, mode TEXT NOT NULL, user_request TEXT NOT NULL, plan_json TEXT, result TEXT, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS az_memories (id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, label TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
            "CREATE TABLE IF NOT EXISTS az_approvals (approval_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT NOT NULL, action_json TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
        ]
    else:
        statements = [
            "CREATE TABLE IF NOT EXISTS az_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE INDEX IF NOT EXISTS az_messages_session_idx ON az_messages(namespace, session_id, id)",
            "CREATE TABLE IF NOT EXISTS az_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, session_id TEXT NOT NULL, mode TEXT NOT NULL, user_request TEXT NOT NULL, plan_json TEXT, result TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS az_memories (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, label TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS az_approvals (approval_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, session_id TEXT NOT NULL, action_json TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ]
    cur = conn.cursor()
    for statement in statements:
        cur.execute(statement)
    conn.commit()
    cur.close()


def initialize_database() -> tuple[str, str]:
    """Return (backend, notice). If Neon is misconfigured, use local SQLite and show a warning."""
    if NEON_DATABASE_URL and psycopg is not None and dict_row is not None:
        try:
            with psycopg.connect(NEON_DATABASE_URL, connect_timeout=6, row_factory=dict_row) as conn:
                _create_schema(conn, "postgres")
            return "postgres", "Connected to Neon PostgreSQL."
        except Exception as exc:
            _init_sqlite()
            return "sqlite", f"Neon connection failed, so local SQLite fallback is active: {type(exc).__name__}: {exc}"
    _init_sqlite()
    if NEON_DATABASE_URL and psycopg is None:
        return "sqlite", "Neon URL is set, but psycopg is unavailable. Local SQLite fallback is active. Check requirements.txt and redeploy."
    return "sqlite", "Local SQLite memory is active. Add NEON_DATABASE_URL to Streamlit Secrets to use Neon across app restarts."


def _init_sqlite() -> None:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_PATH, timeout=10) as conn:
        _create_schema(conn, "sqlite")


def db_connect():
    backend = st.session_state.get("db_backend", "sqlite")
    if backend == "postgres":
        return psycopg.connect(NEON_DATABASE_URL, connect_timeout=6, row_factory=dict_row)
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_execute(query: str, params: tuple | list = (), fetch: bool = False) -> list[dict[str, Any]]:
    backend = st.session_state.get("db_backend", "sqlite")
    sql = query.replace("?", "%s") if backend == "postgres" else query
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows: list[dict[str, Any]] = []
        if fetch:
            raw = cur.fetchall()
            rows = [dict(row) if not isinstance(row, dict) else row for row in raw]
        conn.commit()
        cur.close()
        return rows


def save_message(session_id: str, role: str, content: str) -> None:
    try:
        db_execute(
            "INSERT INTO az_messages(namespace, session_id, role, content) VALUES (?, ?, ?, ?)",
            (NAMESPACE, session_id, role, content),
        )
    except Exception as exc:
        st.session_state["db_write_warning"] = f"Message memory save failed: {exc}"


def load_messages(session_id: str, limit: int = 120) -> list[dict[str, str]]:
    try:
        rows = db_execute(
            "SELECT role, content FROM az_messages WHERE namespace = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
            (NAMESPACE, session_id, limit), fetch=True,
        )
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
    except Exception:
        return []


def save_run(session_id: str, mode: str, request_text: str, plan: dict[str, Any] | None, result: str, status: str) -> None:
    try:
        db_execute(
            "INSERT INTO az_runs(namespace, session_id, mode, user_request, plan_json, result, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (NAMESPACE, session_id, mode, request_text, json.dumps(plan, ensure_ascii=False) if plan else None, result, status),
        )
    except Exception as exc:
        st.session_state["db_write_warning"] = f"Run log save failed: {exc}"


def save_memory(label: str, content: str) -> None:
    existing = db_execute(
        "SELECT id FROM az_memories WHERE namespace = ? AND lower(label) = lower(?) ORDER BY id DESC LIMIT 1",
        (NAMESPACE, label.strip()), fetch=True,
    )
    if existing:
        db_execute(
            "UPDATE az_memories SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND namespace = ?",
            (content.strip(), existing[0]["id"], NAMESPACE),
        )
    else:
        db_execute("INSERT INTO az_memories(namespace, label, content) VALUES (?, ?, ?)", (NAMESPACE, label.strip(), content.strip()))


def load_memories(limit: int = 20) -> list[dict[str, Any]]:
    try:
        return db_execute(
            "SELECT id, label, content, updated_at FROM az_memories WHERE namespace = ? ORDER BY id DESC LIMIT ?",
            (NAMESPACE, limit), fetch=True,
        )
    except Exception:
        return []


def save_approval(item: dict[str, Any], status: str, result: str | None = None) -> None:
    now_status = status
    try:
        existing = db_execute("SELECT approval_id FROM az_approvals WHERE approval_id = ?", (item["id"],), fetch=True)
        if existing:
            db_execute(
                "UPDATE az_approvals SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP WHERE approval_id = ?",
                (now_status, result, item["id"]),
            )
        else:
            db_execute(
                "INSERT INTO az_approvals(approval_id, namespace, session_id, action_json, status, result) VALUES (?, ?, ?, ?, ?, ?)",
                (item["id"], NAMESPACE, item["session_id"], json.dumps(item["action"], ensure_ascii=False), now_status, result),
            )
    except Exception as exc:
        st.session_state["db_write_warning"] = f"Approval log save failed: {exc}"


def load_pending_approvals(session_id: str) -> list[dict[str, Any]]:
    try:
        rows = db_execute(
            "SELECT approval_id, action_json, status, result, created_at FROM az_approvals WHERE namespace = ? AND session_id = ? AND status = 'pending' ORDER BY created_at DESC",
            (NAMESPACE, session_id), fetch=True,
        )
        return [
            {"id": row["approval_id"], "session_id": session_id, "action": json.loads(row["action_json"]),
             "status": "pending", "result": row.get("result"), "created_at": str(row.get("created_at", ""))}
            for row in rows
        ]
    except Exception:
        return []


# -----------------------------
# OpenAI model access
# -----------------------------
def get_client():
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK missing. Deploy with the supplied requirements.txt.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing. Add your key in Streamlit → Settings → Secrets.")
    return OpenAI(api_key=OPENAI_API_KEY, timeout=75.0, max_retries=2)


def ask_model(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    use_web_search: bool = False,
    max_tokens: int = 3500,
) -> str:
    client = get_client()
    if use_web_search:
        try:
            resp = client.responses.create(
                model=MODEL_NAME,
                input=messages,
                tools=[{"type": "web_search"}],
                max_output_tokens=max_tokens,
            )
            text_out = getattr(resp, "output_text", "")
            if text_out and text_out.strip():
                return text_out.strip()
            raise RuntimeError("OpenAI web-search response did not contain output text.")
        except Exception as exc:
            raise RuntimeError(f"OpenAI hosted web search failed: {type(exc).__name__}: {exc}") from exc

    kwargs: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as first_exc:
        # Compatibility retry without JSON response_format for accounts/models where JSON mode differs.
        if json_mode:
            kwargs.pop("response_format", None)
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as retry_exc:
                raise RuntimeError(f"OpenAI request failed for model '{MODEL_NAME}': {retry_exc}") from retry_exc
        else:
            # Older SDK/model adapters may use max_tokens instead.
            kwargs.pop("max_completion_tokens", None)
            kwargs["max_tokens"] = max_tokens
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as retry_exc:
                raise RuntimeError(f"OpenAI request failed for model '{MODEL_NAME}': {retry_exc}") from first_exc
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned an empty response. Try again or verify model access.")
    return content.strip()


def parse_json_reply(text: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else (fallback or {})
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else (fallback or {})
        except Exception:
            pass
    return fallback or {}


def memory_context() -> str:
    notes = load_memories(15)
    if not notes:
        return "No saved long-term notes yet."
    return "\n".join(f"- {row['label']}: {row['content']}" for row in notes)


# -----------------------------
# Boss planner and worker runner
# -----------------------------
def create_plan(user_request: str, max_workers: int) -> dict[str, Any]:
    roster = [{"id": key, "name": value["name"], "focus": value["focus"]} for key, value in AGENTS.items()]
    tools = {key: value for key, value in TOOL_DESCRIPTIONS.items()}
    prompt = f"""You are the ALPHA ZERO Boss. Make a realistic, minimal plan for the user's request.
Return ONLY one JSON object with this schema:
{{"goal":"...","approach":"...","tasks":[{{"title":"...","agent_id":"one exact ID from roster","instruction":"specific task and expected result","tools":["zero or more exact tool IDs from tool catalog"]}}],"risks":["..."],"acceptance_checks":["..."]}}
Rules:
- Choose only relevant specialists. Assign no more than {max_workers} tasks; no need to activate every worker for every request.
- Across the entire team, the roster contains 20 specialist workers. Use exact IDs in the roster below.
- Do not claim any action has been performed. This is a plan only.
- Tool choices must come only from the tool catalog. Use [] when no tool is needed.
- For actions that send data outside the app, write instructions that propose the action, but never bypass ALPHA ZERO's approval queue.
- If a task asks to create code/files, use workspace_write and include a precise relative path and the required content in the worker's expected output.
- For web/current facts, consider web_search. For external apps, use configured APIs only.
- Do not invent available integrations. State dependencies and uncertainty.

Worker roster:
{json.dumps(roster, ensure_ascii=False)}
Tool catalog:
{json.dumps(tools, ensure_ascii=False)}
Saved memory:
{memory_context()}
User request:
{user_request}
"""
    text_out = ask_model(
        [{"role": "system", "content": "You are a careful agentic project manager. Follow JSON schema and safety boundaries exactly."},
         {"role": "user", "content": prompt}],
        json_mode=True,
        max_tokens=6000,
    )
    plan = parse_json_reply(text_out)
    if not isinstance(plan.get("tasks"), list):
        raise RuntimeError("Boss planner did not return a valid tasks list. Try a shorter or clearer request.")
    clean_tasks = []
    for item in plan["tasks"][:max_workers]:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id", "")).strip()
        if agent_id not in AGENTS:
            agent_id = "planner"
        clean_tools = [t for t in item.get("tools", []) if isinstance(t, str) and t in TOOL_DESCRIPTIONS]
        clean_tasks.append({
            "title": str(item.get("title", "Assigned task"))[:180],
            "agent_id": agent_id,
            "instruction": str(item.get("instruction", "Review the user's request and provide a focused result."))[:6000],
            "tools": clean_tools,
        })
    plan["tasks"] = clean_tasks
    plan["goal"] = str(plan.get("goal", user_request))[:600]
    plan["approach"] = str(plan.get("approach", "Delegate focused subtasks and combine verified results."))[:2000]
    plan["risks"] = plan.get("risks", []) if isinstance(plan.get("risks", []), list) else []
    plan["acceptance_checks"] = plan.get("acceptance_checks", []) if isinstance(plan.get("acceptance_checks", []), list) else []
    return plan


def run_worker(agent_id: str, task: dict[str, Any], user_request: str) -> dict[str, Any]:
    agent = AGENTS[agent_id]
    allowed_tools = task["tools"]
    available = {key: TOOL_DESCRIPTIONS[key] for key in allowed_tools}
    prompt = f"""You are specialist worker '{agent['name']}' in ALPHA ZERO.
Your specialty: {agent['focus']}
User's overall request: {user_request}
Your assigned task: {task['instruction']}
Task title: {task['title']}
Allowed tools for this task:
{json.dumps(available, ensure_ascii=False)}
Saved long-term memory:
{memory_context()}

Return ONLY JSON with exact shape:
{{"report":"What you found or completed; be honest about limits.","actions":[{{"tool":"exact allowed tool id","arguments":{{}}}}],"warnings":["..."]}}
Rules:
- Only propose actions whose tool is explicitly listed above. Do not create tool IDs.
- Do not say an action was completed unless the system's execution result confirms it. Put operations you want performed in actions.
- Actions must have enough real arguments to execute. Never invent API keys, owners, repositories, URLs, credentials, or result data.
- Use workspace paths relative to the ALPHA ZERO workspace. Never use absolute paths or '..'.
- workspace_write should create a useful, complete text/code artifact, not a placeholder, when that is the task.
- For GitHub/app API writes, propose the exact effect. The app will pause for approval before external writes.
- Keep report practical and concise. If no tool is needed, return actions: [].
"""
    raw = ask_model(
        [{"role": "system", "content": "You are a precise specialist worker. Output valid JSON and never fabricate tool results."},
         {"role": "user", "content": prompt}],
        json_mode=True,
        max_tokens=5000,
        # Keep the worker response structured JSON. It can propose a web_search
        # action, which is executed separately by the validated tool router.
        use_web_search=False,
    )
    parsed = parse_json_reply(raw)
    if not parsed:
        return {"agent_id": agent_id, "name": agent["name"], "report": raw, "actions": [], "warnings": ["Worker response was not structured JSON; actions were not executed."]}
    actions = parsed.get("actions", [])
    if not isinstance(actions, list):
        actions = []
    verified_actions = []
    for action in actions[:8]:
        if not isinstance(action, dict):
            continue
        tool = action.get("tool")
        args = action.get("arguments", {})
        if tool in allowed_tools and tool in TOOL_DESCRIPTIONS and isinstance(args, dict):
            verified_actions.append({"tool": tool, "arguments": args})
    warnings = parsed.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    return {
        "agent_id": agent_id,
        "name": agent["name"],
        "report": str(parsed.get("report", "No report returned."))[:14000],
        "actions": verified_actions,
        "warnings": [str(w)[:500] for w in warnings[:6]],
    }


# -----------------------------
# Action execution and approval gate
# -----------------------------
def safe_workspace_path(relative_path: str) -> Path:
    rel = str(relative_path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise ValueError("Provide a non-empty relative workspace path.")
    parts = PurePosixPath(rel).parts
    if any(part in ("..", "") for part in parts):
        raise ValueError("Workspace paths cannot contain '..' or empty path segments.")
    target = (WORKSPACE_DIR / Path(*parts)).resolve()
    if target != WORKSPACE_DIR and WORKSPACE_DIR not in target.parents:
        raise ValueError("Workspace path escaped the allowed workspace directory.")
    return target


def request_json(method: str, url: str, *, headers: dict[str, str] | None = None, params: Any = None, body: Any = None, timeout: int = 25) -> Any:
    response = requests.request(method, url, headers=headers or {}, params=params, json=body, timeout=timeout)
    if response.status_code >= 400:
        detail = response.text[:1800]
        raise RuntimeError(f"HTTP {response.status_code} from configured service: {detail}")
    if not response.content:
        return {"status_code": response.status_code, "message": "Request succeeded with empty response."}
    try:
        return response.json()
    except Exception:
        return {"status_code": response.status_code, "text": response.text[:MAX_READ_TEXT]}


def github_headers() -> dict[str, str]:
    if not (GITHUB_OWNER and GITHUB_REPO and GITHUB_TOKEN):
        raise RuntimeError("GitHub is not configured. Add GITHUB_OWNER, GITHUB_REPO, and GITHUB_TOKEN in Streamlit Secrets.")
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def github_read(args: dict[str, Any]) -> str:
    headers = github_headers()
    path = str(args.get("path", "")).strip().strip("/")
    if not path:
        payload = request_json("GET", f"{GITHUB_API}/repos/{quote(GITHUB_OWNER)}/{quote(GITHUB_REPO)}", headers=headers)
        return json.dumps({k: payload.get(k) for k in ["full_name", "description", "private", "default_branch", "html_url", "open_issues_count"]}, ensure_ascii=False, indent=2)
    url = f"{GITHUB_API}/repos/{quote(GITHUB_OWNER)}/{quote(GITHUB_REPO)}/contents/{quote(path, safe='/')}"
    payload = request_json("GET", url, headers=headers)
    if isinstance(payload, list):
        return json.dumps([{"name": x.get("name"), "type": x.get("type"), "path": x.get("path"), "size": x.get("size")} for x in payload[:150]], ensure_ascii=False, indent=2)
    if isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("content"):
        try:
            text_content = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
            return f"Path: {payload.get('path')}\nSHA: {payload.get('sha')}\n\n{text_content[:MAX_READ_TEXT]}"
        except Exception:
            pass
    return json.dumps(payload, ensure_ascii=False, indent=2)[:MAX_READ_TEXT]


def perform_action(action: dict[str, Any], *, approved: bool = False) -> tuple[str, str]:
    """Return (state, message). state is done, pending, or failed."""
    tool = action.get("tool")
    args = action.get("arguments", {})
    if tool not in TOOL_DESCRIPTIONS or not isinstance(args, dict):
        return "failed", "Rejected unknown tool or malformed arguments."

    # External and destructive effects always pause until a direct user click approves them.
    sensitive = tool in {"workspace_delete", "github_create_issue", "github_write_file", "webhook_post"}
    if tool == "app_api_request" and str(args.get("method", "GET")).upper() not in {"GET", "HEAD"}:
        sensitive = True
    if tool == "workspace_write":
        try:
            p = safe_workspace_path(str(args.get("path", "")))
            if p.exists():
                sensitive = True
        except Exception as exc:
            return "failed", f"Workspace path rejected: {exc}"
    if sensitive and not approved:
        return "pending", "This action needs your approval before it can run."

    try:
        if tool == "web_search":
            query = str(args.get("query", "")).strip()
            if not query:
                raise ValueError("web_search requires a query.")
            result = ask_model(
                [{"role": "system", "content": "Search the web, synthesize the useful findings, and cite the sources/URLs provided by the search tool. Clearly label uncertainty."},
                 {"role": "user", "content": query}],
                use_web_search=True,
                max_tokens=2400,
            )
            return "done", result

        if tool == "workspace_list":
            rel = str(args.get("path", "")).strip()
            folder = WORKSPACE_DIR if not rel else safe_workspace_path(rel)
            if not folder.exists() or not folder.is_dir():
                raise FileNotFoundError(f"Workspace directory not found: {rel or '.'}")
            files = []
            for p in sorted(folder.iterdir(), key=lambda item: item.name.lower())[:200]:
                files.append({"name": p.name, "type": "directory" if p.is_dir() else "file", "size": p.stat().st_size if p.is_file() else None})
            return "done", json.dumps(files, ensure_ascii=False, indent=2)

        if tool == "workspace_read":
            p = safe_workspace_path(str(args.get("path", "")))
            if not p.is_file():
                raise FileNotFoundError(f"Workspace file not found: {args.get('path')}")
            data = p.read_text(encoding="utf-8", errors="replace")
            return "done", f"Read {p.relative_to(WORKSPACE_DIR)} ({len(data)} chars).\n\n{data[:MAX_READ_TEXT]}"

        if tool == "workspace_write":
            p = safe_workspace_path(str(args.get("path", "")))
            content = str(args.get("content", ""))
            if not content.strip():
                raise ValueError("Refusing to create an empty file.")
            if len(content) > MAX_ACTION_TEXT:
                raise ValueError(f"File is too large. Maximum is {MAX_ACTION_TEXT} characters.")
            if p.exists() and not approved:
                return "pending", "The file already exists. Approval is required before overwriting it."
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return "done", f"Created/updated workspace file: {p.relative_to(WORKSPACE_DIR)} ({len(content)} characters)."

        if tool == "workspace_delete":
            p = safe_workspace_path(str(args.get("path", "")))
            if not p.is_file():
                raise FileNotFoundError(f"Workspace file not found: {args.get('path')}")
            p.unlink()
            return "done", f"Deleted workspace file {p.relative_to(WORKSPACE_DIR)}."

        if tool == "github_read":
            return "done", github_read(args)

        if tool == "github_create_issue":
            headers = github_headers()
            title = str(args.get("title", "")).strip()
            body = str(args.get("body", "")).strip()
            if not title:
                raise ValueError("GitHub issue needs a title.")
            url = f"{GITHUB_API}/repos/{quote(GITHUB_OWNER)}/{quote(GITHUB_REPO)}/issues"
            result = request_json("POST", url, headers=headers, body={"title": title[:240], "body": body[:60000]})
            return "done", f"GitHub issue created: {result.get('html_url', '(URL unavailable)')}"

        if tool == "github_write_file":
            headers = github_headers()
            path = str(args.get("path", "")).strip().strip("/")
            content = str(args.get("content", ""))
            message = str(args.get("message", "ALPHA ZERO update")).strip()[:200]
            if not path or ".." in PurePosixPath(path).parts:
                raise ValueError("Provide a safe repository-relative path.")
            if len(content) > MAX_ACTION_TEXT:
                raise ValueError("GitHub file content exceeds the allowed size limit.")
            api_url = f"{GITHUB_API}/repos/{quote(GITHUB_OWNER)}/{quote(GITHUB_REPO)}/contents/{quote(path, safe='/')}"
            current = requests.get(api_url, headers=headers, timeout=20)
            payload: dict[str, Any] = {"message": message or "ALPHA ZERO update", "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
            if current.status_code == 200:
                current_json = current.json()
                payload["sha"] = current_json.get("sha")
            elif current.status_code != 404:
                raise RuntimeError(f"GitHub read before write failed: HTTP {current.status_code}: {current.text[:1000]}")
            result = request_json("PUT", api_url, headers=headers, body=payload)
            html_url = (result.get("content") or {}).get("html_url", "(URL unavailable)")
            return "done", f"GitHub file write committed: {html_url}"

        if tool == "app_api_request":
            if not APP_API_BASE_URL:
                raise RuntimeError("APP_API_BASE_URL is not configured in Streamlit Secrets.")
            parsed = urlparse(APP_API_BASE_URL)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Configured app API must use an HTTPS base URL.")
            method = str(args.get("method", "GET")).upper()
            if method not in {"GET", "POST", "PATCH", "DELETE", "HEAD"}:
                raise ValueError("API method not allowed.")
            path = str(args.get("path", "")).strip()
            if not path.startswith("/") or path.startswith("//") or ".." in PurePosixPath(path).parts:
                raise ValueError("API path must be a safe relative path starting with one slash.")
            if not APP_API_ALLOWED_PATHS:
                raise RuntimeError("APP_API_ALLOWED_PATHS is empty. Explicitly allow the endpoint prefixes in Secrets first.")
            if not any(path == prefix or path.startswith(prefix + "/") for prefix in APP_API_ALLOWED_PATHS):
                raise PermissionError("This API path is not in APP_API_ALLOWED_PATHS.")
            headers = {"Accept": "application/json"}
            if APP_API_BEARER_TOKEN:
                headers["Authorization"] = f"Bearer {APP_API_BEARER_TOKEN}"
            result = request_json(method, APP_API_BASE_URL + path, headers=headers, params=args.get("query"), body=args.get("body"))
            return "done", json.dumps(result, ensure_ascii=False, indent=2)[:MAX_READ_TEXT]

        if tool == "webhook_post":
            if not APP_WEBHOOK_URL:
                raise RuntimeError("APP_WEBHOOK_URL is not configured in Streamlit Secrets.")
            parsed = urlparse(APP_WEBHOOK_URL)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Webhook URL must use HTTPS.")
            payload = args.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("Webhook payload must be a JSON object.")
            result = request_json("POST", APP_WEBHOOK_URL, headers={"Content-Type": "application/json"}, body=payload)
            return "done", json.dumps(result, ensure_ascii=False, indent=2)[:MAX_READ_TEXT]

        return "failed", "No executor exists for this tool."
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {exc}"


def queue_approval(session_id: str, action: dict[str, Any], agent_name: str, task_title: str) -> dict[str, Any]:
    item = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "action": action,
        "agent_name": agent_name,
        "task_title": task_title,
        "status": "pending",
        "result": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    st.session_state.setdefault("approval_items", []).insert(0, item)
    save_approval(item, "pending")
    return item


def execute_worker_actions(session_id: str, worker: dict[str, Any], task: dict[str, Any]) -> list[dict[str, str]]:
    outcomes = []
    for action in worker.get("actions", []):
        state, result = perform_action(action, approved=False)
        if state == "pending":
            item = queue_approval(session_id, action, worker["name"], task["title"])
            outcomes.append({"tool": action["tool"], "state": "pending", "result": f"Queued approval {item['id'][:8]}: {result}"})
        else:
            outcomes.append({"tool": action["tool"], "state": state, "result": result})
    return outcomes


def synthesize_report(user_request: str, plan: dict[str, Any], worker_results: list[dict[str, Any]]) -> str:
    compact = []
    for item in worker_results:
        compact.append({
            "worker": item["name"], "task": item.get("task_title", ""), "report": item.get("report", ""),
            "action_results": item.get("action_results", []), "warnings": item.get("warnings", []),
        })
    prompt = f"""Write ALPHA ZERO's final report for the user.
User request: {user_request}
Plan goal: {plan.get('goal', '')}
Worker results and actual tool execution states:
{json.dumps(compact, ensure_ascii=False)[:26000]}
Rules:
- Distinguish completed work from pending approvals and failures.
- Never claim pending or failed actions succeeded.
- State created workspace file paths and GitHub/API outcomes explicitly.
- Give a brief overall result, completed work, approvals waiting, blockers, and next best step.
- Do not list worker names unnecessarily; make the report clear and direct.
"""
    return ask_model(
        [{"role": "system", "content": "You are ALPHA ZERO's accurate final reporter. Do not invent outcomes."},
         {"role": "user", "content": prompt}],
        max_tokens=3000,
    )


def format_plan(plan: dict[str, Any]) -> str:
    lines = [f"## Plan: {plan.get('goal', 'Your request')}", "", str(plan.get("approach", "")), "", "### Tasks"]
    if not plan.get("tasks"):
        lines.append("No delegated tasks were returned. Make the request more specific and try again.")
    for i, task in enumerate(plan.get("tasks", []), 1):
        agent = AGENTS.get(task["agent_id"], AGENTS["planner"])
        tool_names = ", ".join(task.get("tools", [])) or "No tools"
        lines.extend([f"{i}. **{task['title']}** · {agent['icon']} {agent['name']}", f"   - Work: {task['instruction']}", f"   - Tools: {tool_names}"])
    risks = plan.get("risks", [])
    checks = plan.get("acceptance_checks", [])
    if risks:
        lines.extend(["", "### Risks / dependencies"] + [f"- {str(x)}" for x in risks[:10]])
    if checks:
        lines.extend(["", "### Completion checks"] + [f"- {str(x)}" for x in checks[:10]])
    lines.extend(["", "**Plan mode did not execute tasks or call external write actions.**"])
    return "\n".join(lines)


def detect_mood(text: str) -> str:
    value = text.lower()
    if any(word in value for word in ["sorry", "apolog", "difficult", "sad", "upset", "worry", "careful"]):
        return "caring"
    if any(word in value for word in ["error", "failed", "warning", "risk", "security", "blocked"]):
        return "thinking"
    if any(word in value for word in ["great", "done", "success", "complete", "created", "ready", "awesome"]):
        return "happy"
    if "?" in value or any(word in value for word in ["question", "curious", "wonder"]):
        return "curious"
    if any(word in value for word in ["surpris", "wow", "unexpected"]):
        return "surprised"
    if any(word in value for word in ["plan", "step", "analysis", "thinking"]):
        return "thinking"
    return "neutral"


def process_request(prompt: str, mode: str, max_workers: int) -> str:
    session_id = st.session_state.session_id
    st.session_state.history.append({"role": "user", "content": prompt})
    save_message(session_id, "user", prompt)
    try:
        if mode == "Interactive":
            history = st.session_state.history[-14:]
            memory = memory_context()
            system = (
                "You are ALPHA ZERO, a helpful AI assistant. Your default API model is GPT-6 Luna. "
                "In Interactive mode, chat only: do not delegate workers, execute tools, or claim external actions. "
                "Be clear, direct, warm, and honest. Use the saved memory below only when relevant.\n\nSaved memory:\n" + memory
            )
            messages = [{"role": "system", "content": system}] + history
            answer = ask_model(messages, max_tokens=3500)
            save_run(session_id, mode, prompt, None, answer, "completed")
            return answer

        with st.spinner("Boss is preparing your plan…"):
            plan = create_plan(prompt, max_workers)
        st.session_state.last_plan = plan
        if mode == "Plan":
            answer = format_plan(plan)
            save_run(session_id, mode, prompt, plan, answer, "planned_only")
            return answer

        worker_results: list[dict[str, Any]] = []
        tasks = plan.get("tasks", [])
        if not tasks:
            answer = "The Boss could not make any delegated tasks. Please make the request more specific and run Executive again."
            save_run(session_id, mode, prompt, plan, answer, "no_tasks")
            return answer
        progress = st.progress(0, text="Executive mode: preparing workers…")
        for index, task in enumerate(tasks):
            agent_id = task["agent_id"]
            agent = AGENTS[agent_id]
            progress.progress(int(index / max(len(tasks), 1) * 100), text=f"{agent['icon']} {agent['name']} is working…")
            try:
                worker = run_worker(agent_id, task, prompt)
                outcomes = execute_worker_actions(session_id, worker, task)
                worker["action_results"] = outcomes
                worker["task_title"] = task["title"]
                worker_results.append(worker)
            except Exception as exc:
                worker_results.append({
                    "agent_id": agent_id, "name": agent["name"], "task_title": task["title"],
                    "report": f"Worker failed: {type(exc).__name__}: {exc}", "actions": [], "action_results": [], "warnings": ["Task did not complete."],
                })
        progress.progress(100, text="Combining worker reports…")
        try:
            answer = synthesize_report(prompt, plan, worker_results)
        except Exception as exc:
            lines = ["## Executive report", "", f"**Goal:** {plan.get('goal', prompt)}", "", "### Worker reports"]
            for row in worker_results:
                lines.extend([f"**{row['name']} · {row.get('task_title', '')}**", row.get("report", "No report."), ""])
                for outcome in row.get("action_results", []):
                    lines.append(f"- `{outcome['tool']}` · {outcome['state']}: {outcome['result'][:900]}")
            lines.append(f"\nFinal synthesis model unavailable: {exc}")
            answer = "\n".join(lines)
        pending_count = sum(1 for a in st.session_state.get("approval_items", []) if a.get("status") == "pending")
        if pending_count:
            answer += f"\n\n### 🔐 Approvals needed\n**{pending_count}** action(s) are waiting in the Approvals panel below. They have not run yet."
        st.session_state.last_worker_results = worker_results
        save_run(session_id, mode, prompt, plan, answer, "completed_with_pending_approvals" if pending_count else "completed")
        return answer
    except Exception as exc:
        answer = f"I couldn't complete that request: **{type(exc).__name__}: {exc}**\n\nCheck `OPENAI_API_KEY`, `OPENAI_MODEL`, model access/billing, then try again. No unapproved external write action was run."
        save_run(session_id, mode, prompt, st.session_state.get("last_plan"), answer, "failed")
        return answer


# -----------------------------
# Chibi avatar (HTML/CSS/JS embedded in app.py)
# -----------------------------
def avatar_html(mood: str, speech_text: str) -> str:
    mood = mood if mood in {"neutral", "happy", "caring", "curious", "surprised", "confused", "sad", "thinking"} else "neutral"
    speech_js = json.dumps(speech_text[:7000], ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    *{{box-sizing:border-box}} body{{margin:0;font-family:Inter,system-ui,sans-serif;background:transparent;color:#27324b}}
    .stage{{height:326px;width:100%;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;border-radius:26px;background:radial-gradient(circle at 50% 30%,#fff 0,#f1fbff 43%,#fff3fb 100%);border:1px solid #e2eef7}}
    .halo{{position:absolute;width:224px;height:224px;border-radius:50%;background:linear-gradient(135deg,#dff7ff,#f7e6ff,#fff0dd);filter:blur(1px);animation:halo 5s ease-in-out infinite}}
    .avatar{{width:218px;height:252px;z-index:2;transform-origin:center bottom;animation:floaty 4.2s ease-in-out infinite;filter:drop-shadow(0 9px 8px #5b76a51b)}}
    .spark{{position:absolute;z-index:3;font-size:20px;animation:twinkle 2.4s ease-in-out infinite}} .s1{{top:43px;left:calc(50% - 120px)}} .s2{{top:83px;left:calc(50% + 99px);animation-delay:.6s;font-size:17px}} .s3{{bottom:76px;left:calc(50% - 132px);animation-delay:1.1s;font-size:16px}}
    .face-label{{position:absolute;bottom:15px;left:0;right:0;text-align:center;font-weight:750;font-size:13px;letter-spacing:.2px;color:#6b6c9c}}
    .eye-pair{{transform-box:fill-box;transform-origin:center;animation:blink 5.2s infinite}} .eye-white{{fill:#fff}} .iris{{fill:#7554c6}} .pupil{{fill:#202b4c}} .shine{{fill:white}}
    .lid{{fill:#ffdecf;opacity:0}} .brow{{fill:none;stroke:#563953;stroke-width:4;stroke-linecap:round}}
    .mouth{{fill:none;stroke:#8f4b66;stroke-width:4;stroke-linecap:round;opacity:0}} .mouth-open{{fill:#9c4567;stroke:#8f4b66;stroke-width:3;opacity:0}}
    .blush{{fill:#f7a5af;opacity:.28}} .tear{{fill:#94d9ff;opacity:0}} .thinking-hand{{opacity:0}}
    .mood-happy .mouth-happy,.mood-caring .mouth-smile,.mood-curious .mouth-o,.mood-surprised .mouth-o,.mood-confused .mouth-flat,.mood-sad .mouth-sad,.mood-thinking .mouth-flat,.mood-neutral .mouth-flat{{opacity:1}}
    .mood-happy .blush,.mood-caring .blush{{opacity:.7}} .mood-surprised .mouth-o,.mood-curious .mouth-o{{opacity:1}}
    .mood-sad .brow-left,.mood-sad .brow-right{{stroke:#6b5682}} .mood-thinking .brow-left{{transform:rotate(-10deg);transform-origin:144px 118px}} .mood-thinking .brow-right{{transform:rotate(10deg);transform-origin:196px 118px}}
    .mood-surprised .eye-pair,.mood-curious .eye-pair{{animation:none;transform:scale(1.12)}} .mood-sad .tear{{opacity:1}} .mood-thinking .thinking-hand{{opacity:1}}
    .speaking .mouth{{opacity:0}} .speaking .mouth-open{{opacity:1;animation:talk .22s ease-in-out infinite alternate}} .speaking .eye-pair{{animation:blink 3.1s infinite}}
    @keyframes floaty{{0%,100%{{transform:translateY(0) rotate(-1deg)}}50%{{transform:translateY(-7px) rotate(1deg)}}}}
    @keyframes halo{{0%,100%{{transform:scale(.97);opacity:.78}}50%{{transform:scale(1.04);opacity:1}}}}
    @keyframes twinkle{{0%,100%{{transform:scale(.8) rotate(-8deg);opacity:.5}}50%{{transform:scale(1.15) rotate(12deg);opacity:1}}}}
    @keyframes blink{{0%,43%,47%,100%{{transform:scaleY(1)}}45%,46%{{transform:scaleY(.08)}}}}
    @keyframes talk{{from{{transform:scaleY(.48)}}to{{transform:scaleY(1.1)}}}}
    .controls{{position:absolute;right:12px;top:12px;display:flex;gap:6px;z-index:5}} .btn{{border:1px solid #d9e3f3;background:#ffffffdd;color:#37456a;padding:7px 10px;border-radius:12px;font-weight:700;font-size:12px;cursor:pointer}} .btn:hover{{background:#eef8ff}}
    .caption{{position:absolute;top:13px;left:14px;font-size:11px;font-weight:800;letter-spacing:1px;color:#8588b0}}
    @media(max-width:480px){{.stage{{height:300px}}.avatar{{width:195px;height:235px}}.halo{{width:202px;height:202px}}}}
    </style></head><body><div id="stage" class="stage mood-{mood}">
      <div class="caption">ALPHA ZERO · LIVE AVATAR</div>
      <div class="controls"><button class="btn" id="speakBtn">▶ Speak</button><button class="btn" id="stopBtn">■ Stop</button></div>
      <div class="halo"></div><span class="spark s1">✨</span><span class="spark s2">✦</span><span class="spark s3">✧</span>
      <svg class="avatar" viewBox="0 0 340 380" role="img" aria-label="Animated chibi anime assistant">
        <defs><linearGradient id="hair" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#7d79e8"/><stop offset=".55" stop-color="#a88df4"/><stop offset="1" stop-color="#f4a9ce"/></linearGradient><linearGradient id="coat" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#7dcdf4"/><stop offset="1" stop-color="#a9a1ff"/></linearGradient></defs>
        <ellipse cx="170" cy="348" rx="81" ry="15" fill="#748bb7" opacity=".14"/>
        <path d="M96 292 Q83 318 86 348 Q91 361 121 357 L145 351 L195 351 L221 357 Q251 361 255 348 Q258 319 244 292Z" fill="url(#coat)" stroke="#7a75be" stroke-width="3"/>
        <path d="M143 294 L170 328 L197 294" fill="#fff" opacity=".9"/><path d="M162 326 L170 340 L178 326" fill="#ffcae5"/>
        <path d="M112 87 Q82 44 118 32 Q155 4 203 28 Q257 27 261 78 L251 169 L91 173Z" fill="url(#hair)" stroke="#5a4c99" stroke-width="4"/>
        <path d="M101 140 Q73 133 82 158 Q85 176 105 173" fill="#ffdecf" stroke="#8b6688" stroke-width="3"/><path d="M239 140 Q266 132 258 158 Q254 176 235 173" fill="#ffdecf" stroke="#8b6688" stroke-width="3"/>
        <path d="M103 112 Q95 74 133 63 Q169 44 211 62 Q243 74 239 119 L231 223 Q222 269 170 277 Q116 269 108 223Z" fill="#ffdecf" stroke="#936a91" stroke-width="4"/>
        <path d="M98 122 Q83 86 112 62 Q126 47 145 43 Q121 74 154 84 Q181 92 207 67 Q225 80 243 61 Q261 94 242 132 L231 110 Q220 135 203 128 Q181 123 169 105 Q151 127 125 133 L112 113Z" fill="url(#hair)" stroke="#5a4c99" stroke-width="4" stroke-linejoin="round"/>
        <path d="M128 91 Q143 106 143 121" fill="none" stroke="#d9c5ff" stroke-width="6" stroke-linecap="round"/><path d="M210 86 Q225 99 226 115" fill="none" stroke="#ffd8ef" stroke-width="5" stroke-linecap="round"/>
        <g class="brows"><path class="brow brow-left" d="M125 137 Q141 128 153 137"/><path class="brow brow-right" d="M190 137 Q205 128 219 138"/></g>
        <g class="eye-pair"><g class="eye"><ellipse class="eye-white" cx="140" cy="161" rx="19" ry="23"/><ellipse class="iris" cx="141" cy="164" rx="13" ry="17"/><ellipse class="pupil" cx="143" cy="166" rx="7" ry="11"/><circle class="shine" cx="135" cy="155" r="5"/><circle class="shine" cx="148" cy="173" r="2.7"/><path class="lid" d="M120 157 Q140 132 159 157 L159 143 L120 143Z"/></g><g class="eye"><ellipse class="eye-white" cx="202" cy="161" rx="19" ry="23"/><ellipse class="iris" cx="201" cy="164" rx="13" ry="17"/><ellipse class="pupil" cx="199" cy="166" rx="7" ry="11"/><circle class="shine" cx="196" cy="155" r="5"/><circle class="shine" cx="208" cy="173" r="2.7"/><path class="lid" d="M183 157 Q202 132 221 157 L221 143 L183 143Z"/></g></g>
        <ellipse class="blush" cx="117" cy="195" rx="17" ry="8"/><ellipse class="blush" cx="226" cy="195" rx="17" ry="8"/>
        <path d="M168 172 Q164 182 170 184" stroke="#d392a1" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path class="mouth mouth-flat" d="M155 209 Q170 213 185 209"/><path class="mouth mouth-happy" d="M150 204 Q170 230 190 204"/><path class="mouth mouth-smile" d="M154 207 Q170 222 186 207"/><ellipse class="mouth mouth-o" cx="170" cy="211" rx="8" ry="12"/><path class="mouth mouth-sad" d="M151 219 Q170 200 189 219"/><ellipse class="mouth-open" cx="170" cy="213" rx="12" ry="9"/>
        <path class="tear" d="M222 185 Q233 199 222 208 Q212 200 222 185Z"/><g class="thinking-hand"><path d="M225 250 Q243 225 230 209 Q218 202 214 215 L218 239" fill="#ffdecf" stroke="#936a91" stroke-width="3" stroke-linecap="round"/></g>
        <path d="M111 306 Q129 283 145 288 M229 306 Q211 283 195 288" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" opacity=".85"/>
        <circle cx="170" cy="298" r="7" fill="#fff" stroke="#ada6f2" stroke-width="3"/>
      </svg>
      <div class="face-label" id="moodLabel">{html.escape(mood.title())} mode · blink + expressions</div>
    </div>
    <script>
      const speechText = {speech_js};
      const stage = document.getElementById('stage');
      const speakBtn = document.getElementById('speakBtn');
      const stopBtn = document.getElementById('stopBtn');
      function startSpeaking() {{
        if (!speechText || !('speechSynthesis' in window)) {{ document.getElementById('moodLabel').textContent = 'Speech unavailable in this browser'; return; }}
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(speechText);
        utterance.rate = 1.0; utterance.pitch = 1.08;
        const voices = window.speechSynthesis.getVoices();
        const preferred = voices.find(v => /^en[-_]/i.test(v.lang) && /female|natural|aria|jenny|samantha/i.test(v.name)) || voices.find(v => /^en[-_]/i.test(v.lang));
        if (preferred) utterance.voice = preferred;
        stage.classList.add('speaking'); document.getElementById('moodLabel').textContent = 'Speaking · mouth animation synced to speech';
        utterance.onend = () => {{ stage.classList.remove('speaking'); document.getElementById('moodLabel').textContent = '{html.escape(mood.title())} mode · blink + expressions'; }};
        utterance.onerror = () => {{ stage.classList.remove('speaking'); }};
        window.speechSynthesis.speak(utterance);
      }}
      speakBtn.addEventListener('click', startSpeaking);
      stopBtn.addEventListener('click', () => {{ if ('speechSynthesis' in window) window.speechSynthesis.cancel(); stage.classList.remove('speaking'); document.getElementById('moodLabel').textContent = 'Speech stopped'; }});
      window.speechSynthesis?.getVoices();
    </script></body></html>'''


# -----------------------------
# UI styling
# -----------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Manrope:wght@500;600;700;800&display=swap');
:root { color-scheme: light; }
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background: linear-gradient(145deg,#fbfdff 0%,#f7fbff 47%,#fff8fc 100%); color:#27324b; }
header[data-testid="stHeader"] { background:transparent; }
.block-container { padding-top:1.2rem; padding-bottom:2.5rem; max-width:1500px; }
[data-testid="stSidebar"] { background:linear-gradient(180deg,#f4faff 0%,#fff8fc 100%); border-right:1px solid #e4edf7; }
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3 { color:#37446b; }
.az-brand { font-family:Manrope,sans-serif; font-size:2.2rem; font-weight:800; line-height:1.06; letter-spacing:-1px; background:linear-gradient(95deg,#6275e9,#9c72da,#e58fb7); -webkit-background-clip:text; color:transparent; }
.az-subtitle { color:#7886a7; font-size:.94rem; margin-top:.35rem; }
.az-pill { display:inline-block; border:1px solid #dce7f7; background:#ffffffc9; border-radius:999px; padding:.32rem .72rem; color:#6574a0; font-size:.76rem; font-weight:800; letter-spacing:.3px; }
div[data-testid="stMetric"] { border:1px solid #e1eaf7; background:#ffffffcc; border-radius:18px; padding:14px 16px; box-shadow:0 6px 22px #91a4c50c; }
.stButton button, .stDownloadButton button { border-radius:13px; font-weight:750; border:1px solid #dce6f5; transition:all .15s ease; }
.stButton button[kind="primary"] { background:linear-gradient(100deg,#6479ed,#9974dc); border:0; color:white; }
.stButton button:hover, .stDownloadButton button:hover { border-color:#9e9ded; transform:translateY(-1px); }
[data-testid="stChatMessage"] { background:#ffffffcf; border:1px solid #e6edf7; border-radius:18px; padding:.4rem .8rem; box-shadow:0 4px 18px #9aabc20b; }
[data-testid="stChatInput"] { border-radius:18px; }
[data-testid="stExpander"] { border:1px solid #e4eaf6; border-radius:16px; background:#ffffffb8; }
.stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] { border-radius:12px; }
.az-note { font-size:.82rem; color:#73809e; line-height:1.5; }
.az-footer { text-align:center; color:#97a0b5; font-size:.77rem; padding:1.2rem; }
</style>
""", unsafe_allow_html=True)


# -----------------------------
# Session setup and database init
# -----------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []
if "history_loaded" not in st.session_state:
    st.session_state.history = load_messages(st.session_state.session_id)
    st.session_state.history_loaded = True
if "approval_items" not in st.session_state:
    st.session_state.approval_items = load_pending_approvals(st.session_state.session_id)
if "avatar_mood" not in st.session_state:
    st.session_state.avatar_mood = "neutral"
if "last_plan" not in st.session_state:
    st.session_state.last_plan = None
if "last_worker_results" not in st.session_state:
    st.session_state.last_worker_results = []
if "pending_voice_prompt" not in st.session_state:
    st.session_state.pending_voice_prompt = ""

if "db_backend" not in st.session_state:
    backend, db_notice = initialize_database()
    st.session_state.db_backend = backend
    st.session_state.db_notice = db_notice
    # Reload only after database is ready, so Neon memory can be used immediately.
    if not st.session_state.history:
        st.session_state.history = load_messages(st.session_state.session_id)
else:
    db_notice = st.session_state.get("db_notice", "")


# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.markdown('<div class="az-brand" style="font-size:1.8rem">✨ ALPHA ZERO</div><div class="az-subtitle">Boss + 20 specialist agents</div>', unsafe_allow_html=True)
    st.write("")
    mode = st.radio("Boss mode", ["Interactive", "Plan", "Executive"], index=0, help="Interactive = chat only. Plan = plan only. Executive = assign workers and execute supported tools.")
    max_workers = st.slider("Maximum workers per Executive run", min_value=1, max_value=20, value=8, help="The Boss chooses the relevant agents, up to this limit.")
    st.divider()
    st.markdown("### 🧠 Memory & status")
    db_label = "Neon PostgreSQL" if st.session_state.db_backend == "postgres" else "Local SQLite fallback"
    st.caption(f"Memory backend: **{db_label}**")
    with st.expander("Connection details", expanded=False):
        st.write(db_notice)
        st.caption(f"Model: `{MODEL_NAME}`")
        st.caption("OpenAI key: configured" if OPENAI_API_KEY else "OpenAI key: not configured")
        st.caption("GitHub connector: configured" if (GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO) else "GitHub connector: not configured")
        st.caption("App API connector: configured" if APP_API_BASE_URL else "App API connector: not configured")
    if "db_write_warning" in st.session_state:
        st.warning(st.session_state["db_write_warning"])

    with st.expander("Save long-term memory", expanded=False):
        with st.form("save_memory_form", clear_on_submit=True):
            memory_label = st.text_input("Memory label", placeholder="Project preferences")
            memory_content = st.text_area("What should Boss remember?", placeholder="Keep the UI bright. Use Neon for memory.", height=90)
            memory_submitted = st.form_submit_button("Save memory", type="primary", use_container_width=True)
        if memory_submitted:
            if not memory_label.strip() or not memory_content.strip():
                st.warning("Enter both a label and content.")
            else:
                try:
                    save_memory(memory_label, memory_content)
                    st.success("Memory saved.")
                except Exception as exc:
                    st.error(f"Memory save failed: {exc}")
        saved = load_memories(10)
        if saved:
            st.caption("Saved notes")
            for note in saved:
                with st.expander(f"📝 {note['label']}"):
                    st.write(note["content"])
                    if st.button("Delete this note", key=f"del_mem_{note['id']}"):
                        db_execute("DELETE FROM az_memories WHERE id = ? AND namespace = ?", (note["id"], NAMESPACE))
                        st.rerun()

    with st.expander("🎙️ Voice input", expanded=False):
        st.caption("Record a prompt, transcribe it, then review the text before sending it to Boss.")
        voice_audio = st.audio_input("Record your prompt")
        if st.button("Transcribe recording", use_container_width=True, disabled=voice_audio is None):
            try:
                client = get_client()
                transcription = client.audio.transcriptions.create(
                    model=TRANSCRIPTION_MODEL,
                    file=("alpha_zero_voice.wav", voice_audio.getvalue(), "audio/wav"),
                )
                st.session_state.pending_voice_prompt = str(transcription.text).strip()
                st.success("Transcript ready. Review it in the main panel.")
                st.rerun()
            except Exception as exc:
                st.error(f"Transcription failed: {exc}")

    with st.expander("👥 The 20 workers", expanded=False):
        for agent_id, agent in AGENTS.items():
            st.markdown(f"{agent['icon']} **{agent['name']}**")
            st.caption(agent["focus"])

    st.divider()
    st.markdown("<div class='az-note'>Executive can work with files inside its workspace and connected APIs. It cannot directly control arbitrary apps installed on your Android phone from a hosted Streamlit page.</div>", unsafe_allow_html=True)


# -----------------------------
# Main header and avatar
# -----------------------------
st.markdown('<div class="az-brand">ALPHA ZERO <span style="font-size:1.3rem">✨</span></div>', unsafe_allow_html=True)
st.markdown('<div class="az-subtitle">Your command center. One Boss. Twenty specialists. Clear approvals.</div>', unsafe_allow_html=True)
st.write("")
metric1, metric2, metric3, metric4 = st.columns(4)
with metric1:
    st.metric("Specialist workers", "20", "Boss assigns relevant agents")
with metric2:
    st.metric("Boss mode", mode, "Selected for this request")
with metric3:
    st.metric("Memory", "Neon" if st.session_state.db_backend == "postgres" else "Local", "Persistent when Neon is connected")
with metric4:
    pending_now = sum(1 for a in st.session_state.approval_items if a.get("status") == "pending")
    st.metric("Pending approvals", str(pending_now), "External/destructive actions pause here")

latest_assistant = next((item["content"] for item in reversed(st.session_state.history) if item["role"] == "assistant"), "Welcome! Tell me the outcome you want ALPHA ZERO to work toward.")
with st.expander("✨ Meet your live chibi anime assistant", expanded=True):
    components_html = st.components.v1.html(avatar_html(st.session_state.avatar_mood, latest_assistant), height=334, scrolling=False)
    st.caption("The character blinks and floats continuously. Tap Speak to hear the latest assistant response; the mouth animation runs during browser speech. Voice availability depends on your browser/device.")

if st.session_state.db_backend != "postgres":
    st.info("Memory is currently using local SQLite, not Neon. Add `NEON_DATABASE_URL` to Streamlit Secrets to activate Neon memory across app restarts.")

# Voice transcript review panel
if st.session_state.pending_voice_prompt:
    with st.form("voice_prompt_review"):
        voice_prompt_edited = st.text_area("Review transcribed voice prompt", value=st.session_state.pending_voice_prompt, height=110)
        voice_send = st.form_submit_button("Send voice prompt", type="primary")
    if voice_send:
        voice_prompt = voice_prompt_edited.strip()
        st.session_state.pending_voice_prompt = ""
        if voice_prompt:
            with st.spinner("ALPHA ZERO is working…"):
                result = process_request(voice_prompt, mode, max_workers)
            st.session_state.history.append({"role": "assistant", "content": result})
            save_message(st.session_state.session_id, "assistant", result)
            st.session_state.avatar_mood = detect_mood(result)
            st.rerun()

# Render conversation history
for message in st.session_state.history:
    with st.chat_message(message["role"], avatar="✨" if message["role"] == "assistant" else "🙂"):
        st.markdown(message["content"])

prompt = st.chat_input("Tell the Boss what you need done…", key="alpha_zero_chat_input")
if prompt:
    with st.spinner("ALPHA ZERO is working…"):
        result = process_request(prompt, mode, max_workers)
    st.session_state.history.append({"role": "assistant", "content": result})
    save_message(st.session_state.session_id, "assistant", result)
    st.session_state.avatar_mood = detect_mood(result)
    st.rerun()

# Show the latest planned task cards in Plan and Executive modes.
if st.session_state.get("last_plan"):
    with st.expander("📋 Latest Boss plan", expanded=False):
        plan = st.session_state.last_plan
        st.markdown(f"**Goal:** {plan.get('goal', '')}")
        for idx, task in enumerate(plan.get("tasks", []), 1):
            agent = AGENTS.get(task["agent_id"], AGENTS["planner"])
            st.markdown(f"**{idx}. {agent['icon']} {agent['name']}** · {task['title']}")
            st.caption(task["instruction"])
            if task.get("tools"):
                st.caption("Allowed tools: " + ", ".join(task["tools"]))

# Pending sensitive action approval queue
with st.expander(f"🔐 Approval center · {sum(1 for a in st.session_state.approval_items if a.get('status') == 'pending')} pending", expanded=True):
    pending_items = [a for a in st.session_state.approval_items if a.get("status") == "pending"]
    if not pending_items:
        st.success("No actions are waiting for approval.")
    for item in pending_items:
        action = item["action"]
        tool = action.get("tool", "unknown")
        args = action.get("arguments", {})
        st.markdown(f"**{item.get('agent_name', 'Worker')}** · `{tool}`")
        st.caption(f"Task: {item.get('task_title', 'Worker action')}")
        # Never show credential values in action payload; connector secrets are not passed through worker arguments.
        st.json(args, expanded=False)
        checkbox_key = f"approval_check_{item['id']}"
        consent = st.checkbox("I reviewed this action and approve it to run now.", key=checkbox_key)
        approve_col, reject_col = st.columns(2)
        with approve_col:
            if st.button("Approve & execute", key=f"approve_{item['id']}", type="primary", disabled=not consent, use_container_width=True):
                with st.spinner("Executing approved action…"):
                    state, result = perform_action(action, approved=True)
                item["status"] = "approved_and_executed" if state == "done" else "approved_but_failed"
                item["result"] = result
                save_approval(item, item["status"], result)
                st.rerun()
        with reject_col:
            if st.button("Reject action", key=f"reject_{item['id']}", use_container_width=True):
                item["status"] = "rejected"
                item["result"] = "Rejected by user; no action was executed."
                save_approval(item, "rejected", item["result"])
                st.rerun()

completed_approvals = [a for a in st.session_state.approval_items if a.get("status") not in {"pending"}]
if completed_approvals:
    with st.expander("Action history", expanded=False):
        for item in completed_approvals[:20]:
            icon = "✅" if item.get("status") == "approved_and_executed" else "⛔"
            st.markdown(f"{icon} **{item.get('action', {}).get('tool', 'action')}** · {item.get('status')}")
            if item.get("result"):
                st.caption(item["result"][:1800])

# Download created workspace artifacts directly from the app.
with st.expander("📁 Workspace files created by Executive", expanded=False):
    workspace_files = [p for p in WORKSPACE_DIR.rglob("*") if p.is_file()]
    if not workspace_files:
        st.caption("No files have been created in the runtime workspace yet.")
    for file_path in sorted(workspace_files, key=lambda p: str(p).lower())[:100]:
        relative = file_path.relative_to(WORKSPACE_DIR).as_posix()
        try:
            file_bytes = file_path.read_bytes()
            st.download_button(
                label=f"⬇️ {relative} ({len(file_bytes):,} bytes)",
                data=file_bytes,
                file_name=file_path.name,
                key=f"download_{relative}",
                use_container_width=True,
            )
        except Exception as exc:
            st.caption(f"Could not prepare {relative} for download: {exc}")

st.markdown('<div class="az-footer">ALPHA ZERO · Bright UI · GPT-6 Luna · 20-agent orchestration · Approval-first execution</div>', unsafe_allow_html=True)
