"""Document tools (read-only)."""

from personal_ai_assistant.domain.document import (
    DocumentContent,
    DocumentMeta,
    DocumentSearch,
    ReadDocument,
)
from personal_ai_assistant.domain.result import Page, ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.providers.document import DocumentProvider
from personal_ai_assistant.tools.base import Tool, ToolSpec


class SearchDocumentsTool(Tool[DocumentSearch]):
    spec = ToolSpec(
        name="documents.search",
        description="Search documents by keywords.",
        base_risk=RiskLevel.READ,
        input_model=DocumentSearch,
    )

    def __init__(self, provider: DocumentProvider) -> None:
        self._provider = provider

    def run(self, params: DocumentSearch) -> ToolResult[Page[DocumentMeta]]:
        return self._provider.search_documents(params)


class ReadDocumentTool(Tool[ReadDocument]):
    spec = ToolSpec(
        name="documents.read",
        description="Read a document's text, up to max_chars characters.",
        base_risk=RiskLevel.READ,
        input_model=ReadDocument,
    )

    def __init__(self, provider: DocumentProvider) -> None:
        self._provider = provider

    def run(self, params: ReadDocument) -> ToolResult[DocumentContent]:
        return self._provider.read_document(params)
