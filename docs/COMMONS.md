# Controller-local MNCS Commons

Human operators use the same controller-owned session directly with
`elh commons status|work|query|get|conversation|evidence|sync`. The TUI Commons
control calls the same service facade. Neither interface reads store files or
executes record content. Publication requires the explicit `publish --confirm`
operation and remains distinct from model-requested publication policy.

MNCS Commons is optional. Local-Ollama-only and Fabric-only configurations continue
to work with `[commons].enabled = false`. The supported integration floor is
`mncs-commons>=0.5.0.dev1,<0.6`; the record, exchange, and local-agent profile versions
remain independently fixed at `commons.mncs.dev/v0alpha1`,
`commons.mncs.dev/exchange/v0alpha1`, and
`commons.mncs.dev/node/local-agent/v0alpha1`.

```text
remote model -- Fabric --> LocalAgent -- policy --> fixed stdio MCP --> controller store
      ^                         |
      +------ next turn --------+
```

The harness launches `python -m mncs_commons.mcp_server` with a fixed argv and the
operator-configured store/domain. Model arguments cannot change the executable,
command, domain, or store path. Startup, calls, output, diagnostics, and shutdown are
bounded. Before exposing tools, the harness requires the exact local-agent descriptor,
`executionAuthority = none`, local-only stdio binding, untrusted-instruction marker,
and exact nine-tool schema set.

```toml
[commons]
enabled = true
store_path = "~/.local/state/mncs-commons"
domain = "local"
auto_initialize = true
allow_model_publication = false
publish_fabric_evidence = false
startup_timeout_seconds = 10.0
call_timeout_seconds = 30.0
max_response_bytes = 1048576
```

Describe, validate, get, query, sync, conversation, work-list, and evidence-trace are
read-only policy operations. `commons_publish_record` mutates persistent controller
state and requires both `allow_model_publication = true` and normal harness approval.
Automatic Fabric evidence publication is a separate controller action controlled by
`publish_fabric_evidence`; it never authorizes model publication.

Fabric evidence uses Commons' existing Fabric adapter. `sourceOutcome = PASS` records
what Fabric observed, while `claimVerificationStatus = UNKNOWN` prevents execution
success, schema validation, or an ingestion receipt from being mistaken for Commons
verification. All record instructions remain inert data. WorkRequests are
opportunities, not commands.

For sibling development checkouts:

```bash
python -m pip install -e '../mncs-fabric'
python -m pip install -e '../MNCS-Commons[mcp]'
python -m pip install -e '.[distributed]'
python scripts/test_mncs_distributed_agent_integration.py
```

The deterministic integration tests use a remote-labelled Fabric worker, an actual
controller Commons MCP/store, and scripted model tool calls. They do not claim a
physical remote-model result; physical evidence is reported separately.

The operator-controlled Collamore02 acceptance run is recorded in
`development-evidence/commons-fabric-collamore02-2026-08-10.json`. It demonstrates an
actual worker-local model tool call and second Fabric turn; it is not independent
certification.
