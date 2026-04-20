from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ParseContext, ParsedDocument
from ..utils import normalize_for_match


class BaseProfile(ABC):
    profile_id = ""
    insurer = ""
    display_name = ""
    keywords: tuple[str, ...] = ()
    priority = 0
    prefer_ocr_even_for_digital = False

    def match_score(self, text: str) -> tuple[int, list[str]]:
        upper_text = normalize_for_match(text)
        markers: list[str] = []
        score = self.priority
        for keyword in self.keywords:
            if normalize_for_match(keyword) in upper_text:
                markers.append(keyword)
                score += max(20, len(keyword) * 4)
        return score, markers

    @abstractmethod
    def parse(self, text: str, context: ParseContext) -> ParsedDocument:
        raise NotImplementedError
