"""E-mail tools. There is deliberately no send tool yet."""

from personal_ai_assistant.domain.email import (
    EmailDraft,
    EmailMessage,
    EmailSearch,
    MessageRef,
    SavedDraft,
)
from personal_ai_assistant.domain.result import Page, ToolResult
from personal_ai_assistant.domain.risk import RiskLevel
from personal_ai_assistant.providers.email import EmailProvider
from personal_ai_assistant.tools.base import Tool, ToolSpec


class SearchEmailTool(Tool[EmailSearch]):
    spec = ToolSpec(
        name="email.search",
        description="Search e-mail by keywords and/or time window.",
        base_risk=RiskLevel.READ,
        input_model=EmailSearch,
    )

    def __init__(self, provider: EmailProvider) -> None:
        self._provider = provider

    def run(self, params: EmailSearch) -> ToolResult[Page[EmailMessage]]:
        return self._provider.search_messages(params)


class GetEmailTool(Tool[MessageRef]):
    spec = ToolSpec(
        name="email.get",
        description="Read one e-mail message including its body.",
        base_risk=RiskLevel.READ,
        input_model=MessageRef,
    )

    def __init__(self, provider: EmailProvider) -> None:
        self._provider = provider

    def run(self, params: MessageRef) -> ToolResult[EmailMessage]:
        return self._provider.get_message(params.message_id)


class CreateEmailDraftTool(Tool[EmailDraft]):
    spec = ToolSpec(
        name="email.create_draft",
        description="Save an e-mail draft for the user to review. Does not send.",
        base_risk=RiskLevel.DRAFT,
        input_model=EmailDraft,
    )

    def __init__(self, provider: EmailProvider) -> None:
        self._provider = provider

    def run(self, params: EmailDraft) -> ToolResult[SavedDraft]:
        return self._provider.create_draft(params)
