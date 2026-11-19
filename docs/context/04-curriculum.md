# 04. Curriculum

100 lessons in 10 parts. Each part ends at a **checkpoint**: a git tag where the reference implementation works and its tests pass. "Builds" says what the lesson adds to the project.

Lesson ids are `P.N` (part, number). Lesson files live at `course/part-P-<slug>/NN-<slug>.qmd`.

Parts 1 to 8 end with a **Review and practice** lesson: mixed exercises without step-by-step guidance, a small build task defined only by its acceptance tests, and quiz questions drawn from earlier parts. Part 9 ends with an unguided capstone. Following a guided project builds recognition; these lessons build the ability to work alone.

## Part 0: Start here (3)

| Id | Lesson | Builds |
|---|---|---|
| 0.1 | What a backend is, what we will build, how to use this course | Nothing yet: the map |
| 0.2 | Your workstation: terminal, files and paths, environment variables (Linux, macOS, Windows with WSL) | A working shell setup |
| 0.3 | Git and GitHub: commits, branches, remotes, forks, pull requests, open-source etiquette | Your fork of this repo |

## Part 1: Programming with Python (12)

| Id | Lesson | Builds |
|---|---|---|
| 1.1 | Installing Python with uv, running code, the REPL | Your `my-atlas` repository |
| 1.2 | Values, variables, types, operators | |
| 1.3 | Control flow: conditions and loops; reading code like a computer | |
| 1.4 | Functions and modules | |
| 1.5 | Collections: list, dict, set, tuple; a first feel for Big-O | |
| 1.6 | Iteration in depth: iterators, generators, comprehensions | |
| 1.7 | Text, dates and regular expressions | |
| 1.8 | Classes, objects, composition, dataclasses | |
| 1.9 | Errors, files, `pathlib`, JSON, type hints | |
| 1.10 | Packages, dependencies and project layout | `atlas` package skeleton |
| 1.11 | Testing with pytest; debugging | `atlas` CLI v0 with tests |
| 1.12 | Review and practice | Unguided: `atlas stats` command |

**Checkpoint 1:** `atlas search "event handler"` does keyword search over a local clone of PyTorch-Ignite's docs and code and prints `file:line` matches.

## Part 2: Computers and the internet (7)

| Id | Lesson | Builds |
|---|---|---|
| 2.1 | How the operating system runs your code: processes, memory, file handles, signals | |
| 2.2 | Networks: IP addresses, ports, TCP, DNS | |
| 2.3 | HTTP in depth, curl, TLS and HTTPS | |
| 2.4 | Linux servers and SSH: users, permissions, services, logs | |
| 2.5 | Web APIs and JSON: the GitHub REST API with `httpx`, tokens, pagination, rate limits | GitHub client |
| 2.6 | Concurrency: processes, threads, `async`/`await` | Concurrent fetching |
| 2.7 | Review and practice | Unguided: fetch and summarise recent commits |

**Checkpoint 2:** `atlas issues` fetches open `good first issue` issues from GitHub concurrently and caches them to disk.

## Part 3: Databases (12)

| Id | Lesson | Builds |
|---|---|---|
| 3.1 | Why databases; Docker basics; running Postgres | `compose.yml` with Postgres |
| 3.2 | SQL I: create, insert, select, update, delete | |
| 3.3 | SQL II: joins, aggregates, subqueries, CTEs, window functions | |
| 3.4 | Data modelling: keys, constraints, normalisation; designing Atlas's schema | Schema v1 |
| 3.5 | Common data patterns: timestamps, soft delete, audit history, enums, many-to-many, JSON columns | Pattern toolbox (used in Parts 4 and 8) |
| 3.6 | Indexes and query tuning: `EXPLAIN ANALYZE`, index types, reading query plans | Full-text index |
| 3.7 | Transactions and concurrency: isolation levels, locks, deadlocks, optimistic locking | Safe concurrent writes |
| 3.8 | Python and SQL: drivers, SQLAlchemy 2.0, sessions | Data layer |
| 3.9 | Choosing a data store: relational, key-value, document, search, time-series, vector | Comparison notes |
| 3.10 | Schema design exercise from a written spec, without guidance | |
| 3.11 | Testing code that touches a database | DB test fixtures |
| 3.12 | Review and practice | Unguided: issue statistics queries |

**Checkpoint 3:** repos, documents, chunks and issues live in Postgres; keyword search runs in SQL.

## Part 4: Building APIs with FastAPI (20)

This part absorbs the original 17-chapter FastAPI tutorial (tag `posts-api-v1`), retargeted from "posts" to Atlas's resources. Its depth pass ([08-depth-pass.md](08-depth-pass.md)) makes lessons failure-first and test-first from 4.5, and adds "Attack it" sections to the security lessons.

| Id | Lesson | Builds |
|---|---|---|
| 4.1 | FastAPI fundamentals: ASGI, routes, parameters, docs, `def` vs `async def` | API skeleton |
| 4.2 | Configuration and secrets with pydantic-settings | `Settings` |
| 4.3 | Schemas and domain types: parse, don't validate | Schemas, domain types |
| 4.4 | Database sessions and dependency injection | `get_session`, `/ready` |
| 4.5 | Testing the API from the start | Test client, rolled-back sessions, `/ready` returns 503 |
| 4.6 | Routers and project structure | Routers |
| 4.7 | Users and password hashing | Users |
| 4.8 | Authentication with JWT | Login, `get_current_user` |
| 4.9 | Sign in with GitHub: OAuth2 and PKCE | GitHub login |
| 4.10 | Staying signed in: refresh tokens, logout and roles | Refresh tokens, admin role |
| 4.11 | Relationships and authorisation | Saved issues, admin-only routes |
| 4.12 | Querying: pagination (offset and cursor), filtering, search | `/search` (keyword), `/issues` |
| 4.13 | Migrations with Alembic | Migrations |
| 4.14 | Error handling by design | Error format |
| 4.15 | Observability from day one: structured logs and request tracing | JSON logs, request ids |
| 4.16 | Browsers and retries: CORS, idempotency keys and API conventions | CORS, idempotency keys |
| 4.17 | Async FastAPI with async SQLAlchemy | API converted to async |
| 4.18 | API styles: REST, GraphQL, gRPC, WebSockets, and when to use each | WebSocket demo |
| 4.19 | Testing strategy | Test suite |
| 4.20 | Review and practice | Unguided: a "notes on issues" resource |

**Checkpoint 4:** Atlas API v1 with the ready-made web UI: sign in, browse issues, keyword-search the codebase.

## Part 5: Background work and integrations (7)

| Id | Lesson | Builds |
|---|---|---|
| 5.1 | Queues and workers: at-least-once delivery, idempotency, retries, dead letters | Worker + Redis queue |
| 5.2 | Scheduling and webhooks: cron, GitHub webhooks, HMAC signature checks | Scheduled sync, webhook route |
| 5.3 | Syncing with GitHub properly: conditional requests, rate limits, incremental sync by commit | Incremental sync |
| 5.4 | Object storage with the S3 API (MinIO locally) | Repo snapshots |
| 5.5 | Caching and rate limiting with Redis | Cache, per-user limits |
| 5.6 | Publish/subscribe and event streams: Redis Streams, Kafka concepts, consumer groups | `issue.updated` events |
| 5.7 | Review and practice | Unguided: a daily digest job |

**Checkpoint 5:** indexing and syncing happen in the background; new commits and issues flow in automatically.

## Part 6: AI engineering: Atlas (9)

| Id | Lesson | Builds |
|---|---|---|
| 6.1 | LLMs for engineers: tokens, context windows, effort, cost, latency, failure modes | |
| 6.2 | The provider interface: Claude via the Anthropic SDK, local models via Ollama, streaming, retries, cost logging | `llm/` package |
| 6.3 | Prompts and structured outputs validated with Pydantic | Issue fit scoring |
| 6.4 | Embeddings and vector search with pgvector | Vector column, HNSW index |
| 6.5 | Ingestion: chunking Markdown by heading and Python by `ast`, incremental re-index | Ingest pipeline |
| 6.6 | Retrieval: hybrid search, reciprocal rank fusion, filters | `/search?mode=hybrid` |
| 6.7 | RAG answers with citations, streamed over SSE, conversation history | `/ask` |
| 6.8 | Evaluating RAG: datasets, recall@k, answer grading, regression checks in CI | `evals/` |
| 6.9 | Review and practice | Unguided: "explain this file" feature |

**Checkpoint 6:** Atlas answers questions about PyTorch-Ignite with `file:line` citations, and its quality is measured.

## Part 7: Production on AWS (14)

| Id | Lesson | Builds |
|---|---|---|
| 7.1 | Containers for real: multi-stage Dockerfiles, the full compose stack | Production images |
| 7.2 | Continuous integration: lint, types, tests, dependency audit, image builds | `ci.yml` |
| 7.3 | AWS foundations: accounts, IAM, regions, VPC, budgets and billing alarms | AWS account, budget |
| 7.4 | Infrastructure as code with Terraform | `infra/` |
| 7.5 | Deploying Atlas: ECR, ECS Fargate, RDS, S3, SQS, ALB, Secrets Manager, CloudFront | Live environment |
| 7.6 | Continuous delivery: deploy pipeline, migrations on deploy, rollbacks | `deploy.yml` |
| 7.7 | Databases in production: replication, backups and a restore drill, connection pooling, zero-downtime schema changes | Backup policy, pooler |
| 7.8 | Observability: structured logs, metrics, traces, LLM cost dashboards, alerts | Dashboards, alarms |
| 7.9 | Reliability: health checks, graceful shutdown, timeouts, retries, circuit breakers | Hardening |
| 7.10 | Security across the system: threat modelling, OWASP Top 10, secrets, supply-chain security | Threat model, scans |
| 7.11 | Privacy and personal data: what to store, retention, deletion | Data retention job |
| 7.12 | Performance: load testing, profiling, capacity planning | Load test script |
| 7.13 | Incident response: on-call, runbooks, blameless postmortems | Runbooks |
| 7.14 | Review and practice | Unguided: a game-day failure drill |

**Checkpoint 7:** Atlas runs on AWS, deployed by CI/CD, observable, secure and within budget.

## Part 8: AI engineering: Atlas Agents (10)

| Id | Lesson | Builds |
|---|---|---|
| 8.1 | What an agent is: the loop, tools, stopping, and when not to build one | Minimal loop |
| 8.2 | Designing tools: strict schemas, errors as data, MCP | Tool registry |
| 8.3 | Durable runs: a state machine in Postgres, the agent worker, pause, resume, cancel | `agent_runs`, `run_steps` |
| 8.4 | Sandboxing untrusted code: per-repo images, no network, resource limits | `sandbox/`, Ignite image |
| 8.5 | Issue scouting: syncing good first issues, fit scoring against a skills profile | Ranked issues |
| 8.6 | The reproduction loop: write, run, verify, run the project's checks, bounded iterations | Repro step |
| 8.7 | Human in the loop and the Issue Brief: approvals, the explain-it-back gate, live progress over SSE | Briefs, run view |
| 8.8 | Security for agents: prompt injection, tool permissions, audit trails | Guardrails |
| 8.9 | Evaluating agents: replaying closed issues, file-overlap score, cost and latency budgets | Agent evals |
| 8.10 | Review and practice | Unguided: a new read-only agent tool |

**Checkpoint 8:** Atlas Agents produces verified Issue Briefs on AWS.

## Part 9: Scale and career (6)

| Id | Lesson | Builds |
|---|---|---|
| 9.1 | Distributed systems fundamentals: consistency, CAP, idempotency, why exactly-once is rare, clocks | |
| 9.2 | System design fundamentals: scaling, caching layers, partitioning, load balancing | Design doc |
| 9.3 | Services and events: splitting a service, the outbox pattern | Outbox for run events |
| 9.4 | Case studies and interview practice | |
| 9.5 | Unguided capstone: build a notifications service for Atlas from a written spec | `notifier/` service |
| 9.6 | Telling your story: README, demo, write-up; contributing to open source; GSoC proposals | Portfolio |

**Checkpoint 9:** the finished project, documented and presented, plus one service the learner built without guidance.

## Every lesson has

1. An analogy box
2. Theory
3. At least one diagram where a picture explains better than text
4. The project code for this step, with "why" comments
5. Common mistakes
6. A quiz (3 questions with explanations)
7. A guided exercise with a hidden solution
8. A stretch challenge with a hidden solution
9. "What changed in the project" (files touched, link to the diff between checkpoints)

Review and practice lessons use their own template. Both templates are in [06-conventions.md](06-conventions.md).
