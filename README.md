# gmail-trash-agent

An always-on local agent that learns which emails you delete and auto-moves
future look-alikes out of your inbox.

Built with **LangGraph** and a **local LLM** (via [LM Studio](https://lmstudio.ai/))
— no email contents or metadata leave your machine.

## What it does

Every N minutes the agent:

1. Reads recent items from your Gmail **Trash**.
2. Uses the local model to tag each one with a short topic (from a closed
   vocabulary: `marketing`, `social`, `job-alert`, `news`, `transactional`,
   `security`, `personal`, `other`).
3. Records a rule of the form `(sender_domain, topic_tag)` with a rolling
   count in [`rules.json`](./rules.json).
4. Once a rule has been confirmed ≥ `CONFIDENCE_THRESHOLD` times, scans your
   inbox for senders matching that rule, classifies each hit, and if the
   topic also matches:
5. Moves the message from `INBOX` to a label called `agent-trash`
   (created on first use). Nothing is hard-deleted — you can always recover.

### Safety rails

- **Dry-run by default.** `DRY_RUN=true` logs what *would* be moved without
  touching anything. Flip to `false` once you trust it.
- **Confidence threshold** (default 2) prevents one-off deletions from
  creating aggressive filters.
- **Closed-vocabulary tags** keep the LLM from fragmenting similar mail into
  many near-duplicate rules.
- **Auto-move allowlist.** Only `marketing`, `social`, `job-alert`, and
  `news` tags trigger moves. `security`, `transactional`, and `personal`
  are never auto-moved even when a rule matches.
- **Idempotent.** Each message id is remembered in `moved_ids`, so moving a
  message back to inbox doesn't cause a loop.

## Architecture

```
main.py                      # polling loop, signal-safe
 └─ agent.py                  # LangGraph pipeline
     ├─ fetch_trash           # Gmail API: recent trash
     ├─ extract_rules         # LLM classifies each → rules.json
     ├─ scan_inbox            # narrow inbox to active-rule domains
     ├─ match                 # LLM classifies candidates, filter by safe tags
     └─ apply                 # Gmail API: move to agent-trash label
gmail_client.py               # OAuth + Gmail REST calls
rules_store.py                # rules.json I/O, confidence counting
config.py                     # env loading
```

LangGraph nodes share a `TypedDict` state; each node returns a partial
update.

## Setup

### 1. Python environment

Requires Python 3.10+ recommended (works on 3.9 with deprecation warnings).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Gmail OAuth (one-time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/), create a
   project, and **enable the Gmail API**.
2. Go to **Credentials → Create credentials → OAuth client ID**. Pick
   **Desktop app** as the application type.
3. Download the client JSON and save it as `./credentials.json` in this repo.
4. On the **OAuth consent screen**, add your own Gmail address as a **test
   user** (otherwise Google blocks consent while the app is unverified).
5. Run the auth bootstrap — this opens your browser for consent and writes
   `./token.json`:

   ```bash
   python setup_auth.py
   ```

The app requests scope `https://www.googleapis.com/auth/gmail.modify`
(needed to apply/remove labels).

### 3. LM Studio

1. Install [LM Studio](https://lmstudio.ai/).
2. Download a small-to-mid instruction-tuned model. The default
   `openai/gpt-oss-20b` works well and fits in ~16 GB RAM. Larger models
   give better tags but cost more time per cycle.
3. Start the local server on port **1234** (LM Studio → Developer →
   Start Server).
4. Verify: `curl http://127.0.0.1:1234/v1/models`.

### 4. Configure

```bash
cp .env.example .env
```

Key settings:

| Var | Default | Meaning |
| --- | --- | --- |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | OpenAI-compatible endpoint |
| `LM_STUDIO_MODEL` | `local-model` | must match the id LM Studio reports |
| `POLL_INTERVAL_SECONDS` | `600` | sleep between cycles |
| `TRASH_LOOKBACK_HOURS` | `168` | how far back to look on each cycle |
| `INBOX_SCAN_LIMIT` | `50` | max inbox messages fetched per cycle |
| `CONFIDENCE_THRESHOLD` | `2` | min count before a rule auto-moves |
| `AGENT_TRASH_LABEL` | `agent-trash` | Gmail label for moved mail |
| `DRY_RUN` | `true` | `false` = actually move |

### 5. Run

```bash
# foreground (easy to stop with Ctrl-C)
python main.py

# or always-on in the background
nohup python main.py > agent.log 2>&1 &
```

Watch the logs for a few cycles with `DRY_RUN=true` — you'll see
`[DRY_RUN] would move ...` lines. When you're happy with what it picks,
set `DRY_RUN=false` in `.env` and restart.

## Inspecting & tuning

```bash
# see learned rules and counts
python -c "import json; print(json.dumps(json.load(open('rules.json'))['rules'], indent=2))"

# tail the log
tail -f agent.log

# reset state (forces the agent to relearn from trash)
rm rules.json
```

Want it stricter? Bump `CONFIDENCE_THRESHOLD` to 3+, or shorten the
`SAFE_AUTO_MOVE_TAGS` set in `agent.py`. Want it more aggressive? Lower
the threshold to 1, or add more tags to the auto-move allowlist.

## Keeping it always-on

- Quick: `nohup python main.py &` (dies on logout).
- macOS: wrap in a launchd user agent (`~/Library/LaunchAgents/*.plist`)
  that runs `main.py` with `KeepAlive=true`.
- Linux: a user `systemd` unit with `Restart=always`.

## Files

- `agent.py`, `main.py`, `gmail_client.py`, `rules_store.py`, `config.py` — source
- `setup_auth.py` — one-time OAuth bootstrap
- `requirements.txt`, `.env.example` — deps and config template
- `credentials.json`, `token.json`, `.env`, `rules.json`, `agent.log` —
  **runtime artifacts, gitignored**
