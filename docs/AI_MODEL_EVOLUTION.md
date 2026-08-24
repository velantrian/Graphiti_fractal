# 🤖 AI Model Evolution — current policy + historical record

**Status:** reference / history  
**Current-policy snapshot:** 2026-08-24

This document separates two things that used to be mixed together in old Fractal Memory notes:

1. **Current runtime model policy** — what this repository actually selects today.
2. **Historical model evolution** — older model names kept as evidence of how the AI ecosystem and this project evolved.

Historical entries are **not runtime recommendations** and do **not** imply that Fractal Memory currently implements each provider.

---

## ✅ Current Fractal Memory runtime policy

Fractal Memory currently has a first-class **OpenAI runtime path**.

| Workload | Current default | Why |
|---|---|---|
| Interactive chat | `gpt-5.6-terra` | balanced intelligence/cost |
| General / summary synthesis | `gpt-5.6-luna` | lower-cost, high-volume synthesis |
| Graphiti main extraction/reasoning | `gpt-5.6-terra` | balanced structured extraction |
| Graphiti small-model prompts | `gpt-5.6-luna` | efficient small-prompt path |
| Embeddings | `text-embedding-3-small` | retained deliberately; changing embedding model is a reindex decision |

The active defaults live in `core/model_policy.py`. Environment variables can override them without changing source code.

### Why GPT-5.6?

As of 2026-08-24 OpenAI's current model guidance describes:

- **GPT-5.6 Sol** — frontier model for complex professional work;
- **GPT-5.6 Terra** — balance of intelligence and cost;
- **GPT-5.6 Luna** — cost-sensitive, high-volume workloads.

Fractal Memory therefore uses Terra for the main interactive/extraction path and Luna for cheaper bounded synthesis/small-model work. Sol remains an explicit opt-in choice rather than the default, because memory ingestion and routine chat do not need the most expensive tier by default.

The GPT-5.6 family supports Chat Completions as well as Responses. Fractal Memory keeps its existing bounded Chat Completions surface for this cleanup instead of coupling a model refresh to a second API migration.

---

## 🧭 Runtime support vs ecosystem history

| Provider/family | In current Fractal runtime? | Treatment in this repository |
|---|---:|---|
| OpenAI GPT | ✅ Yes | active configured provider |
| Anthropic Claude | ❌ No first-class adapter here | historical/ecosystem reference |
| Google Gemini | ❌ No first-class adapter here | historical/ecosystem reference |
| DeepSeek | ❌ No first-class adapter here | historical/ecosystem reference |
| Qwen | ❌ No first-class adapter here | historical/ecosystem reference |
| xAI Grok | ❌ No first-class adapter here | historical/ecosystem reference |

This boundary is intentional: **mentioning a model is not the same as supporting its API**.

---

# 📜 Model evolution timeline

The goal of this section is not to rank vendors. It preserves major generations that appeared in project notes or were part of the broader model landscape around the project's development.

## 🟢 OpenAI / GPT

### Historical generations

- **GPT-3 / GPT-3.5** — early broadly used generative/chat generation.
- **GPT-4** — major reasoning/capability step; appears in the original Fractal Day-2 example and is intentionally preserved there as a historical snapshot.
- **GPT-4 Turbo** — lower-cost/faster GPT-4-era deployment path.
- **GPT-4o / GPT-4o mini** — multimodal/efficient generation; `gpt-4o-mini` later became an early Fractal runtime default.
- **GPT-4.1 + o-series** — stronger coding/instruction following and explicit reasoning-model era.
- **GPT-5 / GPT-5.x** — unified GPT-5 reasoning-capable generations.

### Current snapshot — 2026-08-24

- `gpt-5.6-sol` — frontier capability.
- `gpt-5.6-terra` — balanced capability/cost; **Fractal main default**.
- `gpt-5.6-luna` — high-volume/cost-sensitive; **Fractal summary/small default**.

So old references such as **GPT-4** and **GPT-4o mini** should be read as historical stages, not current recommendations.

---

## 🟠 Anthropic / Claude

### Historical generations

- Claude 1 / 2
- Claude 3: Haiku / Sonnet / Opus
- Claude 3.5 / 3.7 Sonnet era
- Claude 4.x family, including later Opus/Sonnet revisions

### 2026 snapshot

Anthropic's 2026 releases progressed through **Opus 4.6/4.7/4.8** into the **Claude 5** generation. By mid-2026 Anthropic announced **Claude Fable 5**, **Claude Sonnet 5**, and later **Claude Opus 5**.

These names are kept here as ecosystem history only. Fractal Memory does not currently instantiate an Anthropic client.

---

## 🔵 Google / Gemini

### Historical generations

- Bard-era Google models / PaLM lineage
- Gemini 1.x
- Gemini 2.x, including 2.0 Flash / Flash-Lite
- Gemini 2.5-era reasoning and multimodal models
- Gemini 3 generation

### 2026 snapshot

Google's Gemini API documentation in August 2026 lists the Gemini 3 generation with models including **Gemini 3.1 Pro** and **Gemini 3.7 Flash**, while older models such as Gemini 2.0 Flash are in the previous/deprecated section.

Fractal Memory does not currently instantiate a Gemini client.

---

## 🟣 DeepSeek

### Historical generations

- DeepSeek V2 / V2.5
- DeepSeek V3
- **DeepSeek R1** — reasoning-focused open model generation
- DeepSeek V3.1 / V3.2

### 2026 snapshot

DeepSeek announced **DeepSeek V4 Preview** in April 2026 with:

- `deepseek-v4-pro`
- `deepseek-v4-flash`

The older API aliases `deepseek-chat` and `deepseek-reasoner` were scheduled for retirement in July 2026. Historical notes using those old names should therefore remain historical rather than be copied into new runtime configuration.

Fractal Memory does not currently instantiate a DeepSeek client.

---

## 🟡 Qwen

### Historical generations

- Qwen
- Qwen1.5
- Qwen2 / Qwen2.5
- QwQ reasoning line
- Qwen3

Qwen's 2025 Qwen3 release represented a major reasoning/agentic generation. In 2026 Qwen Code documentation shows the service moving to **`qwen3.5-plus`** for its OAuth model path and continuing rapid agent/tooling evolution.

This is ecosystem history/reference only; Fractal Memory does not currently instantiate a Qwen provider.

---

## ⚫ xAI / Grok

### Historical generations

- Grok 1 / 1.5
- Grok 2
- Grok 3
- Grok 4 generation

### 2026 snapshot

xAI's April 2026 model card identifies **Grok 4.20** as its then-latest model and describes single-agent and multi-agent deployment modes.

Fractal Memory does not currently instantiate an xAI client.

---

# 🧩 Repository rule for future model updates

When a model generation changes:

1. Update **`core/model_policy.py`** only if the active runtime default should change.
2. Update `.env.example` and the current README model table.
3. Add the superseded model to this history instead of deleting it.
4. Do not silently swap the embedding model; treat that as a separate migration/reindex decision.
5. Do not claim provider support because a provider/model is mentioned in documentation.
6. Run the always-on model-policy contract plus normal CI.
7. Keep live provider integration as a separate validation tier.

This preserves both **technical freshness** and **project memory**: today's recommendation stays clear, while yesterday's model choices remain visible as part of the system's evolution.
