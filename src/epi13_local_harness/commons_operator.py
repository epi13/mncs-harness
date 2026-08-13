"""Human operator facade over the existing controller-local Commons MCP seam."""

from __future__ import annotations

from typing import Any

from .commons import CommonsSession


class CommonsOperatorService:
    """Bounded read/write operations shared by CLI and TUI.

    Returned Commons content remains inert untrusted JSON.  This facade never
    converts record text or WorkRequests into Harness tool invocations.
    """

    def __init__(self, session: CommonsSession) -> None:
        self.session = session

    def status(self) -> dict[str, Any]:
        status = self.session.status()
        return {
            "enabled": status.enabled,
            "ready": status.ready,
            "code": status.code,
            "detail": status.detail,
            "profile": status.profile,
            "protocol": status.protocol,
            "exchange": status.exchange,
            "store_healthy": status.store_healthy,
            "record_count": status.record_count,
            "controller_mode": status.controller_mode,
            "package_compatible": status.package_compatible,
            "service_reachable": status.service_reachable,
            "read_capable": status.read_capable,
            "publication_capable": status.publication_capable,
            "publication_configured": status.publication_configured,
            "content_trust": "UNTRUSTED",
        }

    def work(self, *, limit: int = 100) -> dict[str, Any]:
        return self._read("commons_durable_work_list", {"limit": limit})

    def opportunities(self, *, limit: int = 100) -> dict[str, Any]:
        return self._read("commons_work_list", {"limit": limit})

    def work_status(self, work_id: str) -> dict[str, Any]:
        return self._read("commons_work_status", {"workId": work_id})

    def query(self, **filters: Any) -> dict[str, Any]:
        if "open_work" in filters:
            filters["openWorkRequests"] = filters.pop("open_work")
        return self._read(
            "commons_query",
            {key: value for key, value in filters.items() if value is not None},
        )

    def get(self, digest: str) -> dict[str, Any]:
        return self._read("commons_get_record", {"digest": digest})

    def conversation(self, root: str) -> dict[str, Any]:
        return self._read("commons_conversation", {"root": root})

    def evidence(self, root: str) -> dict[str, Any]:
        return self._read("commons_evidence_trace", {"root": root})

    def sync(self, *, cursor: dict[str, Any] | None = None, limit: int = 1000) -> dict[str, Any]:
        arguments: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            arguments["cursor"] = cursor
        return self._read("commons_sync", arguments)

    def publish(self, record: dict[str, Any]) -> dict[str, Any]:
        result, success = self.session.call(
            "commons_publish_record", {"record": record}, allow_write=True
        )
        if not success:
            return {"outcome": "UNKNOWN", "result": result, "content_trust": "UNTRUSTED"}
        return {"outcome": "PASS", "result": result, "content_trust": "UNTRUSTED"}

    def _read(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result, success = self.session.call(name, arguments)
        return {
            "outcome": "PASS" if success else "UNKNOWN",
            "result": result,
            "content_trust": "UNTRUSTED",
        }
