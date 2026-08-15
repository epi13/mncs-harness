"""Controller-local MNCS Commons MCP mediation and evidence publication."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

# Capability-driven Commons tool contract. Commons may advertise additional
# operator-admin tools; the model-facing surface accepts only these classes.
CONSUMER_READ_TOOLS = frozenset(
    {
        "commons_describe",
        "commons_validate_record",
        "commons_get_record",
        "commons_query",
        "commons_sync",
        "commons_conversation",
        "commons_work_list",
        "commons_work_status",
        "commons_durable_work_list",
        "commons_evidence_trace",
    }
)
REQUIRED_CONSUMER_TOOLS = frozenset(
    {
        "commons_describe",
        "commons_validate_record",
        "commons_get_record",
        "commons_query",
        "commons_sync",
        "commons_conversation",
        "commons_work_list",
        "commons_evidence_trace",
    }
)
MODEL_PUBLICATION_TOOLS = frozenset(
    {
        "commons_publish_record",
        "commons_submit_work_record",
        "commons_transition_work_record",
    }
)
REQUIRED_PUBLICATION_TOOLS = frozenset({"commons_publish_record"})
OPERATOR_ADMIN_TOOLS = frozenset(
    {
        "commons_retention_status",
        "commons_compact_store",
    }
)
# Intentional aliases for retired names. Empty until a rename needs a
# documented compatibility window; do not scatter legacy names elsewhere.
TOOL_ALIASES: dict[str, str] = {}
EXPECTED_TOOLS = CONSUMER_READ_TOOLS | MODEL_PUBLICATION_TOOLS
WRITE_TOOLS = MODEL_PUBLICATION_TOOLS
DURABLE_WORK_TOOLS = frozenset(
    {
        "commons_work_status",
        "commons_durable_work_list",
        "commons_submit_work_record",
        "commons_transition_work_record",
    }
)


def _canonical_tool_name(name: str) -> str:
    return TOOL_ALIASES.get(name, name)


def _schema_name(schema: dict[str, Any]) -> str:
    return _canonical_tool_name(str(schema.get("function", {}).get("name") or ""))


def _model_facing_schemas(
    consumer_schemas: Any,
    operator_schemas: Any = (),
) -> list[dict[str, Any]]:
    """Project Commons service tools onto the model-facing capability surface."""

    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schema in (*tuple(consumer_schemas or ()), *tuple(operator_schemas or ())):
        if not isinstance(schema, dict):
            continue
        name = _schema_name(schema)
        if name in OPERATOR_ADMIN_TOOLS or name in seen:
            continue
        if name not in EXPECTED_TOOLS:
            raise CommonsError(
                "COMMONS_TOOLSET_MISMATCH",
                f"Commons advertised an unexpected model-facing tool: {name}",
            )
        function = dict(schema.get("function") or {})
        function["name"] = name
        accepted.append({**schema, "function": function})
        seen.add(name)
    _validate_model_facing_names(seen, publication=bool(MODEL_PUBLICATION_TOOLS & seen))
    return accepted


def _validate_model_facing_names(names: set[str], *, publication: bool = False) -> None:
    del publication
    canonical = {_canonical_tool_name(name) for name in names} - OPERATOR_ADMIN_TOOLS
    if canonical - EXPECTED_TOOLS or not REQUIRED_CONSUMER_TOOLS <= canonical:
        raise CommonsError(
            "COMMONS_TOOLSET_MISMATCH",
            "Commons MCP tool set is missing or unexpected",
        )


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
    controller_mode: str | None = None
    package_compatible: bool = False
    service_reachable: bool = False
    read_capable: bool = False
    publication_capable: bool = False
    publication_configured: bool = False


ExchangeRunner = Callable[[str, dict[str, Any]], CommonsExchange]


class CommonsSession:
    """Mediate bounded Commons tools through the configured controller boundary.

    The normal path is the persistent consumer socket; fixed stdio remains an
    explicit compatibility mode. The model sees schemas and results only. It
    cannot select a socket, executable, store path, domain, environment, or
    service/process lifecycle.
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
        self._service_client: Any | None = None
        self._admin_client: Any | None = None

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
        if self.config.controller_mode == "service":
            return self._initialize_service()
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
                controller_mode="stdio",
                package_compatible=True,
                service_reachable=True,
                read_capable=True,
                publication_capable=True,
                publication_configured=bool(self.config.allow_model_publication),
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

    def _initialize_service(self) -> CommonsStatus:
        try:
            from mncs_commons.local_service import (
                SERVICE_PROTOCOL,
                CommonsAdminClient,
                CommonsClient,
            )

            client = CommonsClient.connect(
                self.config.service_socket,
                timeout=float(self.config.call_timeout_seconds),
            )
            status = client.status()
            service_descriptor = client.descriptor()
            descriptor = client.describe()
            if status.get("serviceProtocol") != SERVICE_PROTOCOL:
                raise CommonsError(
                    "COMMONS_PROTOCOL_MISMATCH", "Commons local-service protocol is incompatible"
                )
            consumer_schemas = service_descriptor.get("consumerTools")
            operator_schemas = service_descriptor.get("operatorTools")
            if not isinstance(consumer_schemas, list) or not isinstance(operator_schemas, list):
                raise CommonsError(
                    "COMMONS_PROTOCOL_MISMATCH", "Commons service tool projection is invalid"
                )
            schemas = _model_facing_schemas(
                consumer_schemas,
                operator_schemas if self.config.allow_model_publication else (),
            )
            publication_capable = status.get("operatorPublicationCapable") is True
            if self.config.allow_model_publication:
                admin = CommonsAdminClient.connect(
                    self.config.operator_socket,
                    timeout=float(self.config.call_timeout_seconds),
                )
                admin.status()
                self._admin_client = admin
            self._service_client = client
            exchange = CommonsExchange(
                "mncs-commons",
                tuple(dict(schema) for schema in schemas),
                descriptor,
                descriptor,
            )
            self._accept_exchange(exchange)
            self._status = CommonsStatus(
                enabled=True,
                ready=status.get("storeHealthy") is True,
                code=(
                    "COMMONS_READY"
                    if status.get("storeHealthy") is True
                    else "COMMONS_STORE_INVALID"
                ),
                detail=(
                    "persistent controller-local Commons service is ready"
                    if status.get("storeHealthy") is True
                    else "persistent Commons service reports an unhealthy store"
                ),
                profile=str(descriptor["profile"]["version"]),
                protocol=str(descriptor["recordVersions"][0]),
                exchange=str(descriptor["exchangeVersion"]),
                store_healthy=status.get("storeHealthy") is True,
                record_count=(
                    int(status["recordCount"])
                    if isinstance(status.get("recordCount"), int)
                    else None
                ),
                controller_mode="service",
                package_compatible=True,
                service_reachable=True,
                read_capable=status.get("consumerReadCapable") is True,
                publication_capable=publication_capable,
                publication_configured=bool(self.config.allow_model_publication),
            )
        except CommonsError as exc:
            self._status = CommonsStatus(
                True,
                False,
                exc.code,
                exc.detail,
                controller_mode="service",
                package_compatible=True,
                publication_configured=bool(self.config.allow_model_publication),
            )
        except ImportError as exc:
            self._status = CommonsStatus(
                True,
                False,
                "COMMONS_UNAVAILABLE",
                f"optional Commons integration is unavailable: {exc}",
                controller_mode="service",
                publication_configured=bool(self.config.allow_model_publication),
            )
        except Exception as exc:
            self._status = CommonsStatus(
                True,
                False,
                "COMMONS_SERVICE_UNREACHABLE",
                self._root_exception(exc),
                controller_mode="service",
                package_compatible=True,
                service_reachable=False,
                publication_configured=bool(self.config.allow_model_publication),
            )
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
            if self.config.controller_mode == "service":
                if self._admin_client is None:
                    from mncs_commons.local_service import CommonsAdminClient

                    self._admin_client = CommonsAdminClient.connect(
                        self.config.operator_socket,
                        timeout=float(self.config.call_timeout_seconds),
                    )
                if translated.record is None:
                    raise CommonsError(
                        "COMMONS_FABRIC_TRANSLATION_INVALID",
                        "Fabric translation did not produce a record",
                    )
                return self._admin_client.publish(translated.record)
            from mncs_commons.application import CommonsApplication
            from mncs_commons.store import CommonsStore

            return CommonsApplication(CommonsStore(self.store_path)).ingest_adapter_result(
                translated, publish=True, domain=self.config.domain
            )
        except CommonsError:
            raise
        except Exception as exc:
            raise CommonsError("COMMONS_EVIDENCE_PUBLICATION_FAILED", str(exc)) from exc

    def _exchange(self, name: str, arguments: dict[str, Any]) -> CommonsExchange:
        runner = self._exchange_runner or (
            self._service_exchange
            if self.config.controller_mode == "service"
            else self._native_exchange
        )
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

    def _service_exchange(self, name: str, arguments: dict[str, Any]) -> CommonsExchange:
        client = self._service_client
        if client is None:
            raise CommonsError("COMMONS_SERVICE_UNREACHABLE", "Commons service is not connected")
        operations = {
            "commons_describe": lambda: client.describe(),
            "commons_validate_record": lambda: client.validate(arguments.get("record", {})),
            "commons_get_record": lambda: client.get(arguments.get("digest", "")),
            "commons_query": lambda: client.query(**arguments),
            "commons_sync": lambda: client.sync(
                arguments.get("cursor"),
                limit=arguments.get("limit", 1000),
                kind=arguments.get("kind"),
            ),
            "commons_conversation": lambda: client.conversation(
                arguments.get("root", ""),
                depth=arguments.get("depth", 2),
                max_nodes=arguments.get("maxNodes", 1000),
            ),
            "commons_work_list": lambda: client.work(
                limit=arguments.get("limit", 100), domain=arguments.get("domain")
            ),
            "commons_work_status": lambda: client.work_status(
                arguments.get("workId", "")
            ),
            "commons_durable_work_list": lambda: client.work_list(
                states=arguments.get("states"), limit=arguments.get("limit", 100)
            ),
            "commons_evidence_trace": lambda: client.evidence(
                arguments.get("root", ""),
                depth=arguments.get("depth", 3),
                max_nodes=arguments.get("maxNodes", 1000),
            ),
            "commons_publish_record": lambda: self._admin_publish(arguments),
            "commons_submit_work_record": lambda: self._admin_submit_work(arguments),
            "commons_transition_work_record": lambda: self._admin_transition_work(arguments),
        }
        operation = operations.get(name)
        if operation is None:
            raise CommonsError("COMMONS_UNKNOWN_TOOL", f"unsupported Commons tool: {name}")
        result = operation()
        if not isinstance(result, dict):
            raise CommonsError("COMMONS_PROTOCOL_MISMATCH", "Commons result is not an object")
        encoded = json.dumps(
            result, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(encoded) > int(self.config.max_response_bytes):
            raise CommonsError(
                "COMMONS_SERVICE_RESPONSE_OVERSIZED",
                "Commons service response exceeded policy",
            )
        return CommonsExchange(
            "mncs-commons",
            tuple(self.schemas()),
            dict(self._descriptor or client.describe()),
            result,
        )

    def _admin_publish(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._admin_client is None:
            raise CommonsError(
                "COMMONS_TOOL_DENIED", "Commons publication is not configured"
            )
        record = arguments.get("record")
        if not isinstance(record, dict):
            raise CommonsError("COMMONS_INVALID_ARGUMENTS", "record must be an object")
        participant = arguments.get("participant")
        if participant is not None and not isinstance(participant, dict):
            raise CommonsError("COMMONS_INVALID_ARGUMENTS", "participant must be an object")
        return self._admin_client.publish(record, participant=participant)

    def _admin_submit_work(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._admin_client is None:
            raise CommonsError(
                "COMMONS_TOOL_DENIED", "Commons work publication is not configured"
            )
        request = arguments.get("request")
        if not isinstance(request, dict):
            raise CommonsError("COMMONS_INVALID_ARGUMENTS", "request must be an object")
        return self._admin_client.submit_work(request)

    def _admin_transition_work(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._admin_client is None:
            raise CommonsError(
                "COMMONS_TOOL_DENIED", "Commons work publication is not configured"
            )
        work_id = arguments.get("workId")
        transition = arguments.get("transition")
        if not isinstance(work_id, str) or not isinstance(transition, dict):
            raise CommonsError(
                "COMMONS_INVALID_ARGUMENTS", "workId and transition are required"
            )
        return self._admin_client.transition_work(work_id, transition)

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
            environment = dict(os.environ)
            # Stdio compatibility is sometimes launched by a host Python that
            # can import ELH from a checkout but cannot see the optional Commons
            # distribution. Bind the resolved package root explicitly rather than
            # relying on the caller's venv or current working directory.
            spec = importlib.util.find_spec("mncs_commons")
            roots: list[str] = []
            if spec is not None:
                locations = spec.submodule_search_locations
                if locations:
                    roots.extend(str(Path(location).resolve().parent) for location in locations)
                elif spec.origin:
                    roots.append(str(Path(spec.origin).resolve().parent.parent))
            if roots:
                existing = environment.get("PYTHONPATH", "")
                environment["PYTHONPATH"] = os.pathsep.join(
                    dict.fromkeys([*roots, *([existing] if existing else [])])
                )
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
                env=environment,
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
                    timeout_value: float | timedelta
                    try:
                        mcp_major = int(version("mcp").split(".", 1)[0])
                    except (PackageNotFoundError, ValueError):
                        mcp_major = 1
                    timeout_value = (
                        float(self.config.call_timeout_seconds)
                        if mcp_major >= 2
                        else timedelta(seconds=float(self.config.call_timeout_seconds))
                    )
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timeout_value,
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
                                    read_timeout_seconds=timeout_value,
                                )
                            )
                        schemas = tuple(
                            {
                                "type": "function",
                                "function": {
                                    "name": tool.name,
                                    "description": tool.description or "",
                                    "parameters": dict(
                                        getattr(tool, "inputSchema", None)
                                        or getattr(tool, "input_schema", {})
                                    ),
                                },
                            }
                            for tool in listed.tools
                        )
                        return CommonsExchange(
                            server_name=str(
                                (
                                    getattr(initialized, "serverInfo", None)
                                    or getattr(initialized, "server_info", None)
                                ).name
                            ),
                            schemas=schemas,
                            descriptor=self._tool_payload(descriptor_result),
                            result=self._tool_payload(requested),
                            is_error=bool(
                                getattr(requested, "isError", False)
                                or getattr(requested, "is_error", False)
                            ),
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
        accepted = _model_facing_schemas(exchange.schemas)
        self._schemas = {str(schema["function"]["name"]): schema for schema in accepted}
        self._descriptor = dict(exchange.descriptor)

    def _validate_exchange(self, exchange: CommonsExchange) -> None:
        if exchange.server_name != "mncs-commons":
            raise CommonsError("COMMONS_PROTOCOL_MISMATCH", "unexpected MCP server identity")
        names = {_schema_name(item) if isinstance(item, dict) else "" for item in exchange.schemas}
        _validate_model_facing_names(names)
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
            or interface.get("binding")
            != ("local-service" if self.config.controller_mode == "service" else "stdio-mcp")
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
