import os
from dotenv import load_dotenv

load_dotenv()

LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "local-model")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))
TRASH_LOOKBACK_HOURS = int(os.getenv("TRASH_LOOKBACK_HOURS", "24"))
INBOX_SCAN_LIMIT = int(os.getenv("INBOX_SCAN_LIMIT", "50"))
CONFIDENCE_THRESHOLD = int(os.getenv("CONFIDENCE_THRESHOLD", "1"))
AGENT_TRASH_LABEL = os.getenv("AGENT_TRASH_LABEL", "agent-trash")
SKILLS_LABEL = os.getenv("SKILLS_LABEL", "trash-agent-skills")
SKILLS_SYNC_ENABLED = os.getenv("SKILLS_SYNC_ENABLED", "true").lower() in ("1", "true", "yes")
RULES_PATH = os.getenv("RULES_PATH", "./rules.json")
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "./credentials.json")
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "./token.json")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
