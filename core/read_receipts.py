"""Server-side read receipts: the verifiable link between what an MCP client was shown and what it writes back.

``memory.search_knowledge`` stores, for the items it actually delivered, one ``:FractalReadReceipt`` node: owner,
issue time and expiry, the global erasure revision, and per source its type, UUID, group and SHA-256 of the text
that was delivered (before display truncation). The client receives only the random receipt id.

A write that declares itself a retelling (``derived_from``) names the receipt and the source UUIDs. Before Graphiti is
called, and again inside the atomic finalization of every stored chunk, the server checks that the receipt exists,
belongs to this owner, has not expired, lists every named source, and that each source still exists, is visible and
still has exactly the text that was delivered. The receipt proves that the system SHOWED these sources; it does not
prove that the text the client wrote semantically uses them - the lineage it creates is therefore conservative.

Refusals (``ReadReceiptRejected.code``): bad_request, unknown_receipt, receipt_not_applicable (other owner),
receipt_expired, source_not_in_receipt, source_missing, source_not_visible, source_changed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from core.erasure_guard import LOCK_ERASURE_CONTROL, visible_node

RECEIPT_TTL_SECONDS = 3600
MAX_SOURCES = 50

_KIND = {"episode": "Episodic", "entity": "Entity", "Episodic": "Episodic", "Entity": "Entity"}


class ReadReceiptRejected(ValueError):
    """A write cannot be linked to the read it claims to derive from; nothing was written."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class VerifiedReceipt:
    receipt_id: str
    revision: int
    sources: tuple[dict, ...]            # {"type","uuid","group_id","text"} as verified just now
    source_ids: tuple[str, ...] = field(default=())

    def checks(self) -> list[dict]:
        """The per-source expectations a later atomic statement re-verifies (exact current text)."""
        return [{"uuid": s["uuid"], "type": s["type"], "group_id": s["group_id"], "text": s["text"]}
                for s in self.sources]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def delivered_text(summary, content, name) -> str:
    """The text search_knowledge delivers for a record: summary, else content, else name."""
    return str(summary or content or name or "")


async def _rows(graphiti, cypher: str, **params) -> list[dict]:
    return [dict(r) for r in (await graphiti.driver.execute_query(cypher, **params)).records]


async def issue_read_receipt(graphiti, *, user_id: str, tool: str, items: list[dict]) -> dict | None:
    """Persist what was delivered. ``items``: {"type","uuid","group_id","raw_text"}. None if nothing to attest."""
    sources = [{"type": _KIND[i["type"]], "uuid": i["uuid"], "group_id": i["group_id"], "sha256": sha(i["raw_text"])}
               for i in items if i.get("uuid") and i.get("type") in _KIND and i.get("raw_text")][:MAX_SOURCES]
    if not sources:
        return None
    receipt_id = uuid4().hex + uuid4().hex
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=RECEIPT_TTL_SECONDS)
    rows = await _rows(graphiti, LOCK_ERASURE_CONTROL + """
        CREATE (r:FractalReadReceipt {receipt_id:$id, user_id:$u, tool:$tool, sources:$sources,
                                      revision:coalesce(gate.revision,0), issued_at:datetime($now),
                                      expires_at:datetime($exp)})
        RETURN r.revision AS revision""", id=receipt_id, u=user_id, tool=tool, now=now.isoformat(),
        exp=expires.isoformat(), sources=json.dumps(sources, sort_keys=True))
    return {"receipt_id": receipt_id, "expires_at": expires.isoformat(), "revision": rows[0]["revision"],
            "sources": [{"type": s["type"], "uuid": s["uuid"], "group_id": s["group_id"]} for s in sources]}


async def verify_read_receipt(graphiti, *, user_id: str, receipt_id: str, source_ids: list[str],
                              _source_claim_token: str | None = None) -> VerifiedReceipt:
    if (not isinstance(receipt_id, str) or not receipt_id or not isinstance(source_ids, list) or not source_ids
            or len(source_ids) > MAX_SOURCES or any(not isinstance(u, str) or not u for u in source_ids)
            or len(set(source_ids)) != len(source_ids)):
        raise ReadReceiptRejected("bad_request", "receipt_id and a non-empty list of distinct source ids are required")
    rows = await _rows(graphiti, "MATCH (r:FractalReadReceipt {receipt_id:$id}) RETURN r.user_id AS u, r.sources AS s, "
                                 "r.revision AS rev, (r.expires_at IS NULL OR r.expires_at <= datetime()) AS expired", id=receipt_id)
    if len(rows) != 1:
        raise ReadReceiptRejected("unknown_receipt")
    r = rows[0]
    if r["u"] != user_id:
        raise ReadReceiptRejected("receipt_not_applicable", "issued to another owner")
    if r["expired"]:
        raise ReadReceiptRejected("receipt_expired")
    issued = {s["uuid"]: s for s in json.loads(r["s"])}
    verified = []
    for uuid in source_ids:
        src = issued.get(uuid)
        if src is None:
            raise ReadReceiptRejected("source_not_in_receipt", uuid)
        nodes = await _rows(graphiti, f"MATCH (n:{src['type']} {{uuid:$u}}) RETURN n.group_id AS g, n.summary AS s, "
                                      f"n.content AS c, n.name AS name, ({visible_node('n', source_claim=True)}) AS visible",
                            u=uuid, source_claim_token=_source_claim_token)
        if len(nodes) != 1 or nodes[0]["g"] != src["group_id"]:
            raise ReadReceiptRejected("source_missing", uuid)
        if not nodes[0]["visible"]:
            raise ReadReceiptRejected("source_not_visible", uuid)
        text = delivered_text(nodes[0]["s"], nodes[0]["c"], nodes[0]["name"])
        if sha(text) != src["sha256"]:
            raise ReadReceiptRejected("source_changed", uuid)
        verified.append({"type": src["type"], "uuid": uuid, "group_id": src["group_id"], "text": text})
    return VerifiedReceipt(receipt_id, int(r["rev"]), tuple(verified), tuple(source_ids))


# Cypher fragment used by atomic statements: every source still exists once, is visible and has the verified text.
SOURCES_STILL_HOLD = """
    ($source_receipt_id IS NULL AND size($source_checks) = 0) OR (
      $source_receipt_id IS NOT NULL AND size($source_checks) > 0
      AND COUNT { MATCH (:FractalReadReceipt {receipt_id:$source_receipt_id}) } = 1
      AND EXISTS { MATCH (fz_rr:FractalReadReceipt {receipt_id:$source_receipt_id})
                   WHERE fz_rr.user_id=$source_receipt_user AND fz_rr.expires_at > datetime() }
      AND size([chk IN $source_checks WHERE
        COUNT { MATCH (fz_s) WHERE fz_s.uuid = chk.uuid AND fz_s.group_id = chk.group_id
                  AND ((chk.type='Episodic' AND fz_s:Episodic) OR (chk.type='Entity' AND fz_s:Entity))
                  AND """ + visible_node("fz_s", source_claim=True) + """ AND
                  (CASE WHEN coalesce(fz_s.summary,'') <> '' THEN fz_s.summary
                        WHEN coalesce(fz_s.content,'') <> '' THEN fz_s.content
                        ELSE coalesce(fz_s.name,'') END) = chk.text } = 1
        AND COUNT { MATCH (fz_s2) WHERE fz_s2.uuid = chk.uuid AND (fz_s2:Episodic OR fz_s2:Entity) } = 1
    ]) = size($source_checks))
"""


async def merge_lineage_into_existing(graphiti, *, group_id: str, fingerprint: str, content: str,
                                      receipt: VerifiedReceipt, user_id: str) -> bool:
    """A repeat or deduplicated derived write: the already stored derived episode keeps and extends its links.

    Only an existing episode that is itself ``agent_derived`` takes the sources (monotonic union, same exact content,
    sources re-verified in the same statement). An owner-authored episode with the same text is an independent source
    and is left untouched."""
    rows = await _rows(graphiti, LOCK_ERASURE_CONTROL + """
        MATCH (e:Episodic {group_id:$g, fingerprint:$fp})
        WHERE e.origin_class = 'agent_derived' AND e.content = $content
          AND EXISTS { MATCH (e)-[:STORED_FOR]->(:User {user_id:$owner}) }
          AND """ + visible_node("e") + """ AND (""" + SOURCES_STILL_HOLD + """)
        WITH collect(e) AS matches WHERE size(matches)=1
        WITH matches[0] AS e
        SET e.recall_source_ids = [x IN coalesce(e.recall_source_ids,[]) WHERE NOT x IN $ids] + $ids,
            e.recall_derived = true
        RETURN count(e) AS n""", g=group_id, fp=fingerprint, content=content, ids=list(receipt.source_ids),
        source_checks=receipt.checks(), source_receipt_id=receipt.receipt_id,
        source_receipt_user=user_id, source_claim_token=None, owner=user_id)
    return bool(rows and rows[0]["n"])
