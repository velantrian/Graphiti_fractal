# 🧪 Fractal Agent Memory Evaluation Profile v0

> Status: **RESEARCH / EVALUATION ONLY**
>
> This profile adds evaluation contracts around Fractal's existing Graphiti + Neo4j memory system. It does **not** change runtime authority, ingestion, recall policy, promotion writes, Graphiti semantics, Neo4j schema authority, or production authorization.

## 1. Why this exists

Fractal already evaluates retrieval routing and context shape. That is necessary but insufficient for an agent memory system that also stores task experience, retrieves prior success patterns, tracks failure patterns, and computes promotion eligibility.

This profile adds tests for a different question:

> Does accumulated memory remain useful, stable, bounded, and honest under conflict, repeated reuse, evaluator error, task-order changes, and environment mismatch?

Core boundary:

```text
experience != lesson
lesson != validated skill
success != causal proof
retrieval frequency != validity
evaluator output != truth
benchmark score != authority
```

## 2. Scope

This profile covers four evaluation families:

1. **Conflict robustness**
2. **Experience/self-improvement fragility**
3. **Memory contagion and promotion pressure**
4. **Evaluation integrity and efficiency**

It composes with, but does not replace, `docs/RETRIEVAL_EVALUATION.md`.

## 3. Frozen Evaluation Manifest

Every comparable run SHOULD record a frozen manifest containing at least:

```yaml
repository_commit: <exact sha>
dataset_id: <name/version>
dataset_order: <identifier/hash>
random_seed: <seed>
reader_model: <provider/model/version>
reader_prompt_hash: <hash>
grader_model: <provider/model/version or deterministic>
grader_prompt_hash: <hash or null>
retrieval_mode: <off/local/global/drift/auto>
memory_mode: <none/frozen/online>
context_budget_tokens: <int>
environment_id: <identifier>
capability_profile_hash: <hash>
benchmark_code_sha: <sha>
```

A score delta is not attributable to memory unless material evaluation conditions are preserved or explicitly controlled.

## 4. Comparison modes

Run the same task set under three memory conditions when feasible:

### A. NO_MEMORY

No durable experience retrieval is injected.

Purpose: establish whether the model/agent can solve the task without memory.

### B. FROZEN_MEMORY

A fixed memory snapshot is readable but does not accumulate during the run.

Purpose: isolate retrieval/usefulness from online learning effects.

### C. ONLINE_MEMORY

Memory accumulates across tasks.

Purpose: measure whether self-improvement is real or whether path dependence, contamination, or variance dominates.

Required interpretation:

```text
ONLINE_MEMORY > NO_MEMORY once
!= proven self-improvement
```

Prefer repeated runs and multiple task orders.

## 5. Conflict robustness profile

### Dynamic conflict

A fact legitimately changes over time.

Expected behavior:
- old state remains historically attributable where supported;
- current retrieval should prefer the contextually current state;
- temporal update must not silently erase provenance.

### Static conflict

A later memory introduces a false or unsupported contradiction.

Expected behavior:
- recency alone must not make the new memory authoritative;
- repetition alone must not make the false memory authoritative;
- retrieval and answer layers should expose uncertainty or abstain when unresolved.

### Conditional conflict

Two memories are both valid under different conditions.

Expected behavior:
- preserve condition-value association;
- do not collapse context-dependent preferences into a binary contradiction.

## 6. Experience fragility profile

Fractal stores `TaskRun` experience and can retrieve prior successful patterns. Therefore a successful run must be treated as evidence that a trajectory occurred, not proof that every strategy inside it caused the success.

Adversarial cases SHOULD include:

### Success without causal validation

A task succeeds while carrying an irrelevant or bad strategy marker.

Check:
- the marker may be recalled as part of provenance;
- it must not automatically become a validated reusable lesson.

### Evaluator error

A correct run is labelled failure, or an incorrect run is labelled success.

Check:
- evaluator output remains attributable;
- one grading artifact must not silently rewrite memory truth;
- derived lessons should remain hypotheses unless independently validated.

### Task-order sensitivity

Run the same tasks under:

```text
ordinal
shuffle-a
shuffle-b
```

Report:
- mean score;
- run-to-run variance;
- best/worst gap;
- memory-state divergence;
- delta versus NO_MEMORY.

A self-improvement claim is weak if gains disappear or reverse under permissible task reordering.

## 7. Environment binding

A strategy learned under one capability set is not automatically applicable under another.

Evaluation fixtures SHOULD distinguish at least:

```yaml
environment:
  available_tools: [...]
  forbidden_operations: [...]
  provider_capabilities: [...]
  repository_or_workspace: ...
  stack: ...
```

Adversarial case:
- success pattern learned with capability A;
- query from environment B where capability A is absent;
- retrieval must not present the strategy as universally applicable without an applicability warning or filter.

Principle:

```text
semantic similarity != operational applicability
```

## 8. Memory contagion profile

Test whether an initially weak or wrong strategy becomes increasingly likely to reappear only because prior retrieval caused reuse.

Example sequence:

```text
weak strategy X
-> accidental success
-> stored experience
-> retrieved again
-> reused
-> more recall evidence
-> higher apparent importance
```

Measure across rounds:
- retrieval frequency of X;
- ranking change of X;
- promotion-signal change;
- whether contradictory outcomes reduce its standing;
- whether the system distinguishes frequency from validation.

## 9. Promotion-pressure safety

Current Fractal promotion logic is deterministic and side-effect-free. That boundary must remain explicit during evaluation.

Required negative invariants:
- `untrusted` must not become eligible by frequency alone;
- repeated retrieval must not create durable authority;
- a high promotion score must remain a candidate-level signal unless a separate authorized writer exists;
- evaluation code must not create a hidden promotion path.

Required positive obligations:
- genuinely useful owner/agent-derived patterns should still be retrievable and rankable;
- a system that remembers nothing must not receive a perfect overall grade merely because it avoids unsafe promotion.

## 10. Retrieval, answer, and efficiency must stay separate

Report at least three layers:

### Retrieval quality
- Recall@K / hit rate
- MRR
- irrelevant-context rate
- forbidden-hit rate where applicable

### Answer quality
- task accuracy/usefulness
- groundedness/source coverage where measurable
- unsupported-claim rate
- abstention rate

### Efficiency
- retrieved tokens/question
- reader tokens/question
- latency p50/p95/p99
- cost/question when provider-backed
- accuracy gain per 1k injected tokens

A memory system that gains a few answer points by reading vastly more context should not be described simply as "better" without the efficiency dimension.

## 11. Grader discipline

Prefer deterministic checks when the expected property is structural or exact.

Use LLM grading only when semantic judgment is genuinely required.

When LLM grading is used:
- freeze grader model/version where possible;
- freeze prompt and record its hash;
- record grader outputs as evaluation evidence, not truth;
- periodically spot-check with human or alternate-grader review;
- do not mix grader changes into a longitudinal score without an explicit bridge experiment.

## 12. External benchmark mapping

This profile may be exercised against external benchmark families such as:

- conflict-oriented memory tests;
- long-conversation memory benchmarks;
- correction/deletion governance suites;
- self-improvement fragility / task-order stress tests.

External benchmark behavior is evidence about Fractal under a test protocol. It does not become Fractal architecture authority.

## 13. Non-goals

This document does **not** authorize:

- a new GraphRAG pipeline;
- a second memory engine;
- automatic durable promotion;
- self-modifying agent behavior;
- evaluator-driven truth writes;
- production deployment;
- replacing Graphiti or Neo4j;
- changing canonical ingestion or namespace trust boundaries.

## 14. Suggested implementation sequence

```text
Phase 1: docs/profile only
    -> this document

Phase 2: deterministic adversarial fixtures
    -> experience retrieval fragility
    -> promotion-pressure / contagion
    -> environment applicability

Phase 3: controlled live Neo4j corpus
    -> conflict + temporal + retrieval metrics

Phase 4: provider-backed answer evaluation
    -> frozen grader manifest
    -> tokens / latency / cost
```

Each phase must preserve exact-head evidence and existing authority boundaries.

## 15. Acceptance principle

The evaluation program should be able to falsify both unsafe optimism and trivial safety.

```text
A system that remembers everything is not automatically good.
A system that remembers nothing is not automatically safe enough.
A useful agent memory system must satisfy positive obligations and negative invariants together.
```
