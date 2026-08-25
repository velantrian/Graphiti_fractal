# Experience Provenance Envelope v0

Status: bounded additive contract

This document defines a versioned provenance envelope for historical `TaskRun`
and `ToolCall` observations in Graphiti_fractal.

It is deliberately narrow.

The envelope records what was observed or supplied about a historical run. It
does **not** decide whether the run was correct, trusted, reusable, applicable,
or authorized.

## Core invariant

```text
provenance != trust
provenance != applicability
provenance != causal proof
provenance != validated lesson
provenance != authority
```

The existing Fractal boundaries remain unchanged:

```text
retrieval != evidence
success != causal proof
frequency != authority
model output != durable fact
applicability != truth
```

## Why this exists

The current experience layer already records task context, repository state,
tool calls, tests, errors, status, and timing. The provenance envelope adds a
stable place for environment/tool identity details that are useful for later
validation without changing `context_hash` or retrieval semantics.

Semantic task identity and environment identity stay separate:

```text
context_hash
    -> what kind of task/context was this?

provenance envelope
    -> what was recorded about the environment and tools?

applicability guard
    -> can recorded requirements be used under supplied current constraints?
```

No current applicability decision reads these new fields in v0.

## Versioned run envelope

`ExperienceProvenance` is optional and additive.

Fields:

- `version = experience-provenance-v0`
- `provider`
- `model`
- `runtime_id`
- `os_name`
- `environment_id`
- `capability_profile_hash`
- `trace_id`
- `parent_span_id`
- `provenance_state`

`provenance_state` is one of:

- `unknown`
- `partial`
- `complete`

The state is caller-supplied recording metadata. The ingest path does not infer
trust or authority from it.

When the envelope is omitted, persisted provenance state is `unknown`.

## Versioned tool-call provenance

Each `ToolCallEvent` may optionally carry `ToolCallProvenance`.

Fields:

- `version = tool-provenance-v0`
- `canonical_tool_id`
- `tool_version`
- `tool_schema_digest`
- `capabilities[]`
- `permission_scope[]`
- `trace_id`
- `parent_span_id`
- `provenance_state`

Capabilities and permission scopes are recorded observations only. They do not
create permission to execute anything.

## Deterministic envelope digest

The writer builds a canonical JSON envelope and stores its SHA-256 digest.

The digest is intended for lineage comparison and reproducibility. It is not a
signature and does not authenticate the caller.

The deterministic envelope contains:

- run provenance metadata;
- repository / branch / commit context already supplied to the run;
- ordered tool-call entries;
- canonical tool provenance metadata;
- an SHA-256 digest of tool arguments;
- observed exit code;
- `authoritative: false`.

Raw tool arguments, stdout, and stderr are deliberately excluded from the
provenance envelope JSON.

Important: this PR does **not** yet change the existing raw ToolCall persistence
policy. Secret/PII redaction and retention controls are a separate bounded wave.

## Persistence

The existing `TaskRun` node receives additive optional properties such as:

- `provenance_version`
- `provenance_state`
- `provenance_digest`
- `provenance_json`
- `provenance_provider`
- `provenance_model`
- `provenance_runtime_id`
- `provenance_os_name`
- `provenance_environment_id`
- `provenance_capability_profile_hash`
- `trace_id`
- `parent_span_id`

Existing `ToolCall` nodes receive additive provenance properties such as:

- `provenance_version`
- `provenance_state`
- `canonical_tool_id`
- `tool_version`
- `tool_schema_digest`
- `capabilities`
- `permission_scope`
- `args_sha256`
- `trace_id`
- `parent_span_id`

Neo4j remains the same graph store. No second memory engine is introduced.

## Backward compatibility

Legacy callers may omit all provenance fields.

The following behavior must remain unchanged:

- `context_hash` inputs and semantics;
- canonical task/run identity;
- current success/failure observation semantics;
- current environment applicability guard;
- current retrieval ranking/selection;
- promotion policy;
- durable authority boundaries.

The ingest response gains an additive provenance receipt:

```json
{
  "version": "experience-provenance-v0",
  "digest": "...",
  "state": "unknown|partial|complete",
  "authoritative": false
}
```

## Explicit non-goals for v0

This wave does not:

- compute or verify trust;
- validate causal success;
- promote experience into a lesson or skill;
- authorize tool execution;
- change the applicability guard;
- change `context_hash`;
- add a new GraphRAG or memory engine;
- perform secret/PII redaction;
- verify provider identity;
- cryptographically sign provenance;
- make evaluator output authoritative;
- authorize production behavior.

## Future bounded waves

Possible later work, each separately reviewed:

1. ingest redaction and retention policy;
2. OpenInference/OpenTelemetry-compatible trace mapping;
3. keyset/snapshot pagination for constrained scans;
4. optional version/schema/permission applicability checks;
5. persistent-memory poisoning benchmark;
6. contrastive success/failure evidence packages;
7. derived lesson candidates in quarantine/DRY_RUN only.

None of these future items are authorized by this document.
