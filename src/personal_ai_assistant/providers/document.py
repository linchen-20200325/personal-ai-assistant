"""Document provider contract (read-only)."""

from typing import Protocol

from personal_ai_assistant.domain.document import (
    DocumentContent,
    DocumentMeta,
    DocumentSearch,
    ReadDocument,
)
from personal_ai_assistant.domain.result import Page, ToolResult


class DocumentProvider(Protocol):
    """Expected failures (including unsupported formats) are returned as
    ``ToolResult.failed(...)``, never raised."""

    def search_documents(self, query: DocumentSearch) -> ToolResult[Page[DocumentMeta]]:
        """Most recently modified first, at most ``query.limit`` items."""
        ...

    def read_document(self, request: ReadDocument) -> ToolResult[DocumentContent]:
        """At most ``request.max_chars`` characters; ``truncated`` set when cut."""
        ...
