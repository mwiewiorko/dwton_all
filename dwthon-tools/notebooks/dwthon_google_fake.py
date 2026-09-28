"""Deterministic Google Workspace simulation for DWthon Tools.

Swap this one import for dwthon_google_real when an advanced learner has
configured read-only gog credentials. Both expose WorkspaceClient.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WorkspaceClient:
    def __init__(self, profile: str = "founder", fixture_path: str | Path | None = None):
        path = Path(
            fixture_path
            or Path(__file__).resolve().parent.parent / "input" / "morning_profiles.json"
        )
        data = json.loads(path.read_text())
        if profile not in data["profiles"]:
            raise ValueError(f"Unknown profile: {profile}. Available: {', '.join(data['profiles'])}")
        self.profile = profile
        self.data = data["profiles"][profile]

    def search_mail(self, query: str, *, max_results: int = 10) -> dict[str, Any]:
        threads = self.data["gmail"]["threads"][:max_results]
        return {"externalContent": {"source": "google_api", "untrusted": True, "wrapped": True}, "nextPageToken": "", "threads": threads, "query": query}

    def today_calendar(self) -> dict[str, Any]:
        return {"events": self.data["calendar"]["events"], "nextPageToken": ""}

    def get_thread(self, thread_id: str, *, full: bool = False) -> dict[str, Any]:
        thread = self.data["threads"].get(thread_id)
        if not thread:
            raise ValueError(f"Thread is not available in this profile: {thread_id}")
        return {
            "downloaded": None,
            "externalContent": {"source": "google_api", "untrusted": True, "wrapped": True},
            "thread": thread if full else {"id": thread["id"], "messages": [{"snippet": thread["messages"][0]["snippet"]}]},
        }

    def morning_snapshot(self, *, gmail_query: str = "in:inbox is:unread newer_than:14d", max_emails: int = 10) -> dict[str, Any]:
        return {"generated_at": "2026-08-04T07:15:00+00:00", "account": f"{self.profile}@example.test", "mode": "read-only", "gmail": self.search_mail(gmail_query, max_results=max_emails), "calendar": self.today_calendar()}
