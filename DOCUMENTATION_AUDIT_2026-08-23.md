# Documentation Audit — 2026-08-23

**Scope:** accuracy/currency (точность и актуальность) and completeness/structure (полнота и структура) of this repository's documentation (`*.md`, README, `docs/`), assessed against a snapshot of the default branch on 2026-08-23. This is a documentation snapshot audit, not a code-quality or security review, and does not cover unmerged branches.

## Overall health assessment

**Fair.** The docs show real self-awareness in places — `Master_Project_Plan.md` and `Day_5_7_Fractal_Layers.md` both carry explicit "Historical/Warning" banners pointing at the current `layers/` code, and `docs/memory_ops.md`'s Python/FastAPI usage examples match `core/memory_ops.py` and `app.py` almost exactly. But several of the most operationally important instructions are broken or contradicted by the actual repo: the README's Docker workflow conflicts with `docker-compose.yml`'s own `command:`, a documented `make run` target doesn't exist, the documented MCP tool names don't match what `mcp_server/server.py` actually advertises, and the docs/ folder's own navigational index is both incomplete and references nonexistent files. A user following the docs literally would hit real friction on the three most common onboarding paths (Docker, Makefile, MCP).

## Findings

1. **README.md** | accuracy | high | Documented Docker workflow contradicts `docker-compose.yml`'s actual startup command. | README says "app ждёт, можно exec внутрь" then instructs `docker compose exec app bash` → `python -m uvicorn app:app --host 0.0.0.0 --port 8000`, but `docker-compose.yml`'s `command: ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]` already auto-starts that exact server on `docker compose up -d` — the documented manual step would fail with a port-already-in-use conflict.

2. **docs/README_Index.md** | completeness | high | The doc set's own navigational index references 2 nonexistent files and omits 5 real ones. | Points readers to `Quick_Start_30min.md` and `Graphiti_Full_Spec.md` (neither exists in `docs/`), while never mentioning `GRAPH_CONNECTIVITY.md`, `HANDS_ON_TESTING.md`, `REFACTORING_CHANGELOG.md`, `TESTING_AND_SIMPLE_AGENT.md`, or `memory_ops.md` — 5 of the repo's 12 markdown files are orphaned from the index whose entire purpose is navigation.

3. **docs/memory_ops.md** | accuracy | high | Documented MCP tool names don't match the tools the server actually registers. | Doc lists `memory.remember_text`, `memory.search_memory`, `memory.chat` under "MCP Server Tools," but `mcp_server/server.py`'s `TOOLS` list (used for `tools/list`) instead exposes `memory.search_knowledge`, `memory.search_experience`, `memory.remember`, `memory.upload`, `memory.delete` — a real MCP client would never discover the documented tools, and the advertised ones (other than `memory.delete`) aren't handled by `_tool_call`, which raises `"Unknown tool"` for them.

4. **docs/HANDS_ON_TESTING.md** | accuracy | high | Documents `make run` as the "full demo" command, but no such Makefile target exists. | Step 8 says `make run` chains setup/seed/quality/search-demo/l1/l2/l3/viz-export/benchmark; the actual `Makefile` only defines `venv, install, setup, seed, quality, search, context, l1, l2, l3, viz, benchmark, test, migrate, web, dc-build, dc-up, dc-down, dc-logs` — running `make run` fails with "No rule to make target 'run'".

5. **README.md vs mcp.json.example** | accuracy | medium | README's inline MCP config example doesn't match the actual template file it points to. | README shows `"args": ["/c", "C:\\PATH\\TO\\Graphiti_fractal\\run_mcp_server.cmd"]`, but `mcp.json.example` itself wraps the same path in an extra escaped quote pair — copying the actual template does not produce what the README shows.

6. **docs/HANDS_ON_TESTING.md** | accuracy | medium | Manual Neo4j setup instructions pin a different image version than the repo's own compose file. | Doc's `docker run` step uses `neo4j:5.20-community`, while `docker-compose.yml` (the README's recommended path) uses `neo4j:5.26-community`; `Dockerfile` even documents a `graphiti_core` patch justified specifically by "Neo4j 5.20 compatibility."

7. **docker-compose.yml / README.md / .env.example** | completeness | medium | Chat-related env vars set in docker-compose are undocumented anywhere, and one is dead code. | `docker-compose.yml` sets `CHAT_SAVE_EPISODES`, `CHAT_SAVE_BOT_EPISODES`, `CHAT_USE_GRAPHITI_SEARCH`, but none appear in README's env var list or `.env.example`; `core/config.py` explicitly logs that `CHAT_SAVE_EPISODES` "is deprecated and ignored."

8. **docs/memory_ops.md** | accuracy | medium | Architecture diagram describes a call path the code no longer uses. | Diagram states `remember_text() → knowledge.ingest.remember_text()`, but `core/memory_ops.py`'s `MemoryOps.remember_text()` calls its own `self.ingest_pipeline()` — `knowledge.ingest.remember_text` isn't even imported in that file.

9. **README.md** | completeness | low | Three real CLI subcommands are undocumented. | `main.py`'s `build_parser()` defines `migrate`, `consolidate`, and `dedupe-entities` subparsers, none of which appear in README's "Основные команды" section.

10. **docs/Day_2_Custom_Entities.md, docs/Day_3_4_Visualization_Queries.md** | structure | low | Missing the staleness disclaimer that sibling day-docs carry for the same kind of drift. | `Day_5_7_Fractal_Layers.md` and `Master_Project_Plan.md` were retrofitted with explicit "Historical Log"/"Warning" banners pointing to current code; Day_2/3_4 present now-outdated calls/scripts as current instructions with no such caveat.

---
*Generated by an automated documentation audit (Claude Code).*
