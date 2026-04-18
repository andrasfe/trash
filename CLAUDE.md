# Project guidance for Claude

This is a single-user local agent that learns from the Gmail Trash and
auto-labels look-alike mail in the inbox. It is safety-sensitive — a bug
can silently move real mail — so the conventions below are load-bearing.

## Architecture

```
main.py            polling loop (SIGINT/SIGTERM-safe)
agent.py           LangGraph pipeline:
  fetch_trash      list recent trash since N hours, de-dup via seen_trash_ids
  extract_rules    LLM tags each trash item, bump rule counts in rules.json
  scan_inbox       query Gmail PER sender domain (not whole-inbox slice)
  match            LLM classifies candidates; drop unless tag is SAFE
  apply            move to `agent-trash` label, record moved_id
gmail_client.py    OAuth + Gmail REST; pagination helpers live here
rules_store.py     rules.json: {rules, seen_trash_ids, moved_ids}
config.py          env loading
```

Each node returns a *partial* state update; LangGraph merges. State shape
is `AgentState` (TypedDict) in `agent.py`.

## Invariants — do not break without discussion

- **Closed topic vocabulary.** The LLM must pick one of `ALLOWED_TAGS` in
  `agent.py`. Out-of-vocab responses are coerced to `other`. Don't let the
  model invent free-form tags — it fragments rules and dilutes counts.
- **Safe auto-move allowlist.** Only tags in `SAFE_AUTO_MOVE_TAGS`
  (`marketing`, `social`, `job-alert`, `news`) ever trigger a move. A
  matched rule is deliberately skipped for `security`, `transactional`,
  `personal`, `other` — see `node_match`. Do not add these tags to the
  allowlist, even for convenience.
- **Confidence threshold = 1 (default).** One delete → one rule → act on
  matching inbox mail. This is the product's whole point. Do NOT raise
  this back to 2 as a "safety" measure — the safe-tag allowlist is the
  backstop, not the threshold. A user who wants a stricter experience can
  bump it in their own `.env`.
- **Rules key on full sender email, not domain.** A rule is
  `(sender_email, topic_tag)`, e.g. `newsletter@brand.com :: marketing`.
  Keying on domain conflated bulk newsletter senders with individual
  humans at the same company (e.g. `thammer@the1916company.com` was
  wrongly auto-moved because `noreply@the1916company.com` had been
  trashed). Do NOT revert to domain keying. If a user needs
  domain-wide rules they can opt in later; the safe default is strict.
- **Inbox scan queries *per-sender*, not slices.** `list_inbox_from_senders`
  issues one `in:inbox from:<email>` query per active rule and paginates.
  Never go back to `list_inbox(max_results)` for general-purpose inbox
  sweeps — that caps aggressively on recency and silently misses matches.
- **Dry-run default.** `DRY_RUN=true` is the default in `.env.example`.
  Any refactor that changes what gets moved must be tested with DRY_RUN
  first.
- **Idempotence.** `moved_ids` in `rules.json` prevents re-processing a
  message. Don't remove this. If a user moves a message back to INBOX,
  the agent should NOT move it again — that's the point.
- **Skipped-cache.** `skipped_ids` remembers messages that matched a rule
  but were blocked by the safe-tag filter. Without it, every cycle
  re-classifies the same backlog (e.g. years of Google security alerts)
  only to skip them again — thousands of wasted LLM calls. If a user
  edits `SAFE_AUTO_MOVE_TAGS`, `skipped_ids` may need to be cleared so
  newly-safe tags get re-evaluated.
- **Atomic state writes.** `rules_store.save_rules` writes to a `.tmp`
  file then `os.replace`. Preserve this on any change.
- **Skills sync via Gmail draft.** The whole `rules.json` content
  (rules + seen_trash_ids + moved_ids + `_updated_at`) is mirrored to a
  single continuously-updated Gmail draft labeled
  `trash-agent-skills`. On startup `sync_from_gmail` pulls if the remote
  timestamp is newer; after each cycle `sync_to_gmail` pushes. This
  lets the agent move to a new machine without porting local state —
  fresh OAuth + same account recovers everything. Do not change the
  draft storage format lightly; existing snapshots on other machines
  would become unreadable.

## Secrets — never commit

`.gitignore` excludes `.env`, `credentials.json`, `token.json`,
`rules.json`, `agent.log`. If you add a new file, double-check it
doesn't leak tokens or learned user data.

## Non-obvious Gmail behaviors

- **Label views hide TRASH-tagged messages.** If a message has both
  `Label_<id>` and `TRASH`, it does not appear in the label's UI view.
  Users sometimes trash messages we've already labeled; that's expected.
- **Threads vs messages.** Gmail search/list returns messages, but the UI
  groups by thread. A label's UI count reflects threads, not messages.
- **`in:trash` + `after:<unix_ts>`** works for Gmail queries; prefer unix
  timestamps over `YYYY/MM/DD` for precision.
- **`gmail.modify` scope** is enough for labels and inbox moves. We do not
  request `gmail.readonly` or `gmail.full`.

## Testing locally

- Always keep a recent LM Studio model id in `.env` (`LM_STUDIO_MODEL`).
  The OpenAI-compatible endpoint rejects unknown ids with a 400.
- Before any behavior change, flip `DRY_RUN=true` and run a cycle. The
  log emits `[DRY_RUN] would move ...` lines and writes no state.
- `python -c "from agent import run_once; run_once()"` runs a single
  cycle without the polling loop — useful for iteration.
- `rm rules.json` resets state. The next cycle rebuilds from trash
  within `TRASH_LOOKBACK_HOURS`.

## Style rules for this repo

- Terse, direct Python. No speculative abstractions, no framework
  scaffolding. One file per concern.
- No unnecessary comments. The code's structure tells the story.
- Logging uses `log.info("<verb>: <key>=<val>", ...)` with lowercase verbs
  (`learn:`, `match:`, `moved `, `skip-unsafe:`). The Monitor filters in
  dev rely on these prefixes.

## What to ask before changing

- Anything that relaxes the safety filter (`SAFE_AUTO_MOVE_TAGS`,
  `CONFIDENCE_THRESHOLD`, the vocabulary).
- Anything that changes *what* gets moved (scan strategy, match logic,
  label target). User always wants to see a dry-run diff first.
- Anything that deletes rather than labels. The agent never hard-deletes;
  moving to `agent-trash` is the contract.
