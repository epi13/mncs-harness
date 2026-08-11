"""Controller-local MNCS Commons MCP mediation and evidence publication."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

EXPECTED_TOOLS = frozenset(
    {
        "commons_describe",
        "commons_validate_record",
        "commons_get_record",
        "commons_query",
        "commons_sync",
        "commons_conversation",
        "commons_work_list",
        "commons_evidence_trace",
        "commons_publish_record",
    }
)
WRITE_TOOLS = frozenset({"commons_publish_record"})


class CommonsError(RuntimeError):
    """A typed controller-local Commons boundary failure."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CommonsExchange:
    server_name: str
    schemas: tuple[dict[str, Any], ...]
    descriptor: dict[str, Any]
    result: dict[str, Any]
    is_error: bool = False
    stderr: str = ""


@dataclass(frozen=True)
class CommonsStatus:
    enabled: bool
    ready: bool
    code: str
    detail: str
    profile: str | None = None
    protocol: str | None = None
    exchange: str | None = None
    store_healthy: bool = False
    record_count: int | None = None


ExchangeRunner = Callable[[str, dict[str, Any]], CommonsExchange]


class CommonsSession:
    """Launch a fixed stdio MCP server per bounded controller operation.

    The model sees schemas and results only. It cannot select the executable,
    module, store path, domain, environment, or subprocess lifecycle.
    """

    def __init__(self, config: Any, *, exchange_runner: ExchangeRunner | None = None):
        self.config = config
        self.store_path = Path(config.store_path).expanduser().resolve()
        self._exchange_runner = exchange_runner
        self._status = CommonsStatus(
            enabled=bool(config.enabled),
            ready=False,
            code="COMMONS_DISABLED" if not config.enabled else "COMMONS_INITIALIZING",
            detail="Commons is disabled" if not config.enabled else "Commons is initializing",
        )
        self._schemas: dict[str, dict[str, Any]] = {}
        self._descriptor: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @property
    def ready(self) -> bool:
        return self._status.ready

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas)) if self.ready else ()

    def initialize(self) -> CommonsStatus:
        if not self.enabled:
            return self._status
        try:
            from mncs_commons.store import CommonsStore

            store = CommonsStore(self.store_path)
            if not self.store_path.exists():
                if not self.config.auto_initialize:
                    raise CommonsError(
                        "COMMONS_STORE_MISSING", "configured Commons store is not initialized"
                    )
                store.init()
            verification = store.verify()
            if not verification.valid:
                raise CommonsError("COMMONS_STORE_INVALID", "Commons store verification failed")
            exchange = self._exchange("commons_describe", {})
            self._accept_exchange(exchange)
            self._status = CommonsStatus(
                enabled=True,
                ready=True,
                code="COMMONS_READY",
                detail="controller-local Commons MCP is ready",
                profile=str(exchange.descriptor["profile"]["version"]),
                protocol=str(exchange.descriptor["recordVersions"][0]),
                exchange=str(exchange.descriptor["exchangeVersion"]),
                store_healthy=True,
                record_count=len(store.records()),
            )
        except CommonsError as exc:
            self._status = CommonsStatus(True, False, exc.code, exc.detail)
        except ImportError as exc:
            self._status = CommonsStatus(
                True,
                False,
                "COMMONS_UNAVAILABLE",
                f"optional Commons integration is unavailable: {exc}",
            )
        except Exception as exc:
            self._status = CommonsStatus(True, False, "COMMONS_INITIALIZATION_FAILED", str(exc))
        return self._status

    def status(self) -> CommonsStatus:
        return self._status

    def schemas(self) -> tuple[dict[str, Any], ...]:
        if not self.ready:
            return ()
        return tuple(self._schemas[name] for name in sorted(self._schemas))

    def schema(self, name: str) -> dict[str, Any] | None:
        return self._schemas.get(name) if self.ready else None

    def call(
        self, name: str, arguments: dict[str, Any], *, allow_write: bool = False
    ) -> tuple[dict[str, Any], bool]:
        if not self.ready:
            raise CommonsError(self._status.code, self._status.detail)
        schema = self._schemas.get(name)
        if schema is None:
            raise CommonsError("COMMONS_UNKNOWN_TOOL", f"unsupported Commons tool: {name}")
        if name in WRITE_TOOLS and not allow_write:
            raise CommonsError("COMMONS_TOOL_DENIED", "Commons publication policy denied the write")
        parameters = schema["function"]["parameters"]
        allowed = set(parameters.get("properties", {}))
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise CommonsError(
                "COMMONS_INVALID_ARGUMENTS",
                "unexpected Commons arguments: " + ", ".join(unknown),
            )
        exchange = self._exchange(name, arguments)
        self._validate_exchange(exchange)
        return exchange.result, not exchange.is_error

    def publish_fabric_evidence(self, execution: dict[str, Any]) -> dict[str, Any]:
        """Translate and optionally publish inert Fabric evidence on the controller."""

        if not self.ready:
            raise CommonsError(self._status.code, self._status.detail)
        if not self.config.publish_fabric_evidence:
            raise CommonsError(
                "COMMONS_EVIDENCE_PUBLICATION_DISABLED",
                "controller-generated Fabric evidence publication is disabled",
            )
        try:
            from mncs_commons.adapters.fabric import from_fabric_execution
            from mncs_commons.application import CommonsApplication
            from mncs_commons.store import CommonsStore

            subject = execution.get("candidate_identity")
            if not isinstance(subject, str) or not subject:
                raise CommonsError(
                    "COMMONS_FABRIC_TRANSLATION_INVALID",
                    "Fabric execution lacks a candidate identity",
                )
            translated = from_fabric_execution(execution, subject_identity=subject)
            if not translated.valid:
                raise CommonsError(
                    "COMMONS_FABRIC_TRANSLATION_INVALID",
                    json.dumps(translated.as_dict(), sort_keys=True),
                )
            return CommonsApplication(CommonsStore(self.store_path)).ingest_adapter_result(
                translated, publish=True, domain=self.config.domain
            )
        except CommonsError:
            raise
        except Exception as exc:
            raise CommonsError("COMMONS_EVIDENCE_PUBLICATION_FAILED", str(exc)) from exc

    def _exchange(self, name: str, arguments: dict[str, Any]) -> CommonsExchange:
        runner = self._exchange_runner or self._native_exchange
        try:
            return runner(name, arguments)
        except CommonsError:
            raise
        except Exception as exc:
            detail = self._root_exception(exc)
            code = (
                "COMMONS_MCP_TIMEOUT"
                if "timeout" in detail.lower() or "deadline" in detail.lower()
                else "COMMONS_MCP_UNAVAILABLE"
            )
            raise CommonsError(code, detail) from exc

    def _native_exchange(self, name: str, arguments: dict[str, Any]) -> CommonsExchange:
        try:
            import anyio
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as exc:
            raise CommonsError(
                "COMMONS_MCP_UNAVAILABLE", "MCP client support is not installed"
            ) from exc

        async def run(stderr: Any) -> CommonsExchange:
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "mncs_commons.mcp_server",
                    "--store",
                    str(self.store_path),
                    "--domain",
                    str(self.config.domain),
                ],
            )
            total_bound = (
                float(self.config.startup_timeout_seconds)
                + float(self.config.call_timeout_seconds)
                + 5.0
            )
            # The outer bound includes subprocess teardown. Inner bounds retain
            # phase-specific diagnostics for startup and the requested call.
            with anyio.fail_after(total_bound):
                async with stdio_client(params, errlog=stderr) as (
                    read_stream,
                    write_stream,
                ):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=float(self.config.call_timeout_seconds),
                    ) as session:
                        with anyio.fail_after(float(self.config.startup_timeout_seconds)):
                            initialized = await session.initialize()
                            listed = await session.list_tools()
                            descriptor_result = await session.call_tool(
                                "commons_describe", {}
                            )
                        with anyio.fail_after(float(self.config.call_timeout_seconds)):
                            requested = (
                                descriptor_result
                                if name == "commons_describe"
                                else await session.call_tool(
                                    name,
                                    arguments,
                                    read_timeout_seconds=float(
                                        self.config.call_timeout_seconds
                                    ),
                                )
                            )
                        schemas = tuple(
                            {
                                "type": "function",
                                "function": {
                                    "name": tool.name,
                                    "description": tool.description or "",
                                    "parameters": dict(tool.input_schema),
                                },
                            }
                            for tool in listed.tools
                        )
                        return CommonsExchange(
                            server_name=str(initialized.server_info.name),
                            schemas=schemas,
                            descriptor=self._tool_payload(descriptor_result),
                            result=self._tool_payload(requested),
                            is_error=bool(requested.is_error),
                        )

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
            try:
                exchange = anyio.run(run, stderr)
            except Exception as exc:
                stderr.seek(0)
                diagnostic = stderr.read(4096).strip()
                detail = self._root_exception(exc)
                if diagnostic:
                    detail = f"{detail}; stderr: {diagnostic}"
                code = (
                    "COMMONS_MCP_TIMEOUT"
                    if "timeout" in detail.lower()
                    or "deadline" in detail.lower()
                    or type(exc).__name__ == "TimeoutError"
                    else "COMMONS_MCP_UNAVAILABLE"
                )
                raise CommonsError(code, detail) from exc
            stderr.seek(0)
            diagnostic = stderr.read(4096).strip()
        return CommonsExchange(
            exchange.server_name,
            exchange.schemas,
            exchange.descriptor,
            exchange.result,
            exchange.is_error,
            diagnostic,
        )

    def _tool_payload(self, result: Any) -> dict[str, Any]:
        content = getattr(result, "content", None)
        if not isinstance(content, list) or len(content) != 1:
            raise CommonsError("COMMONS_MCP_MALFORMED", "MCP tool returned invalid content")
        text = getattr(content[0], "text", None)
        if not isinstance(text, str):
            raise CommonsError("COMMONS_MCP_MALFORMED", "MCP tool did not return text")
        if len(text.encode("utf-8")) > int(self.config.max_response_bytes):
            raise CommonsError("COMMONS_MCP_RESPONSE_OVERSIZED", "MCP response exceeded policy")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CommonsError("COMMONS_MCP_MALFORMED", "MCP response was not JSON") from exc
        if not isinstance(value, dict):
            raise CommonsError("COMMONS_MCP_MALFORMED", "MCP response was not an object")
        return value

    def _accept_exchange(self, exchange: CommonsExchange) -> None:
        self._validate_exchange(exchange)
        self._schemas = {
            str(schema["function"]["name"]): schema for schema in exchange.schemas
        }
        self._descriptor = dict(exchange.descriptor)

    def _validate_exchange(self, exchange: CommonsExchange) -> None:
        if exchange.server_name != "mncs-commons":
            raise CommonsError("COMMONS_PROTOCOL_MISMATCH", "unexpected MCP server identity")
        names = {str(item.get("function", {}).get("name")) for item in exchange.schemas}
        if names != EXPECTED_TOOLS:
            raise CommonsError(
                "COMMONS_TOOLSET_MISMATCH", "Commons MCP tool set is missing or unexpected"
            )
        descriptor = exchange.descriptor
        profile = descriptor.get("profile")
        interface = descriptor.get("interface")
        security = descriptor.get("securityProfile")
        if (
            descriptor.get("serviceDescriptorVersion")
            != "commons.mncs.dev/service/v0alpha1"
            or descriptor.get("recordVersions") != ["commons.mncs.dev/v0alpha1"]
            or descriptor.get("exchangeVersion") != "commons.mncs.dev/exchange/v0alpha1"
            or not isinstance(profile, dict)
            or profile.get("version") != "commons.mncs.dev/node/local-agent/v0alpha1"
            or profile.get("executionAuthority") != "none"
            or profile.get("trustDomain") != self.config.domain
            or not isinstance(interface, dict)
            or interface.get("binding") != "stdio-mcp"
            or interface.get("localOnly") is not True
            or not isinstance(security, dict)
            or security.get("instructionsAreUntrusted") is not True
        ):
            raise CommonsError(
                "COMMONS_PROTOCOL_MISMATCH", "Commons service descriptor is incompatible"
            )

    @staticmethod
    def _root_exception(exc: BaseException) -> str:
        current = exc
        while isinstance(current, BaseExceptionGroup) and current.exceptions:
            current = current.exceptions[0]
        return str(current) or type(current).__name__
