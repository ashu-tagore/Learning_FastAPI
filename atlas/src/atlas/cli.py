"""The `atlas` command line.

atlas search "query" --path ~/code/ignite
atlas stats --path ~/code/ignite
atlas issues --repo pytorch/ignite
atlas commits --repo pytorch/ignite
atlas init-db
atlas index --path ~/code/ignite --repo pytorch/ignite
atlas find "event handlers" --repo pytorch/ignite
atlas issue-stats --repo pytorch/ignite
atlas worker
atlas scheduler
atlas sync pytorch/ignite
atlas jobs
atlas notifier
atlas digest --day 2026-11-19
atlas llm "What is an event in a training loop?" --stream
atlas llm-usage
atlas fit --email ada@example.com --repo pytorch/ignite --limit 5
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import redis
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from atlas import digest, fit, issue_cache, storage, worker
from atlas.config import Settings
from atlas.events import EventStream
from atlas.github import GitHubError, fetch_issues, fetch_recent_commits
from atlas.issue_cache import CachedIssues
from atlas.jobs import WorkerContext, create_job
from atlas.llm import ChatProvider, ChatRequest, ChatResult, LLMError, TextDelta, user
from atlas.llm.calls import cost_usd, make_chat, make_embeddings, record
from atlas.logs import configure_logging
from atlas.models import Issue, IssueFit, Job, LlmCall, Repo, User
from atlas.notifier import run_notifier as run_notifier_loop
from atlas.queue import RedisQueue
from atlas.search import DEFAULT_EXTENSIONS, search
from atlas.snapshots import SnapshotStore, make_s3_client
from atlas.stats import collect_stats

DEFAULT_REPO = "pytorch/ignite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="Explore a codebase.")
    commands = parser.add_subparsers(dest="command", required=True)

    search_parser = commands.add_parser("search", help="find lines containing a query")
    search_parser.add_argument("query", help="text to look for")
    search_parser.add_argument("--path", type=Path, default=Path("."), help="folder to search")
    search_parser.add_argument("-i", "--ignore-case", action="store_true")
    search_parser.add_argument("--regex", action="store_true", help="treat query as a regex")
    search_parser.add_argument("--limit", type=int, default=None, help="stop after N matches")
    search_parser.add_argument("--json", action="store_true", help="print results as JSON")
    search_parser.add_argument(
        "--ext",
        nargs="+",
        default=list(DEFAULT_EXTENSIONS),
        help="file extensions to include (default: .py .md .rst)",
    )

    stats_parser = commands.add_parser("stats", help="count files and lines by type")
    stats_parser.add_argument("--path", type=Path, default=Path("."), help="folder to measure")
    stats_parser.add_argument("--top", type=int, default=5, help="how many largest files to show")
    stats_parser.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTENSIONS))

    issues_parser = commands.add_parser("issues", help="list open issues from GitHub")
    issues_parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name on GitHub")
    issues_parser.add_argument(
        "--label",
        action="append",
        help="issue label; repeat for several (default: good first issue)",
    )
    issues_parser.add_argument("--refresh", action="store_true", help="ignore the cached copy")
    issues_parser.add_argument(
        "--max-age", type=int, default=60, help="minutes a cached copy stays valid"
    )
    issues_parser.add_argument("--cache", type=Path, default=None, help="cache file location")
    issues_parser.add_argument("--json", action="store_true", help="print results as JSON")
    issues_parser.add_argument("--save", action="store_true", help="also store them in Postgres")

    commits_parser = commands.add_parser("commits", help="list recent commits from GitHub")
    commits_parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name on GitHub")
    commits_parser.add_argument("--limit", type=int, default=10, help="how many (1 to 100)")

    commands.add_parser("init-db", help="create or upgrade the database tables")

    admin_parser = commands.add_parser("make-admin", help="give a user the admin role")
    admin_parser.add_argument("--email", required=True)

    index_parser = commands.add_parser("index", help="store a codebase in Postgres")
    index_parser.add_argument("--path", type=Path, default=Path("."), help="folder to index")
    index_parser.add_argument("--repo", default=DEFAULT_REPO, help="name to store it under")
    index_parser.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTENSIONS))

    find_parser = commands.add_parser("find", help="ranked full-text search in Postgres")
    find_parser.add_argument("query", help="words to look for")
    find_parser.add_argument("--repo", default=DEFAULT_REPO)
    find_parser.add_argument("--limit", type=int, default=10)
    find_parser.add_argument(
        "--mode", choices=["keyword", "semantic", "hybrid"], default="keyword", help="lesson 6.6"
    )

    stats_db_parser = commands.add_parser("issue-stats", help="summarise stored issues")
    stats_db_parser.add_argument("--repo", default=DEFAULT_REPO)

    commands.add_parser("worker", help="run background jobs from the queue")
    commands.add_parser("scheduler", help="queue a sync of every repository, periodically")
    commands.add_parser("notifier", help="turn issue events into users' notifications")

    llm_parser = commands.add_parser("llm", help="ask the configured language model")
    llm_parser.add_argument("prompt")
    llm_parser.add_argument("--system", default="", help="instructions for the model")
    llm_parser.add_argument("--max-tokens", type=int, default=1024)
    llm_parser.add_argument("--stream", action="store_true", help="print the answer as it comes")

    fit_parser = commands.add_parser("fit", help="score open issues against a user's skills")
    fit_parser.add_argument("--email", required=True)
    fit_parser.add_argument("--repo", default=DEFAULT_REPO)
    fit_parser.add_argument("--limit", type=int, default=None, help="score at most N issues")

    commands.add_parser("llm-usage", help="calls, tokens and cost per purpose, from the log")

    digest_parser = commands.add_parser("digest", help="build every user's daily digest")
    digest_parser.add_argument(
        "--day",
        type=date.fromisoformat,
        default=None,
        help="the digest's date, YYYY-MM-DD (default: today, UTC); it covers the day before",
    )

    sync_parser = commands.add_parser("sync", help="queue a sync of one repository now")
    sync_parser.add_argument("repo", help="owner/name of a tracked repository")

    jobs_parser = commands.add_parser("jobs", help="list recent background jobs")
    jobs_parser.add_argument("--limit", type=int, default=20)

    return parser


def database_url() -> str | None:
    # Required for the database commands; read with the rest of the settings (lesson 4.2).
    return Settings().database_url


def open_database() -> Session | None:
    """A session on the configured database, or None after explaining what's missing."""
    url = database_url()
    if url is None:
        print(
            "atlas: set DATABASE_URL, for example "
            "postgresql+psycopg://atlas:atlas-dev-password@localhost:5432/atlas",
            file=sys.stderr,
        )
        return None
    return Session(create_engine(url))


def folder_error(path: Path) -> int | None:
    """Print an error and return exit code 1 if path is not a folder."""
    if path.is_dir():
        return None
    print(f"atlas: not a folder: {path}", file=sys.stderr)
    return 1


def github_token() -> str | None:
    token = Settings().github_token
    return token.get_secret_value() if token is not None else None


def run_search(args: argparse.Namespace) -> int:
    error_code = folder_error(args.path)
    if error_code is not None:
        return error_code

    try:
        matches = search(
            args.path,
            args.query,
            ignore_case=args.ignore_case,
            use_regex=args.regex,
            extensions=tuple(args.ext),
            limit=args.limit,
        )
    except re.error as error:
        print(f"atlas: invalid regular expression: {error}", file=sys.stderr)
        return 1

    if args.json:
        records = [
            {"path": str(match.path), "line_number": match.line_number, "line": match.line}
            for match in matches
        ]
        print(json.dumps(records, indent=2))
        return 0

    for match in matches:
        print(f"{match.path}:{match.line_number}: {match.line}")

    # The count goes to stderr so `atlas search ... > results.txt` saves only the results.
    noun = "match" if len(matches) == 1 else "matches"
    print(f"{len(matches)} {noun}", file=sys.stderr)
    return 0


def run_stats(args: argparse.Namespace) -> int:
    error_code = folder_error(args.path)
    if error_code is not None:
        return error_code

    stats = collect_stats(args.path, tuple(args.ext), top=args.top)

    print(f"{'type':<6} {'files':>6} {'lines':>8}")
    for extension, totals in sorted(stats.totals_by_extension.items()):
        print(f"{extension:<6} {totals.files:>6} {totals.lines:>8}")

    print(f"\nLargest {len(stats.largest_files)} files:")
    for path, line_count in stats.largest_files:
        print(f"{line_count:>8}  {path}")
    return 0


def run_issues(args: argparse.Namespace) -> int:
    labels = tuple(args.label or ["good first issue"])
    cache_path = args.cache or issue_cache.default_cache_path(args.repo)
    now = datetime.now(UTC)

    cached = None if args.refresh else issue_cache.load(cache_path)
    max_age = timedelta(minutes=args.max_age)
    use_cache = cached is not None and cached.is_usable(args.repo, labels, now, max_age)

    if cached is not None and use_cache:
        issues = cached.issues
        minutes_old = int((now - cached.fetched_at).total_seconds() // 60)
        source = f"cached copy from {minutes_old} min ago"
    else:
        try:
            issues = asyncio.run(fetch_issues(args.repo, list(labels), token=github_token()))
        except GitHubError as error:
            print(f"atlas: {error}", file=sys.stderr)
            return 1
        issue_cache.save(cache_path, CachedIssues(args.repo, labels, now, issues))
        source = "GitHub"

    if args.save:
        session = open_database()
        if session is None:
            return 1
        with session, session.begin():
            stored = storage.save_issues(session, args.repo, issues)
        print(f"stored {stored} issues in the database", file=sys.stderr)

    if args.json:
        records = [
            {
                "number": issue.number,
                "title": issue.title,
                "url": issue.url,
                "labels": list(issue.labels),
                "created_at": issue.created_at.isoformat(),
            }
            for issue in issues
        ]
        print(json.dumps(records, indent=2))
    else:
        for issue in issues:
            age_days = (now - issue.created_at).days
            print(f"#{issue.number:<6} {age_days:>4}d  {issue.title}  [{', '.join(issue.labels)}]")

    noun = "issue" if len(issues) == 1 else "issues"
    print(f"{len(issues)} open {noun} from {source}", file=sys.stderr)
    return 0


def run_commits(args: argparse.Namespace) -> int:
    limit_is_valid = 1 <= args.limit <= 100
    if not limit_is_valid:
        print("atlas: --limit must be between 1 and 100", file=sys.stderr)
        return 1

    try:
        commits = asyncio.run(fetch_recent_commits(args.repo, args.limit, token=github_token()))
    except GitHubError as error:
        print(f"atlas: {error}", file=sys.stderr)
        return 1

    for commit in commits:
        day = commit.committed_at.strftime("%Y-%m-%d")
        print(f"{commit.sha}  {day}  {commit.author:<20}  {commit.summary}")
    return 0


def run_init_db() -> int:
    """Bring the database schema up to date by running every pending migration (lesson 4.13)."""
    url = database_url()
    if url is None:
        return 1 if open_database() is None else 0

    config = AlembicConfig()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    alembic_command.upgrade(config, "head")
    print("database schema is up to date", file=sys.stderr)
    return 0


def run_make_admin(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    with session, session.begin():
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"atlas: no user with email {args.email}", file=sys.stderr)
            return 1
        user.role = "admin"
    print(f"{args.email} is now an administrator", file=sys.stderr)
    return 0


def run_index(args: argparse.Namespace) -> int:
    error_code = folder_error(args.path)
    if error_code is not None:
        return error_code
    session = open_database()
    if session is None:
        return 1

    # One transaction: searches see either the old index or the complete new one.
    with session, session.begin():
        summary = storage.index_folder(session, args.repo, args.path, extensions=tuple(args.ext))
    print(
        f"indexed {summary.documents} files into {summary.chunks} chunks "
        f"({summary.skipped} skipped) as {args.repo}",
        file=sys.stderr,
    )
    return 0


def run_find(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    settings = Settings()
    query_vector = None
    if args.mode != "keyword":
        embedder = make_embeddings(settings)
        try:
            query_vector = embedder.embed([args.query], "query")[0]
        except LLMError as error:
            print(f"atlas: {error}", file=sys.stderr)
            return 1
    with session:
        hits = storage.search_chunks(
            session,
            args.repo,
            args.query,
            args.mode,
            query_vector,
            model=settings.embedding_model,
            limit=args.limit,
        )

    for hit in hits:
        where = f"{hit.path}:{hit.start_line}-{hit.end_line}"
        print(f"{where}  {hit.symbol or ''}  {hit.rank:.3f}  {hit.snippet[:80]}")
    noun = "chunk" if len(hits) == 1 else "chunks"
    print(f"{len(hits)} {noun}", file=sys.stderr)
    return 0


def worker_context() -> WorkerContext | None:
    """Everything the background commands need, or None after explaining what's missing."""
    settings = Settings()
    if settings.database_url is None:
        open_database()  # prints the explanation
        return None
    factory = sessionmaker(create_engine(settings.database_url))
    redis_client = redis.Redis.from_url(settings.redis_url)
    snapshots = SnapshotStore(make_s3_client(settings), settings.snapshot_bucket)
    events = EventStream(redis_client)
    embedder = make_embeddings(settings)
    return WorkerContext(factory, RedisQueue(redis_client), settings, snapshots, events, embedder)


def run_worker() -> int:
    ctx = worker_context()
    if ctx is None:
        return 1
    configure_logging()
    logging.getLogger("atlas").info("worker started")
    worker.work(ctx, worker.stop_on_signals())
    return 0


def run_scheduler() -> int:
    ctx = worker_context()
    if ctx is None:
        return 1
    configure_logging()
    worker.run_scheduler(ctx, worker.stop_on_signals())
    return 0


def run_notifier() -> int:
    ctx = worker_context()
    if ctx is None:
        return 1
    configure_logging()
    run_notifier_loop(ctx.session_factory, ctx.events, worker.stop_on_signals())
    return 0


def run_llm(args: argparse.Namespace) -> int:
    settings = Settings()
    chat = make_chat(settings)
    request = ChatRequest(
        messages=[user(args.prompt)],
        system=args.system,
        max_tokens=args.max_tokens,
        purpose="cli",
    )
    try:
        result = stream_to_stdout(chat, request) if args.stream else chat.complete(request)
    except LLMError as error:
        print(f"atlas: {error}", file=sys.stderr)
        return 1
    if not args.stream:
        print(result.text)

    cost = cost_usd(result)
    cost_text = "unknown cost" if cost is None else f"${cost}"
    print(
        f"{result.model} via {result.provider}: {result.input_tokens} tokens in, "
        f"{result.output_tokens} out, {result.latency_ms / 1000:.1f} s, {cost_text}, "
        f"stopped: {result.stop_reason}",
        file=sys.stderr,
    )
    if settings.database_url is not None:
        with Session(create_engine(settings.database_url)) as session, session.begin():
            record(session, result)
    return 0


def stream_to_stdout(chat: ChatProvider, request: ChatRequest) -> ChatResult:
    for event in chat.stream(request):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
            continue
        print()
        return event
    raise LLMError("the stream ended without a result")


def run_fit(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1
    chat = make_chat(Settings())
    with session, session.begin():
        learner = session.scalar(select(User).where(User.email == args.email.lower()))
        if learner is None:
            print(f"atlas: no user with email {args.email}", file=sys.stderr)
            return 1
        summary = fit.score_user_issues(session, chat, learner, args.repo, limit=args.limit)
        ranked = session.execute(
            select(IssueFit.score, Issue.number, Issue.title, IssueFit.reasons)
            .join(Issue, IssueFit.issue_id == Issue.id)
            .where(IssueFit.user_id == learner.id)
            .order_by(IssueFit.score.desc(), Issue.number.desc())
        ).all()

    for score, number, title, reasons in ranked:
        print(f"{score}/5  #{number:<5} {title[:60]}")
        print(f"        {'; '.join(reasons)}")
    print(
        f"{summary.scored} scored, {summary.failed} failed, {summary.unchanged} unchanged",
        file=sys.stderr,
    )
    return 0


def run_llm_usage() -> int:
    session = open_database()
    if session is None:
        return 1
    with session:
        rows = session.execute(
            select(
                LlmCall.purpose,
                LlmCall.model,
                func.count(),
                func.sum(LlmCall.input_tokens),
                func.sum(LlmCall.output_tokens),
                func.avg(LlmCall.latency_ms),
                func.sum(LlmCall.cost_usd),
            )
            .group_by(LlmCall.purpose, LlmCall.model)
            .order_by(LlmCall.purpose, LlmCall.model)
        ).all()
    header = ("purpose", "model", "calls", "in", "out", "avg s", "cost")
    print("{:<12} {:<18} {:>5} {:>8} {:>7} {:>6} {:>10}".format(*header))
    for purpose, model, calls, tokens_in, tokens_out, avg_ms, cost in rows:
        cost_text = "?" if cost is None else f"${cost:.4f}"
        print(
            f"{purpose:<12} {model:<18} {calls:>5} {tokens_in:>8} {tokens_out:>7} "
            f"{avg_ms / 1000:>6.1f} {cost_text:>10}"
        )
    return 0


def run_digest(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    day = args.day or digest.today()
    with session, session.begin():
        created = digest.build_digests(session, day)
    print(f"{created} digests created for {day}", file=sys.stderr)
    return 0


def run_sync(args: argparse.Namespace) -> int:
    ctx = worker_context()
    if ctx is None:
        return 1

    job_ids = []
    with ctx.session_factory() as session, session.begin():
        repo_id = session.scalar(select(Repo.id).where(Repo.full_name == args.repo))
        if repo_id is None:
            print(f"atlas: {args.repo} is not tracked; add it first", file=sys.stderr)
            return 1
        for kind in ("sync_repo", "sync_issues"):
            job_id, created = create_job(session, kind, repo_id)
            if created:
                job_ids.append(job_id)
            print(f"{kind}: job {job_id}{'' if created else ' (already pending)'}")
    for job_id in job_ids:
        ctx.queue.send(job_id)
    return 0


def run_jobs(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    with session:
        rows = session.execute(
            select(Job, Repo.full_name)
            .join(Repo, Job.repo_id == Repo.id)
            .order_by(Job.id.desc())
            .limit(args.limit)
        ).all()

    for job, repo_name in rows:
        error = f"  {job.last_error}" if job.last_error else ""
        print(f"{job.id:>5}  {job.kind:<11}  {job.status:<9}  {job.attempts}  {repo_name}{error}")
    return 0


def run_issue_stats(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    with session:
        by_label = storage.label_counts(session, args.repo)
        by_age = storage.age_buckets(session, args.repo, datetime.now(UTC))

    print("By label:")
    for name, count in by_label:
        print(f"{count:>5}  {name}")
    print("\nBy age:")
    for bucket, count in by_age:
        print(f"{count:>5}  {bucket}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        return dispatch(args)
    except OperationalError as error:
        # The usual cause: the database isn't running or the URL is wrong.
        print(f"atlas: cannot reach the database: {error.orig}", file=sys.stderr)
        return 1


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "search":
        return run_search(args)
    if args.command == "stats":
        return run_stats(args)
    if args.command == "issues":
        return run_issues(args)
    if args.command == "commits":
        return run_commits(args)
    if args.command == "init-db":
        return run_init_db()
    if args.command == "make-admin":
        return run_make_admin(args)
    if args.command == "index":
        return run_index(args)
    if args.command == "find":
        return run_find(args)
    if args.command == "worker":
        return run_worker()
    if args.command == "scheduler":
        return run_scheduler()
    if args.command == "llm":
        return run_llm(args)
    if args.command == "fit":
        return run_fit(args)
    if args.command == "llm-usage":
        return run_llm_usage()
    if args.command == "digest":
        return run_digest(args)
    if args.command == "notifier":
        return run_notifier()
    if args.command == "sync":
        return run_sync(args)
    if args.command == "jobs":
        return run_jobs(args)
    return run_issue_stats(args)


if __name__ == "__main__":
    sys.exit(main())
