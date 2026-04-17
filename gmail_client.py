import base64
import json
import os
from email.message import EmailMessage
from email.utils import parseaddr, formatdate
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

SKILLS_SUBJECT = "trash-agent-skills snapshot"


def get_service():
    creds = None
    if os.path.exists(config.GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_PATH, config.GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(config.GMAIL_CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"Missing OAuth credentials at {config.GMAIL_CREDENTIALS_PATH}. "
                    "Create an OAuth Desktop Client in Google Cloud Console and download it here."
                )
            flow = InstalledAppFlow.from_client_secrets_file(config.GMAIL_CREDENTIALS_PATH, config.GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(config.GMAIL_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload):
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload["parts"]:
            text = _decode_body(part)
            if text:
                return text
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def _parse_message(svc, msg_id):
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    from_raw = _header(headers, "From")
    _, addr = parseaddr(from_raw)
    domain = addr.split("@")[-1].lower() if "@" in addr else addr.lower()
    body = _decode_body(payload)
    return {
        "id": msg_id,
        "thread_id": msg.get("threadId"),
        "from": from_raw,
        "from_email": addr.lower(),
        "from_domain": domain,
        "subject": _header(headers, "Subject"),
        "snippet": msg.get("snippet", ""),
        "body_excerpt": (body or msg.get("snippet", ""))[:1500],
        "label_ids": msg.get("labelIds", []),
    }


def list_trash_since(hours: int, max_results: int = 100):
    svc = get_service()
    after = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    q = f"in:trash after:{after}"
    resp = svc.users().messages().list(userId="me", q=q, maxResults=max_results).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    return [_parse_message(svc, i) for i in ids]


def list_inbox(max_results: int):
    svc = get_service()
    resp = svc.users().messages().list(userId="me", q="in:inbox", maxResults=max_results).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    return [_parse_message(svc, i) for i in ids]


def list_inbox_from_senders(senders, per_sender_cap: int = 500):
    """Return all inbox messages whose sender email matches any in `senders`.
    Paginates through Gmail search; de-dupes by message id."""
    svc = get_service()
    seen_ids: set[str] = set()
    out: list[dict] = []
    for sender in senders:
        q = f"in:inbox from:{sender}"
        page_token = None
        collected = 0
        while True:
            params = {"userId": "me", "q": q, "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            resp = svc.users().messages().list(**params).execute()
            msgs = resp.get("messages", [])
            for m in msgs:
                if m["id"] in seen_ids:
                    continue
                seen_ids.add(m["id"])
                out.append(_parse_message(svc, m["id"]))
                collected += 1
                if collected >= per_sender_cap:
                    break
            page_token = resp.get("nextPageToken")
            if not page_token or collected >= per_sender_cap:
                break
    return out


def ensure_label(name: str) -> str:
    svc = get_service()
    labels = svc.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"].lower() == name.lower():
            return lbl["id"]
    created = svc.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def move_to_label(msg_id: str, label_id: str):
    svc = get_service()
    svc.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
    ).execute()


def _user_email(svc) -> str:
    return svc.users().getProfile(userId="me").execute()["emailAddress"]


def _build_skills_raw(state: dict, user_addr: str) -> str:
    em = EmailMessage()
    em["From"] = user_addr
    em["To"] = user_addr
    em["Subject"] = SKILLS_SUBJECT
    em["Date"] = formatdate(localtime=False)
    em.set_content(json.dumps(state, indent=2, sort_keys=True))
    return base64.urlsafe_b64encode(em.as_bytes()).decode()


def _find_skills_drafts(svc):
    """Return all drafts whose subject matches our snapshot. Labels may or
    may not be applied (a prior write could have failed partway)."""
    resp = svc.users().drafts().list(
        userId="me", q=f'subject:"{SKILLS_SUBJECT}"', maxResults=25,
    ).execute()
    return [d["id"] for d in resp.get("drafts", [])]


def _find_skills_draft(svc, label_id: str):
    ids = _find_skills_drafts(svc)
    return ids[0] if ids else None


def read_skills_snapshot():
    svc = get_service()
    label_id = ensure_label(config.SKILLS_LABEL)
    draft_id = _find_skills_draft(svc, label_id)
    if not draft_id:
        return None
    draft = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    body = _decode_body(draft["message"]["payload"])
    try:
        return json.loads(body)
    except Exception:
        return None


def write_skills_snapshot(state: dict) -> None:
    svc = get_service()
    label_id = ensure_label(config.SKILLS_LABEL)
    user_addr = _user_email(svc)
    raw = _build_skills_raw(state, user_addr)

    existing = _find_skills_drafts(svc)
    body = {"message": {"raw": raw}}
    if existing:
        draft_id = existing[0]
        result = svc.users().drafts().update(userId="me", id=draft_id, body=body).execute()
        for dup in existing[1:]:
            try:
                svc.users().drafts().delete(userId="me", id=dup).execute()
            except Exception:
                pass
    else:
        result = svc.users().drafts().create(userId="me", body=body).execute()

    msg_id = result.get("message", {}).get("id")
    if msg_id:
        svc.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [label_id]},
        ).execute()
