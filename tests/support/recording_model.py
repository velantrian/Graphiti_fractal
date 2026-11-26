"""Deterministic, recording stand-ins for the model-facing clients of graphiti_core.

Nothing here calls a provider. ``RecordingLLM`` stores the exact messages of every
model call (what the model would have seen) and answers with schema-valid output
from a small rule set, so the real ``Graphiti.add_episode`` pipeline runs end to
end against a real Neo4j:

* entities: every ``[[Name]]`` in the *episode being added* (set via ``expect``);
* edges: one fact per episode, linking its first two entities, text = the episode
  sentence;
* entity summaries ("context-faithful model simulation"): every sentence found in
  the prompt that mentions the entity. This is a modelling assumption; it makes
  the stored summary a pure function of the recorded input, so "was the deleted
  text in the input" and "did it reach a derived object" are separate, measurable
  facts.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize

ENTITY = re.compile(r"\[\[([^\]]+)\]\]")
SENTENCE = re.compile(r"[^.\n\"]*[.]")


@dataclass
class ModelCall:
    prompt_name: str | None
    response_model: str | None
    text: str


class RecordingLLM(LLMClient):
    def __init__(self):
        super().__init__(LLMConfig(api_key="recording-stub", model="recording-stub"), cache=False)
        self.calls: list[ModelCall] = []
        self.episode_body = ""

    def expect(self, episode_body: str) -> None:
        """The text of the episode about to be added (used only to build answers)."""
        self.episode_body = episode_body

    def mark(self) -> int:
        return len(self.calls)

    def input_since(self, mark: int) -> str:
        return "\n".join(call.text for call in self.calls[mark:])

    async def _generate_response(self, messages, response_model=None, max_tokens=None,
                                 model_size=ModelSize.medium):
        raise NotImplementedError("RecordingLLM overrides generate_response")

    async def generate_response(self, messages, response_model=None, max_tokens=None,
                                model_size=ModelSize.medium, group_id=None, prompt_name=None,
                                *, attribute_extraction=False):
        text = "\n".join(str(message.content) for message in messages)
        name = response_model.__name__ if response_model is not None else None
        self.calls.append(ModelCall(prompt_name, name, text))
        return self._answer(name, text)

    # -- rule set --------------------------------------------------------------
    def _entities(self) -> list[str]:
        seen: list[str] = []
        for name in ENTITY.findall(self.episode_body):
            if name not in seen:
                seen.append(name)
        return seen

    def _answer(self, name: str | None, text: str) -> dict[str, Any]:
        entities = self._entities()
        if name == "ExtractedEntities":
            return {"extracted_entities": [
                {"name": n, "entity_type_id": 0, "episode_indices": [0]} for n in entities]}
        if name == "NodeResolutions":
            pairs = re.findall(r'"id":\s*(\d+),\s*"name":\s*"([^"]+)"', text) or [
                (str(i), n) for i, n in enumerate(entities)]
            return {"entity_resolutions": [
                {"id": int(i), "name": n, "duplicate_candidate_id": -1} for i, n in pairs]}
        if name == "ExtractedEdges":
            if len(entities) < 2:
                return {"edges": []}
            sentence = next((s.strip() for s in SENTENCE.findall(self.episode_body)
                             if ENTITY.search(s)), self.episode_body)
            fact = ENTITY.sub(lambda m: m.group(1), sentence)
            return {"edges": [{"source_entity_name": entities[0], "target_entity_name": entities[1],
                               "relation_type": "RELATES_TO", "fact": fact}]}
        if name == "EdgeDuplicate":
            return {"duplicate_facts": [], "contradicted_facts": []}
        if name in {"EdgeTimestamps", "BatchEdgeTimestamps"}:
            return {"valid_at": None, "invalid_at": None} if name == "EdgeTimestamps" else {"timestamps": []}
        if name == "SummarizedEntities":
            names = re.findall(r'"name":\s*"([^"]+)"', text)
            wanted = [n for n in dict.fromkeys(names) if n in entities] or entities
            return {"summaries": [{"name": n, "summary": self._summary(n, text)} for n in wanted]}
        raise NotImplementedError(f"RecordingLLM has no answer for response model {name!r}")

    @staticmethod
    def _summary(entity: str, text: str) -> str:
        clean = text.replace("[[", "").replace("]]", "")
        facts = [s.strip().lstrip(", ") for s in SENTENCE.findall(clean) if entity in s]
        return " ".join(dict.fromkeys(facts))[:1500] or entity


class HashEmbedder(EmbedderClient):
    DIM = 16

    async def create(self, input_data):
        digest = hashlib.sha256(str(input_data).encode()).digest()
        return [(b - 127.5) / 127.5 for b in digest[: self.DIM]]

    async def create_batch(self, input_data_list):
        return [await self.create(item) for item in input_data_list]


class ZeroRanker(CrossEncoderClient):
    async def rank(self, query, passages):
        return [(p, 1.0 / (i + 1)) for i, p in enumerate(passages)]
