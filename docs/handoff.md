# Hexis Handoff

Last updated: 2026-07-19 (standalone `~/embeddinggemma.c` chosen; Hexis now
uses the local Metal sidecar binary until publishing)

## Standing orders from Eric (do not violate)

1. **Do not touch the retired local-model app or any of its stores.** No
   commands, comparisons, blob reads, or symlinks into another app's model
   directory. Hexis uses the standalone `~/embeddinggemma.c` engine. (Context:
   an earlier symlink + case-insensitive filename collision let a `curl -o`
   clobber another app's model blob through the symlink — repaired and
   verified, but the lesson stands: never symlink into another app's store;
   beware macOS case-insensitive paths.)
2. **embeddinggemma.c is a true hand-port** — pure C, no C++, no dependencies
   except libcurl for the download path, no ggml, no llama.cpp linkage. Eric
   explicitly chose owning the full inference path. Do not relitigate.
3. **Eric builds and publishes all binaries from this machine** into the Hexis
   release (no CI build matrix). Targets: Metal, CUDA, ROCm, CPU x64, CPU arm64.
   For now Hexis uses the local binary:
   `~/embeddinggemma.c/build/embeddinggemma-metal`.
4. The sidecar owns model placement and download: it always looks for
   `model/embeddinggemma-300M-qat-Q4_0.gguf` relative to its executable and
   downloads the hard-coded Hugging Face URL when absent. No overrides.
5. Re-verify machine state before acting — earlier probes go stale, and a
   duplicate package install once followed a stale "not installed" check; Eric
   called it out.

## This machine (new laptop) — environment state

- **venv**: rebuilt with uv (Python 3.12.13) after the old symlink broke in the
  laptop switch. No `pip` in it — use `uv pip install --python .venv/bin/python`.
  Shell is **fish**: `source .venv/bin/activate` fails in the Bash tool; call
  `.venv/bin/python` / `.venv/bin/pytest` / `.venv/bin/hexis` directly, or
  wrap loops in `bash -c '...'`.
- **Network**: AT&T WiFi with a content filter. apt repos (deb.debian.org,
  apt.postgresql.org) are BLOCKED (redirect to login.attwifi.com) → the db
  Docker image cannot be built locally on this network. Docker Hub, GHCR, npm,
  PyPI, and huggingface.co all work. `gh` token now has `read:packages`.
- **DB stack**: `hexis_brain` runs the prebuilt `ghcr.io/quixiai/hexis-brain:latest`
  tagged locally as `hexis-db:latest`; start with
  `docker compose up -d --no-build`. Working-tree parity comes from
  migrations (all 0001–0086 applied; **next migration number: 0087**). Rebuild
  the image from `ops/Dockerfile.db` when on an unfiltered network.
- **Agent state**: `hexis init` NOT completed — `agent.is_configured` unset,
  0 memories, embedding cache cleared. No `.env` / API keys on this machine
  (didn't transfer). `tools.allow_dynamic=true` already set in config.
- **Web UI**: deps installed via **bun** (`hexis ui` works). Gotcha: fresh
  `bun install` does not run `prisma generate` — if `/api/status` 500s with
  "Cannot find module '.prisma/client/default'", run
  `cd hexis-ui && bunx prisma generate` and restart the dev server.
- **Embeddings**: Hexis now uses the standalone `~/embeddinggemma.c` project,
  not an in-repo `embedding-inference/` tree. Local binary:
  `~/embeddinggemma.c/build/embeddinggemma-metal` (verified Mach-O arm64).
  `hexis up` starts it on port 11434 if that port is idle, then the DB reaches
  it through the existing default
  `EMBEDDING_SERVICE_URL=http://host.docker.internal:11434/api/embed`. Sidecar
  logs go to `~/.hexis/embeddinggemma.log`. The binary itself hard-codes the
  model path relative to its executable and downloads the GGUF with libcurl if
  missing.
- **Init UI**: migration 0086 tightens `get_init_status()` so model setup is
  complete only when both conscious and subconscious configs have provider +
  model. The web init page no longer skips the Models screen from ambient
  stored config while DB stage is still `llm`; it pre-fills rows but waits for
  an explicit Save Models click.
- llama.cpp reference checkout: `~/llama.cpp` at commit `d77599234`, tools
  already built in `build/bin` (`llama-embedding`, `llama-tokenize`,
  `llama-gguf`). Used ONLY to generate goldens.

## Workstream A — Mission plan (MISSION_PROGRESS.md, plan at docs/plans/mission-implementation-plan.md)

- Batches 1 (#96) and 2 (#98): DONE (see git log / previous handoff).
- **Batch 3 (#99): all code complete, suite green (2457 passed / 1 skipped),
  but UNCOMMITTED** — the working tree holds:
  - `core/tools/self_extension.py` (new): `record_self_extension(pool,
    summary, notice, detail)` — journals `record_change('self_extension', …)`
    + queues a web-inbox notice via `queue_outbox_message(…,
    '{"mode":"web_inbox"}')`; advisory (log-and-continue).
  - `core/tools/dynamic.py` + `core/tools/skills.py`: call it after successful
    create_tool / author_skill (created-vs-updated distinguished).
  - Tests: `tests/core/test_dynamic_tools.py` (+2),
    `tests/core/test_skills_marketplace.py` (+1) — journal row, envelope
    `delivery == {"mode":"web_inbox"}`, first-person notice text, cleanup.
  - `CONTRIBUTING.md`: footprint ladder (extend → skill → gated tool → plugin
    → MCP → core last) + omissions-with-redirects table;
    `docs/contributing/index.md` gained Key Principle 5 linking it.
  - `apps/hexis_init.py`: **embedding-failure UX fix** (Experience Bar) —
    `_run_embedding_step(conn, step, interactive=)` shows cause + exact fix +
    "Try again?" retry-in-place (only the failed DB write reruns; answers
    kept); wraps express/character/custom write blocks, consent, and the
    non-interactive path. +3 regression tests in
    `tests/cli/test_init_noninteractive.py` (tests/cli: 50/50 green).
    The guidance now points to the local `embeddinggemma.c` sidecar, not
    the retired local-model app.
  - `apps/hexis_cli.py`: `hexis up` starts
    `~/embeddinggemma.c/build/embeddinggemma-metal` before the advisory
    embedding health probe. The old in-repo `embedding-inference/` directory
    was removed.
- **Remaining to close #99**: (1) live self-extension acceptance — blocked on
  Eric completing init + consent (needs an LLM API key; wizard at
  `hexis init` or `localhost:3477/init`): ask the agent to build a small tool
  via create_tool, bind via author_skill, use it; verify change-journal row +
  web-inbox notice + survival across worker restart; (2) flip Batch 3 rows in
  MISSION_PROGRESS.md with SHAs; (3) close #99. Commit message pattern: see
  git log; never add Co-Authored-By.
- Batches 4–8: not started. The standalone embeddinggemma.c integration has
  jumped the queue by Eric's directive; expect re-sequencing.

## Workstream B — standalone embeddinggemma.c

**Decision update**: `embedding-inference/` was removed from this repo. The
embedding engine is now the standalone project `~/embeddinggemma.c`, pure C
with no C++ and no runtime dependency other than libcurl for model download.
It is not published yet; Hexis is temporarily hard-wired to the local binary
`~/embeddinggemma.c/build/embeddinggemma-metal`.

**Serving contract**: the sidecar binds `0.0.0.0:11434` by default and serves
the DB contract already used by `get_embedding()`:
- `GET /api/tags` for health.
- `POST /api/embed` with `{"model","input":[...]}` returning
  `{"embeddings":[[768 floats]...]}`. The DB sends text prefixes itself via
  `ensure_embedding_prefix`: `search_document:`, `search_query:`,
  `clustering:`, `classification:`.

**Model** (the only one): `ggml-org/embeddinggemma-300M-qat-q4_0-GGUF`
- URL: https://huggingface.co/ggml-org/embeddinggemma-300M-qat-q4_0-GGUF/resolve/main/embeddinggemma-300M-qat-Q4_0.gguf
  (note capital `Q4_0`; lowercase 404s).
- sha256 `50d28e22432a148f6f8a86eab3700f92add5d1f54baf7790675a2a4dadbccf26`,
  277,852,192 bytes.
- The binary checks `model/embeddinggemma-300M-qat-Q4_0.gguf` relative to its
  own executable first and downloads this URL if absent. No override path.

**Project state in `~/embeddinggemma.c`**:
- `PORT_SPEC.md`, `EXTRACTION.md`, tensor manifest, token goldens, and
  embedding goldens are persisted there.
- CPU scalar engine, HTTP server, libcurl model download, and Metal backend
  exist. Local binary `build/embeddinggemma-metal` is present and executable.
- Hexis integration now starts that binary from `hexis up`; init failure
  guidance points to the sidecar; `core.cli_api.embedding_service_diagnosis`
  labels `:11434` as `embeddinggemma.c local sidecar`.
- Still before publishing: build/package the five release binaries from this
  machine, decide final artifact location, then replace the temporary local
  path with the published Hexis-managed binary path.

## Verification quick-reference

```bash
# suite (DB must be up)
POSTGRES_HOST=127.0.0.1 .venv/bin/pytest tests -q          # 2457 pass / 1 skip
# standalone embedding server
cd ~/embeddinggemma.c && make build/embeddinggemma-metal
~/embeddinggemma.c/build/embeddinggemma-metal
# Hexis integration checks
.venv/bin/python -m py_compile apps/hexis_cli.py apps/hexis_init.py core/cli_api.py
.venv/bin/pytest tests/cli/test_init_noninteractive.py -q
# regenerate goldens (only if llama.cpp bumps)
~/llama.cpp/build/bin/llama-embedding -m <gguf> -p "<s>" --pooling mean \
  --embd-normalize 2 --embd-output-format array -t 4 -ngl 0 --no-warmup
~/llama.cpp/build/bin/llama-tokenize -m <gguf> -p "<s>" --ids --no-parse-special
```

## Gotchas carried forward

- Defaulted-param SQL overloads: `DROP FUNCTION` the old signature first.
- Test fixtures: seed embeddings must embed the `ensure_embedding_prefix`
  form; `'once'` schedules need `run_at`; fixture DBs are unconfigured.
- Plugin tests use `include_bundled=False`.
- asyncpg returns JSONB as `str` (no codec registered) — `json.loads` in tests.
- macOS FS is case-insensitive: `300M-qat-Q4_0.gguf` and `300m-qat-q4_0.gguf`
  are THE SAME FILE. Never mix case variants; never symlink into app stores.
- Harness background processes get reaped between sessions — anything
  long-running (dev servers, sidecars) must be relaunched, or owned by the
  stack (`hexis up`), not by a shell.
