from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class ContextSummary:
    used_tokens: int
    context_size: int
    percent: float
    exact: bool
    label: str

    def public(self) -> dict:
        return {
            "used_tokens": self.used_tokens,
            "context_size": self.context_size,
            "percent": self.percent,
            "exact": self.exact,
            "label": self.label,
        }


class ContextTracker:
    """Fast context estimation with optional exact counts supplied by server code."""

    @staticmethod
    def estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        # Practical approximation used for responsive UI updates.
        return max(1, int(len(text) / 4))

    def estimate_messages(self, messages: Iterable[dict], uploads: Iterable[dict] | None = None, context_size: int = 0) -> ContextSummary:
        total = 0
        for message in messages or []:
            total += self.estimate_text_tokens(str(message.get("content") or ""))
        for upload in uploads or []:
            total += int(upload.get("tokens") or self.estimate_text_tokens(str(upload.get("text") or "")))
        total += max(0, len(list(messages or [])) * 4)
        return self._summary(total, context_size, exact=False)

    def from_exact(self, used_tokens: int, context_size: int) -> ContextSummary:
        return self._summary(max(0, int(used_tokens or 0)), context_size, exact=True)

    @staticmethod
    def _summary(used_tokens: int, context_size: int, exact: bool) -> ContextSummary:
        size = max(0, int(context_size or 0))
        percent = round((used_tokens / size) * 100, 1) if size else 0.0
        label = f"{used_tokens:,}" if not size else f"{used_tokens:,} / {size:,}"
        return ContextSummary(used_tokens=used_tokens, context_size=size, percent=min(percent, 999.0), exact=exact, label=label)
