# 07. Decisions

Newest first. Each entry: the decision, why, and what was rejected. Superseded entries stay, marked as such.

---

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
