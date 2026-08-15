# Routing and escalation

## Why deterministic routing first

Calling a model merely to choose another model adds latency, consumes context, and
creates a second unverified judgment. The initial router therefore uses observable
request features and produces a rationale that can be tested.

## Default first-choice policy

### E2B

Selected for short, read-only requests such as explanation, classification,
summarization, and workspace inspection. Its configured tool set excludes writes and
command execution.

Default escalation:

```text
E2B -> E4B -> 12B reviewer
```

### E4B

Selected when the request asks to edit files, execute commands, run tests, or perform
ordinary repository work.

For coding tasks with the specialist enabled:

```text
E4B -> coder role (Fabric-discovered implementation) -> reviewer role
```

Model tags are operator preferences. Automatic placement uses provider claims,
observed evidence, resources, and policy. See [MODEL_AGNOSTICISM.md](MODEL_AGNOSTICISM.md).

For non-code tool tasks:

```text
E4B -> 12B reviewer
```

### 12B reviewer

Selected immediately for multimodal input, high-risk intent, long or structurally
complex requests, many referenced files, and architectural work.

Routing a high-risk request to the reviewer does not grant additional permission.
The same hard policy boundaries still apply.

## What triggers escalation

An attempt escalates when any of the following occurs and the corresponding setting
is enabled:

- deterministic syntax or format verification fails;
- a requested tool is blocked, denied, or fails;
- the Ollama request fails;
- the model exceeds the bounded tool loop;
- the model returns no final content.

Passing verification ends the cascade. The coding specialist is not invoked solely
for a second opinion when the E4B attempt already produced a verifiable result.

## Evaluation discipline

Before changing a routing rule:

1. add representative cases to `evals/tasks.jsonl`;
2. run `elh eval`;
3. adjust one feature or threshold;
4. inspect false-positive and false-negative routes;
5. measure actual model latency and verifier success in `elh metrics`.

Longer term, routing can incorporate measured local performance, but a learned router
should still return explicit features and preserve a deterministic safety override.
