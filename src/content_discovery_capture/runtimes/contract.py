from dataclasses import dataclass, field
from typing import Protocol
from pathlib import Path
from ..domain import Scope


@dataclass
class WebResponse:
    body: bytes
    url: str
    headers: dict = field(default_factory=dict)
    complete: bool = True
    limitations: list[str] = field(default_factory=list)
    actions: int = 1
    method: str = "http"


class BrowserProvider(Protocol):
    def capabilities(self) -> dict: ...
    def inspect(self, location: str, scope: Scope, max_bytes: int, max_actions: int) -> WebResponse: ...
    def capture(self, location: str, scope: Scope, destination: Path, max_bytes: int,
                timeout: int, method: str): ...
