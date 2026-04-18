import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import config

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_rules() -> dict:
    if not os.path.exists(config.RULES_PATH):
        return {"rules": {}, "seen_trash_ids": [], "moved_ids": [], "skipped_ids": []}
    with open(config.RULES_PATH) as f:
        state = json.load(f)
    state.setdefault("skipped_ids", [])
    return state


def save_rules(state: dict) -> None:
    state["_updated_at"] = _now()
    tmp = config.RULES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, config.RULES_PATH)


def sync_from_gmail() -> bool:
    """If the Gmail skills draft is newer than local state, overwrite local.
    Returns True if local state changed."""
    if not config.SKILLS_SYNC_ENABLED:
        return False
    import gmail_client
    try:
        remote = gmail_client.read_skills_snapshot()
    except Exception:
        log.exception("skills sync (read) failed — continuing with local state")
        return False
    if remote is None:
        log.info("no remote skills draft found; local state unchanged")
        return False
    local = load_rules()
    r_t = remote.get("_updated_at", "")
    l_t = local.get("_updated_at", "")
    if r_t > l_t:
        tmp = config.RULES_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(remote, f, indent=2)
        os.replace(tmp, config.RULES_PATH)
        log.info("pulled skills from gmail (remote=%s > local=%s)", r_t, l_t)
        return True
    log.info("local skills newer than gmail (local=%s, remote=%s)", l_t, r_t)
    return False


def sync_to_gmail(state: dict) -> None:
    if not config.SKILLS_SYNC_ENABLED:
        return
    import gmail_client
    try:
        gmail_client.write_skills_snapshot(state)
        log.info("pushed skills to gmail (updated_at=%s)", state.get("_updated_at", ""))
    except Exception:
        log.exception("skills sync (write) failed — local state preserved")


def rule_key(sender_email: str, topic_tag: str) -> str:
    return f"{sender_email}::{topic_tag}"


def bump_rule(state: dict, sender_email: str, topic_tag: str, example_subject: str) -> None:
    key = rule_key(sender_email, topic_tag)
    rule = state["rules"].get(key)
    if rule is None:
        rule = {
            "sender_email": sender_email,
            "topic_tag": topic_tag,
            "count": 0,
            "first_seen": _now(),
            "last_seen": _now(),
            "example_subjects": [],
        }
    rule["count"] += 1
    rule["last_seen"] = _now()
    if example_subject and example_subject not in rule["example_subjects"]:
        rule["example_subjects"] = (rule["example_subjects"] + [example_subject])[-5:]
    state["rules"][key] = rule


def active_rules(state: dict, threshold: int) -> list[dict]:
    return [r for r in state["rules"].values() if r["count"] >= threshold]


def mark_trash_seen(state: dict, msg_id: str) -> bool:
    if msg_id in state["seen_trash_ids"]:
        return False
    state["seen_trash_ids"] = (state["seen_trash_ids"] + [msg_id])[-5000:]
    return True


def mark_moved(state: dict, msg_id: str) -> None:
    state["moved_ids"] = (state["moved_ids"] + [msg_id])[-5000:]


def mark_skipped(state: dict, msg_id: str) -> None:
    if msg_id in state["skipped_ids"]:
        return
    state["skipped_ids"] = (state["skipped_ids"] + [msg_id])[-10000:]
