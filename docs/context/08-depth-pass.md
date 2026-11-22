# 08. Depth pass: Parts 4 and 7

Status: **approved** (2026-11-18), decision D22. Progress is tracked in [05-roadmap.md](05-roadmap.md) as milestone D1.

## Why

Part 4 covers the right topics, but at about 1,500 words a lesson it often states the answer before the learner feels the problem. The bar is a book like *Zero To Production In Rust*: one project, each problem met before it is solved, and long explanations of why. Four themes get the extra depth:

1. **Test-first narrative.** From the moment tests are possible, each feature starts with a failing test.
2. **Errors and domain types.** Error handling is designed, not improvised; invalid data can't get past the edge ("parse, don't validate").
3. **Observability early.** Structured logs and request tracing arrive in Part 4, so every later part produces searchable evidence.
4. **Security reasoning.** Each defence is shown by first carrying out the attack against Atlas.

## Lesson template changes

These apply to Part 4 now and to every lesson written from now on:

- **Starts with a failure.** After the analogy, a "The problem" section reproduces a real failure (a failing test, a leaked field, a stalled server, a forged token) with real output.
- **"Attack it" section** in security lessons: the attack, its real output against an undefended version, then the defence and the same attack failing.
- **Test-first walkthrough** once tests exist (from new 4.5): failing test, then code, then passing test.
- **Depth target:** 2,500 to 4,000 words for a core lesson. Longer means split.
- **Trade-offs section** where a real choice exists: the alternatives considered and when each wins.

## Part 4: from 16 to 20 lessons

| New | Lesson | Comes from | What changes |
|---|---|---|---|
| 4.1 | FastAPI fundamentals | 4.1 | Opens with the blocking-server failure; more on ASGI and the request lifecycle |
| 4.2 | Configuration and secrets | 4.2 | Opens with a leaked secret in a traceback |
| 4.3 | Schemas and domain types: parse, don't validate | 4.3 | **Deepened.** Domain types (`RepoFullName`, normalised `Email`, `Password`) so routes receive values that are already valid; the cursor type follows in 4.12, where cursors are taught |
| 4.4 | Database sessions and dependency injection | 4.4 | Opens with a leaked connection pool |
| 4.5 | Testing the API from the start | **new** | Sync test client, rolled-back session, dependency overrides. Every later lesson is test-first |
| 4.6 | Routers and project structure | 4.5 | Adds the trade-off discussion (layers vs deep modules) with a worked counter-example |
| 4.7 | Users and password hashing | 4.6 | Attack it: cracking a leaked SHA-256 table vs Argon2, measured |
| 4.8 | Authentication with JWT | 4.7 | Attack it: tampered payload, `alg: none`, expired token, token in a URL |
| 4.9 | Sign in with GitHub: OAuth2 and PKCE | 4.8 (split) | Attack it: login CSRF without `state`; intercepted code without PKCE |
| 4.10 | Staying signed in: refresh tokens, logout and roles | 4.8 (split) | Attack it: stolen refresh token replayed; reuse detection catching it |
| 4.11 | Relationships and authorisation | 4.9 | Attack it: reading another user's notes by id (IDOR); mass assignment (`"role": "admin"` in a PATCH) |
| 4.12 | Pagination, filtering and search | 4.10 | Measured: deep offset vs cursor timings; N+1 query counts |
| 4.13 | Migrations with Alembic | 4.11 | Opens with autogenerate destroying data on a rename |
| 4.14 | Error handling by design | 4.12 (split) | **Deepened.** An error taxonomy, domain errors mapped to HTTP in one place, what clients and operators each need |
| 4.15 | Observability from day one: structured logs and request tracing | 4.12 (split) + **new** | JSON logs, request id in every log line (`contextvars`), request timing; debugging a real failure from logs alone |
| 4.16 | Browsers and retries: CORS, idempotency keys and API conventions | 4.12 (split) | Attack it: a page on another origin calling the API; a double-submitted POST |
| 4.17 | Async FastAPI with async SQLAlchemy | 4.13 | Converts the tests too; adds the missing regression test for lazy loads |
| 4.18 | API styles | 4.14 | Unchanged apart from the template |
| 4.19 | Testing strategy | 4.15 | **Deepened.** Since basics moved to 4.5: what to test at which level, fakes vs mocks, OpenAPI contract snapshot, coverage as a question not a target |
| 4.20 | Review and practice | 4.16 | Adds a security review exercise: find and fix three planted vulnerabilities |

The curriculum grows from 96 to 100 lessons. Parts 5, 6, 8 and 9 keep their numbers.

### Reference code changes (ship with the lessons)

- `api/schemas.py`: domain types as `Annotated` Pydantic types; email normalised in the type, not in routes. (Done in block 1, which also found and fixed two real bugs: startup errors printed secrets, and `PATCH /users/me` with `null` answered 500. Block 2 (4.6 to 4.10) found a third: the API started with a guessable signing key, which let a forged token sign in as any user; it now requires 32 characters. Block 4 (4.16 to 4.20) completed Part 4, adding a lazy-load regression test, a contract snapshot and coverage reporting; coverage itself found the sign-in redirect untested. Block 3 (4.11 to 4.15) found four more: unknown routes and 405s bypassed the error format, 500s lacked the `X-Request-ID` header, unknown request fields were silently ignored, and logs could not be searched by request id.)
- Error handling: a small set of domain exceptions with a stable code and status, mapped once in `app.py`. Kept as one module; no per-resource exception classes.
- New `atlas/logging.py`: JSON log formatter and a request-id context variable; request log line with method, path, status and duration.
- Tests: synchronous API fixtures shown in 4.5 (kept in the lesson, converted in 4.17); new tests for every "Attack it" (alg none, mass assignment, IDOR, foreign origin, replayed refresh token); a lazy-load regression test using a fresh session.

### How it gets done

1. Renumbering script: maps old to new ids in `04-curriculum.md`, every lesson and every context doc (about 80 references to Part 4 lessons across 26 files today), then regenerates part pages. Run first, alone, in its own commit, so the text changes stay reviewable.
2. Code changes, test-first, each with its lesson. Intermediate (synchronous) states shown in lessons are run for real from a scratch copy of the sync checkpoint, as in M4.
3. Lessons rewritten in order 4.1 to 4.20.
4. Checks: tests, lint, types, migrations, site render, browser check; then one reader pass against the template checklist.

Estimated size: about 60,000 words of lessons (from 23,700). Delivered as one PR per block of five lessons, so each can be reviewed.

## Part 7: detailed design (written in M7)

Part 7 can't be written before Parts 5 and 6 exist: its lessons deploy their code. This design fixes its depth now. Each lesson starts from a failure Atlas actually has at that point.

| Id | Lesson | Starts from this failure | Depth targets | Real evidence shown |
|---|---|---|---|---|
| 7.1 | Production containers | A 1.5 GB image that runs as root and rebuilds on every change | Multi-stage builds, layer caching, non-root user, image scanning | Image sizes and build times, before and after |
| 7.2 | Continuous integration | A broken migration merged on Friday | Required checks, dependency audit, image build, caching | A red build blocking a bad PR |
| 7.3 | AWS foundations | An access key committed to a repo; a surprise bill | IAM least privilege, roles not keys, budget alarm first, regions | The budget alarm email; a denied IAM call |
| 7.4 | Infrastructure as code with Terraform | Click-ops drift between two environments | State, plans, modules kept shallow, remote state locking | `terraform plan` diffs |
| 7.5a | The network and the data: VPC, RDS, S3, SQS | A database reachable from the internet | Subnets, security groups, private RDS, no NAT gateway (cost) | A blocked connection from outside |
| 7.5b | Running Atlas: ECR, ECS Fargate, ALB, Secrets Manager | A task that starts, fails health checks and loops | Task definitions, health checks from 4.4, secrets injection, autoscaling basics | ECS events of a failing then healthy deploy |
| 7.5c | The front door: CloudFront, domain, TLS | Mixed content and a stale UI after deploy | Cache behaviours, invalidation, HTTPS everywhere, CORS from 4.16 | Response headers through CloudFront |
| 7.6 | Continuous delivery | A deploy that ran migrations after the new code | Migrate-then-deploy, expand-and-contract, rollback | A rollback performed for real |
| 7.7a | Backups and a restore drill | "We have backups" that nobody has restored | Point-in-time recovery, restore into a new instance, RPO and RTO | Measured restore time |
| 7.7b | Databases under load: pooling and zero-downtime schema changes | Connection exhaustion at 50 tasks; a table lock during deploy | RDS Proxy or pgbouncer, `CREATE INDEX CONCURRENTLY`, lock timeouts | `pg_stat_activity` before and after |
| 7.8 | Observability in production | An error report with only "it's slow" | Builds on 4.15's logs: metrics, traces (OpenTelemetry), LLM cost dashboard, alerts that page on symptoms | A trace of one slow `/ask` request |
| 7.9 | Reliability | One slow GitHub call taking the API down | Timeouts, retries with jitter, circuit breaker, graceful shutdown | A fault injected and contained |
| 7.10 | Security across the system | Builds on Part 4's attacks | Threat model of the whole system, OWASP Top 10 mapped to Atlas, supply chain, secrets rotation | A scan report and the fixes it led to |
| 7.11 | Privacy and personal data | Deleted accounts still in backups and logs | Data inventory, retention job, deletion that reaches logs | The retention job's run |
| 7.12 | Performance | A guess about the bottleneck that was wrong | Load testing, profiling, capacity planning, cost per request | Load test results and a profile |
| 7.13 | Incident response | A real outage from the 7.14 drill, replayed | On-call, runbooks, blameless postmortems | A written postmortem |
| 7.14 | Review and practice | | Game-day failure drill | |

Part 7 grows from 14 to 17 lessons (7.5 and 7.7 split). Final curriculum: 103 lessons. The split ids (7.5a and so on) became proper numbers when M7 started (D31): 7.5a to 7.5c are 7.5 to 7.7, 7.6 is 7.8, 7.7a and 7.7b are 7.9 and 7.10, and 7.8 to 7.14 are 7.11 to 7.17.

## Risks

- **Renumbering** touches many files at once. Mitigation: a script, its own commit, and a link check on the rendered site.
- **Length vs momentum**: a truly-zero learner may stall in longer lessons. Mitigation: each lesson keeps a "minimum path" (sections marked optional) and the 4.20 self-check.
- **Accuracy at greater length**: more claims to verify. The rule stays: every output is captured from running code.
- **Cost (Part 7)**: depth means more AWS resources. The budget alarm stays the first step, and each lesson ends with teardown.
