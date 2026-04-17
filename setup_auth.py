"""One-shot helper: opens browser for Google OAuth consent and writes token.json."""
import sys

from gmail_client import get_service


def main() -> int:
    svc = get_service()
    profile = svc.users().getProfile(userId="me").execute()
    print(f"Authenticated as {profile.get('emailAddress')}")
    print("Token saved. You can now run: python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
