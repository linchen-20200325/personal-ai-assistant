"""E-mail provider contract (data access only)."""

from typing import Protocol

from personal_ai_assistant.domain.email import EmailDraft, EmailMessage, EmailSearch, SavedDraft
from personal_ai_assistant.domain.result import Page, ToolResult


class EmailProvider(Protocol):
    """Expected failures are returned as ``ToolResult.failed(...)``, never raised
    and never reported as an empty success. Nothing here sends mail."""

    def search_messages(self, query: EmailSearch) -> ToolResult[Page[EmailMessage]]:
        """Newest first, at most ``query.limit`` items."""
        ...

    def get_message(self, message_id: str) -> ToolResult[EmailMessage]:
        """Full message including ``body_text`` when the provider can extract it."""
        ...

    def create_draft(self, draft: EmailDraft) -> ToolResult[SavedDraft]: ...
