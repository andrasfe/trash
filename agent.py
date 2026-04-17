import json
import logging
from typing import TypedDict

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

import config
import gmail_client
import rules_store

log = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    trash: list[dict]
    inbox: list[dict]
    store: dict
    new_rules_this_run: int
    candidates: list[dict]
    moved: list[dict]


def _llm():
    return ChatOpenAI(
        base_url=config.LM_STUDIO_BASE_URL,
        api_key="lm-studio",
        model=config.LM_STUDIO_MODEL,
        temperature=0.0,
        timeout=120,
    )


ALLOWED_TAGS = {
    "marketing",
    "social",
    "job-alert",
    "news",
    "transactional",
    "security",
    "personal",
    "other",
}

SAFE_AUTO_MOVE_TAGS = {"marketing", "social", "job-alert", "news"}

TOPIC_SYS = (
    "You label emails with ONE tag from this fixed set:\n"
    "- marketing: promotional content, newsletters, product pitches, sales, deals, "
    "brand announcements. Includes urgent-sounding marketing like 'last chance', "
    "countdown/deadline reminders from brands (e.g. tax-filing reminders from TurboTax, "
    "offers expiring soon). If it is bulk promotional mail, it is marketing.\n"
    "- social: notifications from social platforms (LinkedIn, Facebook, X, Reddit, "
    "Instagram) about connections, posts, reactions, follows, searches.\n"
    "- job-alert: job listings, application updates, recruiter outreach, job boards.\n"
    "- news: news digests, daily briefings, headline roundups from news publishers.\n"
    "- transactional: receipts, order confirmations, shipping updates, invoices, "
    "appointment confirmations, booking details.\n"
    "- security: password resets, 2FA codes, login alerts, account verification emails, "
    "sign-in notifications. ONLY real account-security mail — not brand marketing that "
    "merely uses urgent language.\n"
    "- personal: direct person-to-person email from a human, not automated/bulk.\n"
    "- other: anything that doesn't fit above.\n\n"
    'Return ONLY compact JSON: {"topic_tag": "<one tag from the list>", "reason": "<brief>"}. '
    "Do not invent new tags."
)


def _classify_topic(llm, email: dict) -> str:
    prompt = (
        f"From: {email['from']}\n"
        f"Subject: {email['subject']}\n"
        f"Snippet: {email['snippet']}\n"
        f"Body:\n{email['body_excerpt']}\n"
    )
    resp = llm.invoke([SystemMessage(content=TOPIC_SYS), HumanMessage(content=prompt)])
    text = resp.content.strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        data = json.loads(text[start : end + 1])
        tag = str(data.get("topic_tag", "")).strip().lower()
        if tag not in ALLOWED_TAGS:
            log.warning("LLM returned out-of-vocab tag %r, coercing to 'other'", tag)
            tag = "other"
        return tag
    except Exception:
        log.warning("Topic parse failed, raw=%r", text[:200])
        return "other"


def node_fetch_trash(state: AgentState) -> AgentState:
    trash = gmail_client.list_trash_since(config.TRASH_LOOKBACK_HOURS)
    store = rules_store.load_rules()
    unseen = [m for m in trash if m["id"] not in set(store["seen_trash_ids"])]
    log.info("trash fetched=%d unseen=%d", len(trash), len(unseen))
    return {"trash": unseen, "store": store}


def node_extract_rules(state: AgentState) -> AgentState:
    store = state["store"]
    llm = _llm()
    added = 0
    for m in state["trash"]:
        topic = _classify_topic(llm, m)
        rules_store.bump_rule(store, m["from_domain"], topic, m["subject"])
        rules_store.mark_trash_seen(store, m["id"])
        added += 1
        log.info("learn: %s :: %s (%s)", m["from_domain"], topic, m["subject"][:60])
    rules_store.save_rules(store)
    return {"store": store, "new_rules_this_run": added}


def node_scan_inbox(state: AgentState) -> AgentState:
    store = state["store"]
    active = rules_store.active_rules(store, config.CONFIDENCE_THRESHOLD)
    if not active:
        log.info("no active rules yet (threshold=%d)", config.CONFIDENCE_THRESHOLD)
        return {"inbox": [], "candidates": []}
    active_domains = sorted({r["sender_domain"] for r in active})
    log.info("scanning inbox for %d active domains: %s", len(active_domains), ", ".join(active_domains))
    narrowed = gmail_client.list_inbox_from_domains(active_domains, per_domain_cap=config.INBOX_SCAN_LIMIT)
    log.info("inbox domain-hits=%d active-rules=%d", len(narrowed), len(active))
    return {"inbox": narrowed, "candidates": []}


def node_match(state: AgentState) -> AgentState:
    store = state["store"]
    active = rules_store.active_rules(store, config.CONFIDENCE_THRESHOLD)
    by_domain: dict[str, set[str]] = {}
    for r in active:
        by_domain.setdefault(r["sender_domain"], set()).add(r["topic_tag"])

    llm = _llm()
    candidates = []
    already_moved = set(store["moved_ids"])
    for m in state["inbox"]:
        if m["id"] in already_moved:
            continue
        topic = _classify_topic(llm, m)
        if topic in by_domain.get(m["from_domain"], set()):
            if topic not in SAFE_AUTO_MOVE_TAGS:
                log.info(
                    "skip-unsafe: %s :: %s (%s) — tag not in auto-move set",
                    m["from_domain"], topic, m["subject"][:60],
                )
                continue
            candidates.append({**m, "matched_topic": topic})
            log.info("match: %s :: %s (%s)", m["from_domain"], topic, m["subject"][:60])
    return {"candidates": candidates}


def node_apply(state: AgentState) -> AgentState:
    store = state["store"]
    moved = []
    if not state["candidates"]:
        return {"moved": []}
    if config.DRY_RUN:
        for c in state["candidates"]:
            log.info("[DRY_RUN] would move %s subj=%r", c["id"], c["subject"][:80])
        return {"moved": []}
    label_id = gmail_client.ensure_label(config.AGENT_TRASH_LABEL)
    for c in state["candidates"]:
        try:
            gmail_client.move_to_label(c["id"], label_id)
            rules_store.mark_moved(store, c["id"])
            moved.append(c)
            log.info("moved %s subj=%r", c["id"], c["subject"][:80])
        except Exception as e:
            log.exception("move failed for %s: %s", c["id"], e)
    rules_store.save_rules(store)
    return {"moved": moved}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("fetch_trash", node_fetch_trash)
    g.add_node("extract_rules", node_extract_rules)
    g.add_node("scan_inbox", node_scan_inbox)
    g.add_node("match", node_match)
    g.add_node("apply", node_apply)
    g.set_entry_point("fetch_trash")
    g.add_edge("fetch_trash", "extract_rules")
    g.add_edge("extract_rules", "scan_inbox")
    g.add_edge("scan_inbox", "match")
    g.add_edge("match", "apply")
    g.add_edge("apply", END)
    return g.compile()


def run_once() -> dict:
    graph = build_graph()
    return graph.invoke({})
