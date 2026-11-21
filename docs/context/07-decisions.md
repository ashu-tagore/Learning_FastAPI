# 07. Decisions

Newest first. Each entry: the decision, why, and what was rejected. Superseded entries stay, marked as such.

---

### D30. Answers and evaluation as built in M6 block C (2026-11-20)
**Decision:** `POST /ask` streams Server-Sent Events with FastAPI's `EventSourceResponse` (`sources`, `delta`, `done`, `error`); checks that must be a 404 run in a dependency before the stream; nothing is stored unless the answer completes. Five hybrid-search sources go into the prompt, numbered, after up to three earlier turns without their sources; the citation rule is repeated after the sources and shows only the format. A follow-up is searched together with the previous question. Below a nearest-chunk cosine similarity of 0.6 (measured for `nomic-embed-text`), Atlas declines without calling a model. Citations are checked against the sources and stored as copies with the indexed commit. `POST /explain` explains one file from its first six chunks with the same prompt. The web page reads the stream with `fetch` and builds links from text nodes. Evaluation data are files: `evals/ignite.jsonl` (25 answerable and 5 unanswerable questions, checked against Ignite at `0abbcab`), `evals/hand_grades.jsonl` (10 answers graded by a person), `evals/thresholds.json`; each run writes a JSON report, kept as a CI artifact. A separate workflow, `evals.yml`, indexes and embeds Ignite at the pinned commit and runs the full evaluation on every pull request touching `atlas/` or `evals/`. Thresholds gate only exact checks (hybrid recall@5 and MRR, citing an expected file, valid citations, wrong and correct refusals), set below the worst of three measured runs; `correctly_refused` sits at 0.6, the three everyday questions the similarity gate declines without a model, so model randomness can't fail it (the first CI run scored exactly 0.6). A refusal counts only when the answer says so and cites nothing.
**Why:** measured: the citation rule in the system prompt alone produced an uncited answer, repeated at the end it produced citations; the similarity gate separates everyday questions (0.55 to 0.59) but not technical ones the code can't answer (0.65), which the prompt must catch (Core ML was declined in one run of three). The local judge (`qwen3:1.7b`) agreed with the hand grades on 4 of 10 answers, calling all supported, in both a verdict design and a claim-by-claim design, so its verdict is reported and never gated. Files instead of tables because cases and grades are reviewed like code and CI's database is thrown away after each run. Real bugs found by the evaluation: the citation rule's example sentence copied into an unrelated answer, and a refusal check that counted a made-up answer ending in "I couldn't find this" as declining.
**Rejected:** a judge-based threshold (untrustworthy judge); `EventSource` in the page (GET only, no headers); storing partial answers; query rewriting by a model (a model call per follow-up); the planned `eval_cases` and `eval_results` tables; a cached index in CI (the evaluation would no longer measure indexing).

### D29. Embeddings and hybrid search as built in M6 block B (2026-11-20)
**Decision:** chunks are structural: Python by `ast` (module header, each function, each method as `Class.method`, class headers and the gaps between), Markdown and reStructuredText by heading path, anything else (and Python that doesn't parse) in 40-line windows; no chunk exceeds 60 lines. Vectors live in `chunks.embedding` (`vector(768)`, pgvector 0.8 on the `pgvector/pgvector:pg17` image) with an HNSW cosine index, and each chunk records its `embedding_model`. What is embedded is the file path, symbol and text, with `nomic-embed-text`'s `search_document:`/`search_query:` prefixes. The `embed_repo` job embeds pending chunks in batches of 16, one transaction per batch, for about three minutes per run, then re-queues itself with the "continued" outcome without counting an attempt; it runs outside the job's transaction. Re-indexing keeps the vector of any new chunk identical to an old chunk in the same file. Search has three modes (`keyword`, `semantic`, `hybrid`, default hybrid); hybrid takes 50 candidates from each and merges them with reciprocal rank fusion (k = 60). Semantic search sets `hnsw.ef_search` to the number of candidates for its transaction. Without the embedding model, search falls back to keywords and says so in `X-Search-Mode`. `repos.embedded_at` is part of the search cache's version.
**Why:** measured on Ignite: structural chunks kept 96% of functions whole against 69% for windows; hybrid found the answer in the top five for 8 of 10 questions, against 3 for keywords and 6 for semantic search, at single-digit milliseconds plus 48 ms to embed the question. Embedding the repository takes about 29 minutes on a CPU, which is longer than the 10-minute job lease, hence slices; reusing vectors cut a 30-commit re-sync from 1026 chunks to embed to 142. Real failures found while building: a job that never progressed when a slice ended before its first batch, embedded batches rolled back by the job's outer transaction, and HNSW silently returning 40 rows for a `LIMIT 50` (pgvector's default `ef_search`).
**Rejected:** fixed-size windows for everything; a separate vector database (D8); min-max score fusion (tied RRF on the ten questions, and depends on score scales); weighted fusion and rerankers until lesson 6.8 can measure them; one long embedding job; failing search with 503 when embeddings are down.

### D28. Local models by default; Claude built and tested offline (2026-11-20)
**Decision:** Part 6 runs on Ollama by default: `qwen3:1.7b` for chat and `nomic-embed-text` for embeddings, as a compose service (`ollama/ollama:0.40.16`). Claude is a configured alternative (`LLM_PROVIDER=anthropic`, default model `claude-opus-5-5`, effort `medium`, server-side refusal fallbacks on the beta endpoint), built on the official `anthropic` SDK (2.11.0, which uses `httpx2`) and tested against a fake of the Messages API at the HTTP layer. Lessons show real outputs from the local models only and say so wherever Claude code appears. Lesson 6.8's judge is the local model, checked against hand grades. Every call is logged to `llm_calls` with its cost (zero for local models). Structured output requests carry a Pydantic model: Ollama receives its JSON schema; the Claude provider passes it to `messages.parse`, which moves constraints Claude's schemas don't support into client-side checks.
**Why:** the owner chose not to provide an API key. Local models make every lesson runnable for free, and their mistakes (made-up functions, scores that contradict their reasons) are teaching material. The authoring machine couldn't reach Ollama's registry or Hugging Face, so the same models were imported from Docker Hub's `ai/` namespace as GGUF files; learners use `ollama pull`.
**Rejected:** Claude as the default without a key (lessons couldn't show real output); fabricating Claude outputs; a hosted embedding API (needs a key too).
**Supersedes:** "default model `claude-opus-5-5`" as Atlas's default in D13 and the architecture doc; Claude remains supported.

### D27. Background work as built in M5 block B (2026-11-19)
**Decision:** snapshots are `git archive` tarballs keyed by commit in S3-compatible storage, made by their own `snapshot_repo` job, uploaded through a temporary file, pruned to the newest three by commit time (object metadata), and shared through 5-minute SigV4 presigned links; boto3 finds credentials itself (environment locally, IAM role on AWS). Search is cached in Redis under keys that include the repository's `indexed_at` version, and rate-limited per client address (60 a minute) and syncs per user (5 a minute) with a sliding window log; both fail open. The issues sync publishes `issue.updated` events (opened, relabelled, closed) to a Redis Stream before its transaction commits, each carrying who had saved the issue and a key built from the poll's starting watermark; the notifier consumer group writes notifications idempotently. The daily digest is an idempotent command, not a job kind. Partial-index conflict targets are literal SQL.
**Why:** snapshots exist for downloads and Part 8's sandbox; restoring a worker's clone from a tarball was dropped because a tarball has no Git history for the next incremental diff. A separate job keeps an object-storage outage from stopping indexing. Measurement showed search was slow for lack of statistics, not for lack of a cache; the cache remains for database load. Events are facts about GitHub, so publishing before commit is safe, and publishing after would risk loss; a closed issue's `saved_issues` rows are deleted with it, so the audience travels in the event. Real failures found while building: boto3 1.44 signing presigned URLs with deprecated SigV2 unless configured, `LastModified` at one-second resolution, autogenerate missing CHECK constraint changes, `ON CONFLICT` with a parameterised partial-index condition failing on the 11th run once prepared, `XREADGROUP BLOCK 0` waiting forever, and acknowledgement-ordering bugs no test caught (now tested by simulated crashes).
**Rejected:** snapshots in Postgres (`bytea`), proxying downloads through the API, a fixed-window or token-bucket limiter (boundary bursts; Lua), invalidating cache entries on write, Redis pub/sub for events (drops events when nobody listens), thin events, an outbox table (unnecessary for facts about GitHub; kept for events about Atlas's own writes).

### D26. Background jobs as built in M5 block A (2026-11-19)
**Decision:** the queue message is a bare job id; job state is a `jobs` row with a partial unique index allowing one pending job per kind and repository. Workers claim a job with one conditional `UPDATE` and a 10-minute lease, run it in the same transaction that marks it succeeded, retry with full-jitter backoff up to 5 attempts, then mark it dead. A GitHub rate limit defers the job to the reset time without using an attempt. Producers commit before sending; the scheduler re-sends jobs that never started after 5 minutes. Webhooks are checked with HMAC-SHA256 on the raw body. Code sync is incremental by `git diff --name-status --no-renames` from `repos.indexed_commit`; issue sync uses `since`, `state=all`, oldest-first ordering and ETags. The worker's blocking queue wait (2 seconds) stays below redis-py 8's default 5-second socket timeout.
**Why:** at-least-once delivery plus idempotent jobs is the only combination that neither loses nor double-applies work; the database is the one place a uniqueness rule holds across processes; the reference code had to survive real failures found while building it (a lost job on a plain list, a 20-request race, the socket-timeout crash).
**Rejected:** job data in the message (stale on redelivery); check-then-insert deduplication (races); acknowledging before running (loses jobs); time-based watermarks from Atlas's own clock (skew loses updates); Celery or RQ (hide the mechanisms the lessons teach, per D7).

### D25. SeaweedFS replaces MinIO for local object storage (2026-11-19)
**Decision:** the local S3-compatible server in `compose.yml` and CI is SeaweedFS. Atlas talks to it only through the S3 API, so code and lessons are the same against AWS S3 (Part 7).
**Why:** MinIO deleted its community images from Docker Hub in September 2026 and ships the community edition as source code only; its Quay mirror is unreliable and unreachable from this project's build environment. SeaweedFS is Apache-2.0, long maintained, and runs as one container with an S3 gateway.
**Rejected:** building MinIO from source (a toolchain to explain for a stand-in); RustFS (promising, but young); Garage (AGPL-3.0, extra first-start layout step); LocalStack (heavier, changing free-tier terms).
**Supersedes:** "MinIO locally" in the curriculum and architecture docs.

### D24. Testing strategy: coverage reported, contract pinned (2026-11-19)
**Decision:** CI prints coverage (with greenlet-aware measurement) but sets no threshold; a committed snapshot of the OpenAPI paths, methods and status codes is compared on every run; fakes stand in only for external services. Recorded in [06-conventions.md](06-conventions.md) under "Tests".
**Why:** coverage found a real untested security property (tokens in the sign-in redirect), but a threshold rewards tests that run lines without checking them; a route rename broke the web UI's contract while every behaviour test could pass.
**Rejected:** a coverage gate (encourages hollow tests); generated client tests (another toolchain for what one snapshot test covers); automated mutation testing in CI (too slow per commit; done by hand for security rules instead).

### D23. Stable error codes, JSON logs and strict request bodies (2026-11-19)
**Decision:** routes raise one `ApiError` class carrying a status and a stable code; Starlette's own HTTP errors and crashes use the same JSON shape; every request is logged as one JSON line with its request id (a `ContextVar` stamped on each record); request schemas refuse unknown fields. Recorded in [06-conventions.md](06-conventions.md) under "API rules".
**Why:** the depth pass found unknown routes and 405s bypassing the error format, 500s without an `X-Request-ID` header, two different failures sharing one code, logs that couldn't be searched by request id, and unknown fields (such as `"role"`) being silently ignored.
**Rejected:** one exception class per error (shallow classes); RFC 9457 problem details (a better fit for a public API with unknown clients; Atlas's clients are its own); structlog (a dependency for what ~50 lines of standard library do here).

### D22. Depth pass: Part 4 grows to 20 lessons; Part 7 designed in depth (2026-11-18)
**Decision:** lessons start from a reproduced failure, are test-first from 4.5, and security lessons carry out the attack before the defence. Part 4 is renumbered: new 4.5 (testing basics); old 4.8 splits into 4.9 and 4.10; old 4.12 splits into 4.14, 4.15 (observability, new) and 4.16; the rest shift (old 4.13 to 4.17, 4.14 to 4.18, 4.15 to 4.19, 4.16 to 4.20). The curriculum totals 100 lessons. Part 7's lesson specs are fixed in [08-depth-pass.md](08-depth-pass.md) and written in M7. Lesson numbers in D16 and D17 refer to the old numbering (4.13 is now 4.17; 4.8 is now 4.9 and 4.10).
**Why:** the course aims at the depth of the best single-project backend books; Part 4 stated answers before learners met the problem.
**Rejected:** deepening in place without splitting (lessons of three hours); adding deep-dive lessons beside unchanged ones (two styles in one part).

### D21. Migrations live inside the package and start from Part 3's schema (2026-11-18)
**Decision:** Alembic's scripts live in `src/atlas/migrations/`. Revision 0001 is the Part 3 schema, 0002 the Part 4 tables, 0003 indexes; `atlas init-db` runs `upgrade head`. Existing Part 3 databases adopt migrations with `alembic stamp 0001`. Revision ids are renamed to sequential numbers. CI runs upgrade, downgrade, upgrade and `alembic check` on an empty database.
**Why:** the CLI and the Docker image can find the migrations wherever Atlas is installed; learners keep the data they indexed in Part 3; sequential ids read in order.
**Rejected:** a top-level `alembic/` folder (not shipped with the package); one squashed initial migration (would break learners' existing databases).

### D20. GitHub sign-in uses PKCE as well as state, and asks for no scopes (2026-11-18)
**Decision:** the OAuth flow sends an S256 PKCE challenge and a random state, both kept in one short-lived HttpOnly cookie. No `scope` parameter is sent, which grants read-only access to public information.
**Why:** GitHub's documentation strongly recommends PKCE for OAuth apps; no scope is the narrowest grant and keeps the rule that Atlas never writes to GitHub. Refines D17.

### D19. Ready-made web UI is plain HTML, CSS and JavaScript served by the API at /app (2026-11-18)
**Decision:** `web/` holds three static files with no build step, served by FastAPI's `StaticFiles` when `WEB_DIR` is set. Tokens are kept in `sessionStorage`; the page loads no third-party scripts. GitHub sign-in hands tokens to the page in the URL fragment.
**Why:** same origin as the API (no CORS needed locally), nothing to install, and every line is readable by a learner even though the course doesn't teach frontend (D10).
**Rejected:** HttpOnly cookie sessions for the UI (needs CSRF protection, taught later in 7.10).

---

### D18. Learners keep their code in their own `my-atlas` repository (2026-11-17)
**Decision:** each learner creates a separate repository, `my-atlas`, in lesson 1.1. The course repo is cloned only for reading lessons and the reference code.
**Why:** the planned git-ignored `workspace/` folder inside the course repo meant learners could never commit their own work, which defeats Part 0's Git lessons. A repository of their own also becomes their portfolio.
**Supersedes:** the `workspace/` folder in the original M0 plan.

### D17. GitHub sign-in with identity scopes only (2026-11-17)
**Decision:** lesson 4.8 adds "Sign in with GitHub" (OAuth2), refresh tokens and an `admin` role. The OAuth app requests identity scopes only.
**Why:** real products rarely stop at password login, and the learner will meet OAuth everywhere. Identity-only scopes keep the rule that Atlas can never write to GitHub.

### D16. The API becomes async in lesson 4.13 (2026-11-17)
**Decision:** Part 4 builds the API with synchronous SQLAlchemy first, then converts it to async FastAPI with async SQLAlchemy in 4.13. The reference API is async from Checkpoint 4 onward.
**Why:** sync code is easier to learn first; most production FastAPI code is async, and converting a working API shows exactly what changes and why.

### D15. Curriculum expanded from 68 to 96 lessons (2026-11-17)
**Decision:** add operating-system and Linux server basics, more Python practice, a deeper database part (query tuning, concurrency and locking, data patterns, choosing data stores, an unguided design exercise), async APIs, OAuth and roles, API styles, event streams, database operations, a security thread, privacy, incident response, distributed systems, a review-and-practice lesson closing each of Parts 1 to 8, and an unguided capstone.
**Why:** a review against the "zero to production" promise found thin coverage (Python practice, databases, security) and missing topics (OS, async data access, OAuth, event streams, incidents, distributed systems). Guided projects alone create an illusion of competence, so unguided practice is built in.
**Cost:** roughly 40% more lessons.

### D14. Own agent loop on the Messages API, not a hosted agent service (2026-11-17)
**Decision:** Atlas Agents runs its own agent loop and its own sandbox.
**Why:** the provider must be swappable, including local models through Ollama, and building the loop, tools, state machine and sandbox is the core learning goal of Part 8.
**Rejected:** a hosted agent service (less code to own, but ties the project to one provider and hides exactly what the learner needs to understand). Mentioned in lesson 8.1 as the right choice in many real projects.

### D13. Swappable LLM providers behind one small interface (2026-11-17)
**Decision:** `ChatProvider` and `EmbeddingProvider` interfaces. Hosted chat through the official `anthropic` SDK (default model `claude-opus-5-5`, configurable); local chat and default embeddings through Ollama.
**Why:** owner's requirement; free local runs for exercises; hosted quality when needed.
**Rejected:** routing Claude through OpenAI-compatible shims (loses SDK features and typed errors); a large multi-provider framework (hides the concepts being taught).

### D12. The Issue Brief contains no patch (2026-11-17)
**Decision:** the brief explains, locates and reproduces; it never includes code for the fix.
**Why:** PyTorch-Ignite's `AGENTS.md` requires the user to understand the task and forbids agent-authored PR text; GSoC mentors judge the human's understanding. Supersedes an earlier idea of keeping draft fix code locally.

### D11. Target project: PyTorch-Ignite (2026-11-17)
**Decision:** one target, `pytorch/ignite`.
**Why:** its `CONTRIBUTING.md` explicitly allows AI tools and `AGENTS.md` sets clear rules the product can encode; clear `good first issue` label; documented checks. sktime's contribution site could not be read during evaluation, so its policy was unknown.
**Rejected for now:** sktime and ML4SCI (ML4SCI is an umbrella of many repositories). Revisit after Checkpoint 8.

### D10. Ready-made web UI in plain HTML/CSS/JS (2026-11-17)
**Decision:** a small static frontend with no build step, explained but not taught.
**Why:** keeps the course on backend; no Node toolchain; deploys to S3 and CloudFront as plain files.
**Rejected:** teaching frontend (about 4 extra lessons); React (build tooling to explain).

### D9. One product in stages: Atlas, then Atlas Agents (2026-11-17)
**Decision:** one codebase; the agent platform builds on Atlas, and Atlas search is the agents' first tool.
**Why:** avoids building auth, data and deployment twice; gives a genuine reason for a separate worker service.
**Rejected:** two separate projects. Earlier product framings (a document librarian; a research-paper copilot) were replaced by the open-source-contribution focus that serves the owner's GSoC goal.

### D8. Postgres for everything, including vectors (2026-11-17)
**Decision:** Postgres with pgvector and full-text search; tests on real Postgres.
**Why:** one database to learn and operate; hybrid search in one query; transactional consistency between chunks and their vectors.
**Rejected:** a separate vector database; SQLite for tests (no pgvector, different behaviour).

### D7. Queue behind an interface: Redis locally, SQS on AWS (2026-11-17)
**Decision:** a small `Queue` interface; messages carry ids only; state lives in Postgres.
**Why:** cheap local development, managed queue in production, idempotent and inspectable jobs.
**Rejected:** Celery (a large framework that hides the at-least-once and idempotency lessons).

### D6. Deploy on AWS with Terraform and ECS Fargate (2026-11-17)
**Decision:** AWS, everything in Terraform, ECS Fargate, RDS, S3, SQS, CloudFront, Secrets Manager; no NAT gateway; budget alarm first.
**Why:** owner's choice of AWS; Fargate avoids managing servers while still teaching containers; Kubernetes is introduced conceptually only.
**Rejected:** Kubernetes as the deployment target (too much for a truly-zero learner); click-ops.

### D5. Course site built with Quarto, published to GitHub Pages (2026-11-17)
**Decision:** lessons are `.qmd` files in `course/`, rendered by Quarto in CI and published to GitHub Pages. Layout follows the familiar Quarto course style: docked sidebar of all lessons, top navbar, right-hand table of contents, search, light and dark themes.
**Why:** owner wanted a sidebar-of-contents course site; lessons live in git next to the code; free hosting for a public repo.
**Rejected:** a single published HTML page (the earlier 17-chapter tutorial); MkDocs (also fine, but Quarto handles notebooks and diagrams natively).

### D4. Fold the original tutorial into the new course (2026-11-17)
**Decision:** the 17 FastAPI chapters and the `solutions/` reference become Part 4, retargeted to Atlas. Exact past states are preserved by tags `posts-api-v1` and `posts-api-solution-v1`.
**Why:** one storyline, no duplicated lessons, nothing lost.
**Rejected:** keeping the old tutorial as a separate archive (duplicate material that drifts out of date).

### D3. Starting level: truly zero (2026-11-17)
**Decision:** the course starts at "what is a terminal" and "what is a variable".

### D2. Concepts language-agnostic, Python/FastAPI as the vehicle (2026-11-17)

### D1. Repository purpose: zero to production backend for AI engineering (2026-11-17)
**Decision:** the repo becomes "Zero to Production in FastAPI", a full course plus one growing project, instead of a FastAPI learning sandbox.
