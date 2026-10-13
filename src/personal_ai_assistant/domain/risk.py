"""Action risk levels."""

from enum import IntEnum


class RiskLevel(IntEnum):
    """Totally ordered risk of an action; higher is riskier.

    READ        search / read / summarise / compare
    DRAFT       drafts and proposals awaiting a human
    WRITE_LOW   easily reversible writes the user explicitly asked for
    WRITE_HIGH  outbound, irreversible or costly side effects
    """

    READ = 0
    DRAFT = 1
    WRITE_LOW = 2
    WRITE_HIGH = 3

    @property
    def is_write(self) -> bool:
        return self >= RiskLevel.WRITE_LOW
