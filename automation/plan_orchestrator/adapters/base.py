from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

from ..models import NormalizedPlan


class PlanAdapter(Protocol):
    adapter_id: str

    def normalize(self, parsed: dict[str, Any], playbook_path: str | Path) -> NormalizedPlan:
        ...

    def canonicalize_repo_text(self, text: str) -> str:
        ...


class BasePlanAdapter(ABC):
    adapter_id: str

    def canonicalize_repo_text(self, text: str) -> str:
        return text

    @abstractmethod
    def normalize(self, parsed: dict[str, Any], playbook_path: str | Path) -> NormalizedPlan:
        raise NotImplementedError
