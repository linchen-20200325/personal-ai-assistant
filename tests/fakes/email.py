"""In-memory e-mail provider with synthetic data only."""

from collections.abc import Iterable

from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.email import EmailDraft, EmailMessage, EmailSearch, SavedDraft
from personal_ai_assistant.domain.result import DataStatus, Page, Provenance, ToolResult

SOURCE = "fake_email"


def _matches(message: EmailMessage, query: EmailSearch) -> bool:
    if query.window is not None:
        # Unknown receipt time cannot be confirmed to fall inside the window.
        if message.received_at is None:
            return False
        if not query.window.start <= message.received_at < query.window.end:
            return False
    if query.text is not None:
        haystack = " ".join(
            filter(None, (message.subject, message.snippet, message.body_text))
        ).casefold()
        return query.text.casefold() in haystack
    return True


class FakeEmailProvider:
    def __init__(
        self, messages: Iterable[EmailMessage] = (), *, clock: Clock, outage: str | None = None
    ) -> None:
        self.messages = list(messages)
        self.drafts: list[SavedDraft] = []
        self.outage = outage
        self._clock = clock

    def _provenance(self, ref: str | None = None) -> Provenance:
        return Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=ref)

    def search_messages(self, query: EmailSearch) -> ToolResult[Page[EmailMessage]]:
        if self.outage:
            return ToolResult.failed(self.outage)
        hits = sorted(
            (m for m in self.messages if _matches(m, query)),
            key=lambda m: (m.received_at is not None, m.received_at),
            reverse=True,
        )
        # Search results carry no bodies, like real list endpoints.
        items = tuple(m.model_copy(update={"body_text": None}) for m in hits[: query.limit])
        return ToolResult.ok(
            Page(items=items, has_more=len(hits) > query.limit), self._provenance()
        )

    def get_message(self, message_id: str) -> ToolResult[EmailMessage]:
        if self.outage:
            return ToolResult.failed(self.outage)
        for message in self.messages:
            if message.message_id == message_id:
                return ToolResult.ok(message, self._provenance(message_id))
        return ToolResult.failed("message not found", status=DataStatus.UNAVAILABLE)

    def create_draft(self, draft: EmailDraft) -> ToolResult[SavedDraft]:
        if self.outage:
            return ToolResult.failed(self.outage)
        saved = SavedDraft(draft_id=f"draft-{len(self.drafts) + 1}", draft=draft)
        self.drafts.append(saved)
        return ToolResult.ok(saved, self._provenance(saved.draft_id))
