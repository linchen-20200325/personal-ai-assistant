"""In-memory document provider with synthetic data only."""

from collections.abc import Mapping

from personal_ai_assistant.clock import Clock
from personal_ai_assistant.domain.document import (
    DocumentContent,
    DocumentMeta,
    DocumentSearch,
    ReadDocument,
)
from personal_ai_assistant.domain.result import DataStatus, Page, Provenance, ToolResult

SOURCE = "fake_documents"


class FakeDocumentProvider:
    """``documents`` maps metadata to extracted text; ``None`` text means the
    format cannot be extracted."""

    def __init__(
        self,
        documents: Mapping[DocumentMeta, str | None] | None = None,
        *,
        clock: Clock,
        outage: str | None = None,
    ) -> None:
        self.documents = dict(documents or {})
        self.outage = outage
        self._clock = clock

    def _provenance(self, ref: str | None = None) -> Provenance:
        return Provenance(source=SOURCE, retrieved_at=self._clock(), source_ref=ref)

    def search_documents(self, query: DocumentSearch) -> ToolResult[Page[DocumentMeta]]:
        if self.outage:
            return ToolResult.failed(self.outage)
        needle = query.text.casefold() if query.text else None
        hits = sorted(
            (
                meta
                for meta, text in self.documents.items()
                if needle is None
                or needle in (meta.title or "").casefold()
                or needle in (text or "").casefold()
            ),
            key=lambda m: (m.modified_at is not None, m.modified_at),
            reverse=True,
        )
        page = Page(items=tuple(hits[: query.limit]), has_more=len(hits) > query.limit)
        return ToolResult.ok(page, self._provenance())

    def read_document(self, request: ReadDocument) -> ToolResult[DocumentContent]:
        if self.outage:
            return ToolResult.failed(self.outage)
        for meta, text in self.documents.items():
            if meta.doc_id != request.doc_id:
                continue
            if text is None:
                return ToolResult.failed(
                    "document format not supported", status=DataStatus.UNAVAILABLE
                )
            content = DocumentContent(
                doc_id=meta.doc_id,
                title=meta.title,
                mime_type=meta.mime_type,
                text=text[: request.max_chars],
                truncated=len(text) > request.max_chars,
            )
            return ToolResult.ok(content, self._provenance(meta.doc_id))
        return ToolResult.failed("document not found", status=DataStatus.UNAVAILABLE)
