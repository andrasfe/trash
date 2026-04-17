import json
import os
from datetime import datetime, timezone
from typing import Optional

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_rules() -> dict:
    if not os.path.exists(config.RULES_PATH):
        return {"rules": {}, "seen_trash_ids": [], "moved_ids": []}
    with open(config.RULES_PATH) as f:
        return json.load(f)


def save_rules(state: dict) -> None:
    tmp = config.RULES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, config.RULES_PATH)


def rule_key(sender_domain: str, topic_tag: str) -> str:
    return f"{sender_domain}::{topic_tag}"


def bump_rule(state: dict, sender_domain: str, topic_tag: str, example_subject: str) -> None:
    key = rule_key(sender_domain, topic_tag)
    rule = state["rules"].get(key)
    if rule is None:
        rule = {
            "sender_domain": sender_domain,
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
