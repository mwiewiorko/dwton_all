"""Optional real, read-only replacement for dwthon_google_fake.

Requires a preconfigured gog account. This file never exposes write methods.
"""
from __future__ import annotations

# `google_workspace_adapter.py` is shipped next to this file in the workshop
# material. It is a copy of the tested read-only gog adapter.
from google_workspace_adapter import GogWorkspaceClient


class WorkspaceClient(GogWorkspaceClient):
    def __init__(self, profile: str | None = None, account: str = "", **_: object):
        if not account:
            raise ValueError("Real mode requires account='you@example.com' and configured gog credentials.")
        super().__init__(account=account)
        self.profile = profile or account
