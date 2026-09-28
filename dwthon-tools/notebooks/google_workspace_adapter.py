#!/usr/bin/env python3
"""Read-only Google Workspace adapter for DWthon Tools.

This is a real adapter: it invokes the `gog` CLI, which uses the account
authorized with Google OAuth. It deliberately exposes a tiny, read-only
surface for the Morning Briefing artifact:

* Gmail search
* today's Calendar events

It never passes arbitrary shell commands to gog and it never sends mail.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


class GogError(RuntimeError):
    """A safe-to-display failure from the gog command."""


@dataclass(frozen=True)
class GogWorkspaceClient:
    """Restricted client for one OAuth-authorized Google account."""

    account: str
    binary: str = "gog"

    def healthcheck(self) -> dict[str, str]:
        """Check that gog is installed and the configured account is usable."""
        if shutil.which(self.binary) is None and not os.path.isabs(self.binary):
            raise GogError(
                f"Nie znaleziono binarki '{self.binary}'. Zainstaluj gogcli zgodnie z README."
            )
        self._run_json(["auth", "doctor", "--check"])
        return {"status": "ok", "account": self.account, "mode": "read-only"}

    def search_mail(self, query: str, *, max_results: int = 10) -> Any:
        """Search Gmail using a Gmail query and return gog's parsed JSON output."""
        if not query.strip():
            raise ValueError("Zapytanie Gmail nie może być puste.")
        if not 1 <= max_results <= 50:
            raise ValueError("max_results musi być w zakresie 1-50.")
        return self._run_json(["gmail", "search", query, "--max", str(max_results)])

    def today_calendar(self) -> Any:
        """Return today's Google Calendar events as parsed JSON."""
        return self._run_json(["calendar", "events", "--today"])

    def get_thread(self, thread_id: str, *, full: bool = False) -> Any:
        """Fetch one Gmail thread, with sanitized content and no attachments."""
        if not thread_id.strip():
            raise ValueError("thread_id nie może być pusty.")
        command = ["gmail", "thread", "get", thread_id, "--sanitize-content"]
        if full:
            command.append("--full")
        return self._run_json(command)

    def morning_snapshot(self, *, gmail_query: str, max_emails: int = 10) -> dict[str, Any]:
        """Fetch the two controlled data sources required by Poranny Briefing."""
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "account": self.account,
            "mode": "read-only",
            "gmail": self.search_mail(gmail_query, max_results=max_emails),
            "calendar": self.today_calendar(),
        }

    def _run_json(self, command: Sequence[str]) -> Any:
        # Every execution is constrained to read-only gog commands. `--gmail-no-send`
        # remains in place even though this adapter has no draft/send method.
        args = [
            self.binary,
            "--account",
            self.account,
            "--readonly",
            "--gmail-no-send",
            "--no-input",
            "--json",
            "--wrap-untrusted",
            *command,
        ]
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as error:
            raise GogError(f"Nie znaleziono binarki '{self.binary}'.") from error
        except subprocess.TimeoutExpired as error:
            raise GogError("gog nie odpowiedział w ciągu 30 sekund.") from error

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "brak szczegółów"
            raise GogError(f"gog zakończył się błędem ({completed.returncode}): {detail}")

        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise GogError("gog nie zwrócił poprawnego JSON-a.") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Gmail + Calendar adapter through gogcli")
    parser.add_argument("--account", default=os.getenv("GOG_ACCOUNT"), help="Google account authorized in gog")
    parser.add_argument("--gog-bin", default=os.getenv("GOG_BIN", "gog"), help="Path or name of gog binary")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="Verify gog installation and OAuth authorization")

    mail = commands.add_parser("mail", help="Search Gmail")
    mail.add_argument("--query", required=True, help="Gmail search query")
    mail.add_argument("--max-results", type=int, default=10)

    commands.add_parser("calendar", help="Get today's Calendar events")

    thread = commands.add_parser("thread", help="Get one sanitized Gmail thread")
    thread.add_argument("--thread-id", required=True, help="Thread ID returned by the mail command")
    thread.add_argument("--full", action="store_true", help="Return full sanitized message bodies")

    morning = commands.add_parser("morning", help="Get controlled inputs for Poranny Briefing")
    morning.add_argument("--query", default="in:inbox is:unread newer_than:14d")
    morning.add_argument("--max-emails", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.account:
        print("Ustaw --account albo zmienną środowiskową GOG_ACCOUNT.", file=sys.stderr)
        return 2

    client = GogWorkspaceClient(account=args.account, binary=args.gog_bin)
    try:
        if args.command == "health":
            result: Any = client.healthcheck()
        elif args.command == "mail":
            result = client.search_mail(args.query, max_results=args.max_results)
        elif args.command == "calendar":
            result = client.today_calendar()
        elif args.command == "thread":
            result = client.get_thread(args.thread_id, full=args.full)
        else:
            result = client.morning_snapshot(gmail_query=args.query, max_emails=args.max_emails)
    except (GogError, ValueError) as error:
        print(f"Błąd: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
