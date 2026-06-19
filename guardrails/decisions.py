"""Guard decision data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class Action(str, Enum):
    ALLOW = "allow"
    SANITIZE = "sanitize"
    REFUSE = "refuse"


@dataclass
class GuardDecision:
    action: Action
    cleaned_text: str
    reasons: List[str] = field(default_factory=list)

    @property
    def is_refused(self) -> bool:
        return self.action == Action.REFUSE

    @property
    def is_sanitized(self) -> bool:
        return self.action == Action.SANITIZE
