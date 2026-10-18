# ✨ ALPHA ZERO

ALPHA ZERO is a bright Streamlit AI command center with one Boss, 20 specialist workers, three operating modes, a built-in animated chibi anime avatar, optional Neon PostgreSQL memory, and an approval-first action system.

> **Default model:** `gpt-6-luna` (override with `OPENAI_MODEL` in Streamlit Secrets). Model availability and billing depend on your OpenAI API project.

## Exactly three project files

This starter project intentionally contains only:

- `app.py` - complete app, UI, avatar HTML/CSS/JS, Boss planner, 20 worker definitions, tool executors, approvals, memory, voice controls, and persistence.
- `requirements.txt` - Python dependencies.
- `README.md` - this guide.

The app creates its runtime workspace and local SQLite fallback automatically. No extra local source folders are required.

## Boss modes

### 1. Interactive

One-on-one chat with the configured OpenAI model. It does not delegate workers or execute tools.

### 2. Plan

Boss returns a task plan with assigned specialists, allowed tools, dependencies/risks, and completion checks. It does not run the worker tasks or external write actions.

### 3. Executive

Boss creates a plan, selects relevant workers from the 20-agent roster (up to the selected worker limit), asks them for work, validates their proposed tools, runs permitted safe actions, collects reports and tool results, and produces a final report. A task does not have to use all 20 agents.

## The 20 workers

1. Research Scout
2. Strategy Planner
3. Software Builder
4. QA Tester
5. Data Analyst
6. Content Writer
7. Fact Checker
8. Security Analyst
9. API Integrator
10. Database Engineer
11. UI/UX Designer
12. Automation Engineer
13. Documentation Writer
14. Debugger
15. Project Manager
16. Product Strategist
17. Defensive Cybersecurity
18. Workspace Manager
19. Web Research Agent
20. Final Report Editor

The Boss selects specialists relevant to the request. These are LLM-driven specialist roles, not 20 separately hosted model servers. Using several workers creates several API calls and may increase cost and latency.

## Setup from an Android phone

1. Download/extract the project ZIP if you received one, or create a GitHub repository named `ALPHA-ZERO`.
2. Upload **these three files at the repository root**: `app.py`, `requirements.txt`, `README.md`. Do not upload API keys.
3. In Streamlit Community Cloud, create a new app and select the repository, branch, and `app.py` as the main file.
4. Open the deployed app's settings and add the Secrets shown below.
5. Restart/redeploy the app after changing dependencies or Secrets.

## Required Streamlit Secrets

Paste a TOML configuration like this into **Streamlit → App settings → Secrets**. Replace placeholder strings with your own values. Do not commit this text with real credentials to GitHub.

```toml
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
OPENAI_MODEL = "gpt-6-luna"
NEON_DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
ALPHA_ZERO_NAMESPACE = "personal"
```

`OPENAI_MODEL` can be changed to another model that is actually enabled for your API project. The app uses the OpenAI API, which is billed separately from a ChatGPT subscription.

## Optional app integrations

### GitHub API

```toml
GITHUB_TOKEN = "YOUR_GITHUB_TOKEN"
GITHUB_OWNER = "YOUR_GITHUB_USERNAME_OR_ORG"
GITHUB_REPO = "YOUR_REPO_NAME"
```

GitHub reads are read-only. Creating issues and writing/updating repository files always wait for explicit approval in the Approval Center.

### A service API you control

```toml
APP_API_BASE_URL = "https://api.example.com"
APP_API_BEARER_TOKEN = "YOUR_SERVICE_TOKEN"
APP_API_ALLOWED_PATHS = "/v1/tasks,/v1/messages"
```

Only HTTPS is accepted. A model-suggested API path must match one of the exact prefixes you allow. GET is read-only. POST, PATCH and DELETE wait for approval. Use a service/API you own or are authorized to operate. Never paste a key into a chat prompt.

### Webhook

```toml
APP_WEBHOOK_URL = "https://your-approved-service.example/webhook"
```

Webhook payloads always wait for approval. Do not configure an endpoint you do not trust.

### Optional voice transcription model

```toml
AUDIO_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
```

The built-in voice control records in the browser, calls the OpenAI transcription API, then lets you review the transcript before sending it to Boss. Browser speech synthesis reads the latest assistant response aloud. The avatar's mouth animation runs while browser speech is active. Browser voices vary by device, browser, and language.

## Tool behavior and safety

### Tools that can run during Executive

- OpenAI hosted web search, when that tool is available to the selected model/API account.
- List/read/create new files within the app's private runtime workspace.
- Read the configured GitHub repo (only after GitHub Secrets are set).
- Read a configured app API endpoint that matches `APP_API_ALLOWED_PATHS`.

### Actions that pause for approval

- Overwriting an existing workspace file.
- Deleting a workspace file.
- Creating a GitHub issue.
- Writing/updating a file in GitHub.
- API calls using POST/PATCH/DELETE.
- Webhook POSTs.

Review the exact action arguments in the Approval Center and click **Approve & execute** only when you intend to allow the operation. Reject any unexpected payload. A code model is not a security boundary; restrict tokens to least privilege and test with a disposable repository first.

## Neon memory and fallback

- When `NEON_DATABASE_URL` is configured and reachable, ALPHA ZERO stores chat messages, run records, saved memories, and approval logs in Neon PostgreSQL.
- If Neon is missing or unreachable at startup, the app falls back to a local `alpha_zero_memory.db` SQLite file and shows a visible notice. A hosted Streamlit filesystem may be ephemeral, so local fallback data may not survive redeploy/restart.
- Long-term memories can be saved in the sidebar. Use these only for non-sensitive preferences and project facts.
- Set `ALPHA_ZERO_NAMESPACE` to keep separate app instances/namespaces from mixing their stored records.

## Avatar and UI

The chibi anime character is embedded into `app.py`; no separate avatar directory is needed. It uses SVG + CSS + JavaScript for idle float, blinking eyes, mood-specific facial features, sparkles, and speaking-mouth animation synchronized with browser `speechSynthesis`. Its expression is selected from the latest reply's tone.

This is a live animated 2D character inside the Streamlit page, not a full 3D rig or phoneme-accurate lip-sync system. Exact speech timing, voices, and mic permissions depend on the browser/device.

## Important limitation: Android app control

A Streamlit site running in the cloud **cannot directly tap buttons in arbitrary Android apps installed on your phone**. ALPHA ZERO can operate services through their supported APIs/connectors, such as a configured GitHub repo, a service API, or a webhook. To control a particular service, configure its official API or a trusted adapter and allow only the necessary paths/permissions. Do not give an agent unrestricted credentials.

## Security checklist

- Never place API keys in `app.py`, `README.md`, chat messages, or public GitHub commits.
- Keep the deployed app private or behind authentication. The database namespace is not a user-authentication system.
- Use a least-privilege GitHub token, preferably limited to one test repo.
- Start with read-only permissions. Review every write action before approval.
- Avoid adding sensitive personal information to saved memories or action logs.
- Rotate any token that is accidentally exposed.

## Troubleshooting

- **OpenAI key missing:** add `OPENAI_API_KEY` in Streamlit Secrets and reboot the app.
- **Model access error:** verify that the exact `OPENAI_MODEL` ID is available in your API project and that billing/access are active. The default is `gpt-6-luna`; the app will not silently pretend a failed call succeeded.
- **Neon warning:** check the complete connection string, password, host, database name, and `sslmode=require`; verify Neon allows connections. SQLite fallback is clearly indicated in the app.
- **Web search unavailable:** hosted web search depends on the selected model and API account. The app records the tool failure instead of fabricating search results.
- **Voice recording/transcription unavailable:** allow browser microphone access and verify API access to the transcription model. You can continue by typing into chat.
- **GitHub action fails:** check owner/repo spelling and token permissions. Use a test repository first.
- **Workspace files:** downloads are listed in the Workspace Files panel. The app's runtime workspace may not survive a redeploy unless you download outputs or store them in a persistent service.

## Technical notes

- Framework: Streamlit.
- Model SDK: OpenAI Python SDK.
- Primary memory: Neon PostgreSQL via psycopg v3.
- Local fallback: SQLite.
- App API requests: `requests`, HTTPS only, allowlisted path prefixes, write methods gated by approval.
- No arbitrary shell command execution or arbitrary Python-code execution is exposed as a worker tool.
