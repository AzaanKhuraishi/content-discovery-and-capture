from typing import Protocol
from pathlib import Path
from ..domain import Scope, Budget, DiscoveryBatch, CaptureResult


class SourceAdapter(Protocol):
    kind: str

    def discover(self, source: dict, scope: Scope, cursor: dict, budget: Budget) -> DiscoveryBatch: ...

    def methods(self, observation: dict) -> list[str]: ...

    def capture(self, observation: dict, scope: Scope, method: str, destination: Path,
                max_bytes: int, timeout: int) -> CaptureResult: ...


class ExternalDiscoveryProvider(Protocol):
    """V2 boundary only. No implementation, routing or search UI ships in V1."""
    def candidates(self, approved_search_brief: dict) -> list[dict]: ...
