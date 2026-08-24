# 🤖 AI Models & Architectures — roles, evidence, evolution

**Status:** CURRENT REFERENCE + HISTORICAL RECORD  
**Evidence snapshot:** 2026-08-24  
**Runtime authority:** root `README.md` + `core/model_policy.py` + code/contracts

This document answers **what each model/family/architecture is for**, not merely which names exist.

It deliberately separates four different things that are often mixed together:

1. **provider/model family** — GPT, Claude, Gemini, DeepSeek, Qwen, Grok;
2. **model architecture** — dense Transformer, Mixture-of-Experts (MoE), hybrid/sparse attention;
3. **inference optimization** — KV cache, prefix/prompt caching, quantization, expert offload;
4. **Fractal runtime support** — what this repository actually instantiates today.

> **Model mentioned ≠ provider supported ≠ runtime default ≠ architecture adopted.**

## Evidence labels

- **OFFICIAL** — vendor documentation, model card, release notes, or primary project repository.
- **PROJECT DECISION** — a bounded choice made for Fractal Memory.
- **COMMUNITY SIGNAL** — Reddit/forum operator experience. Useful for deployment intuition, **not an authoritative performance guarantee**.
- **HISTORICAL** — retained to show how the ecosystem and Fractal evolved.

---

# 1. ✅ Current Fractal model routing

Fractal Memory currently has a first-class **OpenAI** runtime path.

| Workload | Current default | Exact role in Fractal | Status |
|---|---|---|---|
| Interactive chat | `gpt-5.6-terra` | answer generation over retrieved Fractal context | **ACTIVE** |
| Graphiti main LLM | `gpt-5.6-terra` | Graphiti extraction/reasoning during graph ingestion | **ACTIVE** |
| Summary/general synthesis | `gpt-5.6-luna` | bounded chat/L3 summaries where lower cost matters | **ACTIVE** |
| Graphiti small-model path | `gpt-5.6-luna` | smaller structured Graphiti prompts | **ACTIVE** |
| Embeddings | `text-embedding-3-small` | vector representation used by search/Graphiti support paths | **ACTIVE / STABLE** |
| GPT-5.6 Sol | explicit env override only | difficult review/research/coding when frontier capability is worth the cost | **OPT-IN** |

**PROJECT DECISION:** active defaults live only in `core/model_policy.py`. Changing the embedding model is a separate migration/reindex decision because existing vectors are model-dependent.

## Why Terra / Luna / Sol have different roles

**OFFICIAL:** OpenAI describes the GPT-5.6 family as:

- **Sol** — frontier model for complex professional work;
- **Terra** — balance of intelligence and cost;
- **Luna** — fastest / most affordable tier for cost-sensitive, high-volume workloads.

Fractal therefore does **not** route everything to the strongest model. Memory extraction, routine chat, and periodic summaries have different cost/latency needs.

OpenAI also supports prompt caching and long-lived cache behavior. That can reduce repeated-prefix processing cost/latency, but it is an inference optimization — **not a replacement for durable Fractal/Graphiti memory**.

---

# 2. 🧭 Provider families — what role could each actually play?

| Family | Current 2026 role signal from primary source | Potential Fractal role | Current Fractal status |
|---|---|---|---|
| OpenAI GPT-5.6 | general frontier/balanced/high-volume tiers, tools and agentic work | main hosted chat + Graphiti extraction + summaries | **ACTIVE** |
| Claude Sonnet 5 | agentic coding, tool use, knowledge work, browser/terminal workflows | tool-heavy coding/agent provider candidate | **ADJACENT / NO ADAPTER** |
| Claude Opus 5 | stronger long-running coding/knowledge agent work | difficult independent review / long-running agent candidate | **ADJACENT / NO ADAPTER** |
| Claude Fable 5 | frontier software engineering, research, vision/knowledge tasks | high-complexity research candidate, not routine default | **ADJACENT / NO ADAPTER** |
| Gemini 3.7 Flash | coding + agents + multimodal/high-throughput work; long context | multimodal/high-throughput agent candidate | **ADJACENT / NO ADAPTER** |
| DeepSeek V4 Pro | large open-weight MoE, long context, reasoning/agentic coding | self-host/open-weight high-capability candidate | **RESEARCH / NO ADAPTER** |
| DeepSeek V4 Flash | smaller-active MoE variant optimized for cost/speed | self-host/open-weight high-throughput candidate | **RESEARCH / NO ADAPTER** |
| Qwen3.8-27B | dense, multimodal, long-context open-weight model | realistic local/private agent candidate on capable hardware | **RESEARCH / NO ADAPTER** |
| Qwen3.8-2.4T-A95B | very large sparse MoE open-weight model | MoE/frontier inference research, distributed/server deployment | **RESEARCH / NOT LOCAL DEFAULT** |
| Grok 4.6 | coding, agentic tasks, knowledge work, search/tool ecosystem | tool-heavy research/coding provider candidate | **ADJACENT / NO ADAPTER** |

The table is a **role map**, not a ranking. A model can be excellent yet still have no reason to be wired into Fractal until a concrete provider requirement exists.

---

# 3. 🟢 OpenAI GPT — hosted model layer used today

## GPT-5.6 Terra — Fractal's primary hosted worker

**OFFICIAL role:** balanced intelligence/cost.  
**PROJECT role:**

- interactive answer generation;
- Graphiti extraction and structured reasoning;
- default hosted model when a stronger Sol call is unnecessary.

Why it fits: the memory service needs many repeated structured operations. Paying frontier-model cost for every extraction would couple memory throughput to the most expensive tier.

## GPT-5.6 Luna — bounded synthesis worker

**OFFICIAL role:** cost-sensitive/high-volume workload.  
**PROJECT role:** summaries and small Graphiti prompts.

Luna is therefore not a “weaker fallback” in the architecture; it has a deliberate **economy-lane role**.

## GPT-5.6 Sol — escalation lane

**OFFICIAL role:** frontier complex professional work.  
**PROJECT role:** optional escalation for difficult review/research/coding, selected via environment override.

Fractal does not hardwire Sol because ordinary chat-turn persistence or memory consolidation should not silently select the most expensive lane.

## Prompt caching

OpenAI's current platform supports prompt caching and explicit cache controls. The useful role for Fractal would be repeated stable system prefixes / long reusable instructions.

**Boundary:** provider prompt cache is ephemeral performance state. It does not replace:

- provenance;
- temporal graph facts;
- chat-turn persistence;
- Graphiti episodes;
- durable retrieval.

---

# 4. 🟠 Anthropic Claude — agent/coding specialization candidate

Fractal currently has **no Anthropic runtime adapter**.

## Claude Sonnet 5

Anthropic presents Sonnet 5 as its most agentic Sonnet, emphasizing coding, tool use, knowledge work, and autonomous browser/terminal workflows.

**Potential Fractal role:** a cost-conscious tool-heavy agent/coding provider, especially if Fractal later grows from memory service into an execution-oriented agent surface.

## Claude Opus 5

Anthropic positions Opus 5 for stronger coding/knowledge work and longer-running agents.

**Potential Fractal role:** difficult review, large refactors, or long-running agent tasks where higher capability matters more than routine throughput.

## Claude Fable 5 / restricted Mythos context

Fable 5 is positioned for high-end software engineering, knowledge work, vision and scientific research. Anthropic separately documents restricted-access safety variants/workflows such as Mythos for specialized sensitive domains.

**Potential Fractal role:** Fable-like frontier capability is an adjacent research lane; restricted variants are **not** a normal Fractal provider route and should never be implied by generic Claude support.

---

# 5. 🔵 Google Gemini — multimodal + long-context agent candidate

Fractal currently has **no Gemini runtime adapter**.

Google's current Gemini documentation positions **Gemini 3.7 Flash** as a high-throughput workhorse for coding and agents, with long context and multimodal capability.

**Potential Fractal roles:**

- document/image/video-aware ingestion research;
- high-throughput agent execution;
- long-context analysis where a multimodal source cannot be represented well as text-only chunks.

Google also exposes context caching. As with OpenAI prompt caching, this belongs to the **inference-cost/latency layer**, not durable memory.

---

# 6. 🟣 DeepSeek V4 — MoE + long-context efficiency research

Fractal currently has **no DeepSeek runtime adapter**.

DeepSeek's V4 release is important here not just because it is another model family, but because it demonstrates modern **MoE + long-context engineering**.

## V4 Pro

**OFFICIAL:** roughly **1.6T total parameters / 49B active parameters**, million-token context, stronger agentic coding/world knowledge/reasoning positioning.

**Potential Fractal role:** high-capability open-weight/self-hosted research path where data locality or provider independence matters.

## V4 Flash

**OFFICIAL:** roughly **284B total / 13B active**, also million-token context, targeted at faster/more economical inference.

**Potential Fractal role:** lower-active-compute open-weight agent/extraction worker if local/server infrastructure is later added.

DeepSeek also uses sparse/compressed attention techniques. This matters for long context because the system is attacking **attention/KV cost**, not only parameter count.

---

# 7. 🟡 Qwen — local/private and MoE experimentation spectrum

Fractal currently has **no Qwen runtime adapter**.

Qwen is useful architecturally because the current family exposes both a more practical dense model and a giant sparse-MoE model.

## Qwen3.8-27B

**OFFICIAL:** dense 27B model, multimodal/hybrid-attention family, native long context with documented extension toward larger context windows.

**Potential Fractal role:** local/private coding, document processing, or offline assistant experiments on capable workstation hardware.

This is materially different from routing everything through a hosted API: it trades hosted simplicity for local weights, quantization decisions, GPU/RAM constraints, and local inference maintenance.

## Qwen3.8-2.4T-A95B

**OFFICIAL:** 2.4T-total sparse model with roughly 95B active parameters. Its configuration exposes **512 experts with 10 selected per token**.

**Potential Fractal role:** MoE/distributed-inference research. It is not a reasonable “just run it locally” default merely because active parameters are much smaller than total parameters.

---

# 8. ⚫ xAI Grok — tool/search-heavy agent candidate

Fractal currently has **no xAI runtime adapter**.

xAI positions **Grok 4.6** for coding, agentic tasks, knowledge work and long-running interactive work. xAI's API/tool ecosystem also exposes function calling and search/code tools.

**Potential Fractal role:** tool-heavy research/coding agent where live search/tool integration is a requirement.

**Boundary:** current information comes from search tools; a model family name alone does not imply an always-current factual memory.

---

# 9. 🧠 MoE — an architecture, not “another AI”

**Mixture-of-Experts (MoE)** changes how capacity is organized inside a model.

A simplified view:

```text
token
  ↓
router/gating network
  ↓
select a small subset of experts
  ↓
only selected expert FFNs process that token
  ↓
combine outputs
```

## What MoE gives

- **large total capacity** without activating all expert parameters for every token;
- potentially lower per-token compute than a dense model with the same total parameter count;
- specialization opportunities across experts;
- better economics for very large models when serving infrastructure is designed for routing/expert parallelism.

## What MoE does *not* give

- total weights do not disappear — storage/RAM/distribution still matter;
- “13B active” does **not** mean a 284B-total model occupies only 13B worth of storage;
- expert routing and cross-device communication can become bottlenecks;
- local expert offload can become I/O-bound;
- MoE says nothing by itself about retrieval quality, memory truth, or graph semantics.

Concrete 2026 examples:

| Model | Total params | Active params / routing | Architectural lesson |
|---|---:|---:|---|
| DeepSeek V4 Pro | ~1.6T | ~49B active | huge capacity with bounded active compute |
| DeepSeek V4 Flash | ~284B | ~13B active | smaller active path for efficiency |
| Qwen3.8-2.4T-A95B | ~2.4T | ~95B active; 10/512 experts per token | extreme sparse expert routing |
| Qwen3.8-27B | 27B | dense | often simpler local deployment despite less total capacity |

**PROJECT DECISION:** MoE is **RESEARCH**, not a new Fractal subsystem. If Fractal later adds local inference, model architecture becomes a provider/inference selection concern; it does not alter Graphiti/Neo4j memory semantics.

---

# 10. ⚡ KV cache, prefix cache, prompt cache — their real role

These terms are related but not identical.

## KV cache

During autoregressive Transformer inference, attention keys/values for already processed tokens are retained so the model does not recompute the whole prefix at every generated token.

**Role:** inference acceleration / memory trade-off.

## Prefix caching

Serving engines such as vLLM can reuse an existing KV cache when a new request shares the same prefix.

**OFFICIAL vLLM boundary:** automatic prefix caching reduces **prefill** work; it does not make decoding newly generated tokens faster.

Useful Fractal-shaped workloads:

- repeatedly asking different questions about the same long document;
- multi-round chat with a stable conversation prefix;
- stable system instructions shared across requests.

## Provider prompt/context caching

Hosted APIs may manage equivalent reusable-prefix optimizations. OpenAI and Gemini both expose caching behavior in their platforms.

## None of these are durable memory

```text
KV / prefix / prompt cache → compute reuse
Graphiti + Neo4j            → durable temporal/relational memory
Fractal chat persistence    → durable conversation history
```

A cache miss must never mean “the fact was forgotten.”

---

# 11. 🗣️ Community signal — practical reports, not ground truth

These observations are retained because real deployment pain is useful, but they are **not benchmarks we treat as authoritative**.

### Local Qwen3.8-27B

Recent Reddit reports conflict — which is itself useful information. One operator reported a workable single-RTX-3090 local development setup; another user with two 3090 Ti cards reported looping/incorrect agent behavior relative to hosted coding tools.

**Interpretation:** local-model usability depends heavily on quantization, context size, serving engine, tool harness and prompts. “27B fits” is not the same claim as “27B performs reliably for my agent workflow.”

### MoE offload

LocalLLM/LocalLLaMA users report that MoE expert streaming/offload can make enormous models technically runnable on modest resident memory, but storage I/O and token-generation speed can become the limiting factor.

**Interpretation:** active-parameter count is useful but insufficient for hardware planning.

### Prompt/KV caching

Community questions repeatedly show that cache effectiveness depends on prefix stability and serving-engine configuration.

**Interpretation:** design stable reusable prefixes intentionally; do not assume every repeated request will hit a cache.

---

# 12. 📜 Historical evolution retained deliberately

## OpenAI

GPT-3/3.5 → GPT-4 → GPT-4 Turbo → GPT-4o / GPT-4o mini → GPT-4.1 + o-series → GPT-5.x → GPT-5.6.

`GPT-4` remains in the original Day-2 example as a **historical snapshot**. `gpt-4o-mini` remains documented as a former Fractal default, not a current recommendation.

## Anthropic

Claude 1/2 → Claude 3 family → 3.5/3.7 → Claude 4.x → Sonnet/Opus/Fable 5 generation.

## Google

PaLM/Bard-era systems → Gemini 1.x → Gemini 2.x → 2.5 → Gemini 3.x.

## DeepSeek

V2/V2.5 → V3 → R1 → V3.x → V4 Pro/Flash.

## Qwen

Qwen → Qwen1.5 → Qwen2/2.5 → QwQ → Qwen3 → Qwen3.5/3.6 → Qwen3.8 dense + giant MoE variants.

## xAI

Grok 1/1.5 → Grok 2 → Grok 3 → Grok 4 generation → Grok 4.6.

The point of this section is not nostalgia: historical model choices explain old code, old prompts, old performance assumptions and why current defaults changed.

---

# 13. 🔒 Rule for future AI/model updates

When the ecosystem changes:

1. Verify the primary vendor/model-card source first.
2. Decide the **role**, not merely whether the new model is newer.
3. Change `core/model_policy.py` only when the active Fractal route should actually change.
4. Keep superseded models in this history.
5. Never silently change embeddings; require an explicit reindex/migration decision.
6. A documented provider does not become a supported adapter automatically.
7. MoE/attention/cache improvements are architectural/inference facts, not memory-authority changes.
8. Treat Reddit/forum material as operator signal only.
9. Run always-on contracts and a separate live provider integration tier before claiming runtime validation.

---

# 14. 🔗 Primary sources checked 2026-08-24

## Model/provider sources

- OpenAI GPT-5.6: https://openai.com/index/gpt-5-6/
- OpenAI model docs: https://developers.openai.com/api/docs/models
- Anthropic Claude Sonnet 5: https://www.anthropic.com/news/claude-sonnet-5
- Anthropic Claude Opus 5: https://www.anthropic.com/news/claude-opus-5
- Google latest Gemini models: https://ai.google.dev/gemini-api/docs/latest-model
- DeepSeek V4 release: https://api-docs.deepseek.com/news/news260424/
- Qwen3.8 primary repository: https://github.com/QwenLM/Qwen3.8
- xAI Grok 4.6: https://x.ai/news/grok-4-6

## Inference/cache source

- vLLM Automatic Prefix Caching: https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/

## Community signals (non-authoritative)

- Qwen3.8 local dev-agent report: https://www.reddit.com/r/LocalLLM/comments/1vs9pwo/
- Qwen3.8 negative agentic report: https://www.reddit.com/r/LocalLLaMA/comments/1vsinej/
- MoE/local-memory discussion: https://www.reddit.com/r/LocalLLM/comments/1t8coag/
- DeepSeek V4 Flash streamed-expert experiment: https://www.reddit.com/r/LocalLLaMA/comments/1vdbix4/
- prompt-cache operator discussion: https://www.reddit.com/r/LocalLLaMA/comments/1uiq26m/

Primary sources define facts. Community links provide only practical context around hardware, serving and workflow behavior.