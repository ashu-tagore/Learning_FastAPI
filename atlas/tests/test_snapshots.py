"""Repository snapshots in object storage (lesson 5.4)."""

import io
import subprocess
import tarfile
import time
from pathlib import Path

import httpx

from atlas.jobs import WorkerContext, run_job
from atlas.snapshots import SnapshotStore
from tests.conftest import git_commit
from tests.test_jobs import add_repo, queue_job


def head_commit(path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def names_in(archive: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        return sorted(member.name for member in tar.getmembers() if member.isfile())


def test_upload_stores_the_files_at_that_commit_once(
    snapshot_store: SnapshotStore, upstream: Path
) -> None:
    commit = head_commit(upstream)

    first = snapshot_store.upload("pytorch/ignite", commit, upstream)
    second = snapshot_store.upload("pytorch/ignite", commit, upstream)

    assert (first, second) == (True, False)
    url, _ = snapshot_store.download_url("pytorch/ignite", commit)
    assert names_in(httpx.get(url).content) == ["README.md", "engine.py"]


def test_download_link_works_only_as_signed_and_only_until_it_expires(
    snapshot_store: SnapshotStore, upstream: Path
) -> None:
    commit = head_commit(upstream)
    snapshot_store.upload("pytorch/ignite", commit, upstream)
    url, _ = snapshot_store.download_url("pytorch/ignite", commit, expires_in=1)
    other_key = url.replace(commit, "0" * 40)

    assert httpx.get(url).status_code == 200
    assert httpx.get(url.split("?")[0]).status_code == 403  # no signature
    assert httpx.get(other_key).status_code == 403  # signature was for a different key
    time.sleep(2)  # real time: the server checks expiry against its own clock
    assert httpx.get(url).status_code == 403


def test_prune_keeps_the_newest_commits(snapshot_store: SnapshotStore, upstream: Path) -> None:
    commits = [head_commit(upstream)]
    for number in range(3):
        (upstream / "engine.py").write_text(f"version = {number}\n")
        # Commit times one second apart, the finest Git records.
        time.sleep(1)
        commits.append(git_commit(upstream, f"change {number}"))
    for commit in commits:
        snapshot_store.upload("pytorch/ignite", commit, upstream)

    deleted = snapshot_store.prune("pytorch/ignite", keep=2)

    assert deleted == [SnapshotStore.key("pytorch/ignite", commit) for commit in commits[1::-1]]
    assert [snapshot_store.exists("pytorch/ignite", commit) for commit in commits] == [
        False,
        False,
        True,
        True,
    ]


def test_snapshot_job_stores_the_indexed_commit(worker: WorkerContext, upstream: Path) -> None:
    repo_id = add_repo(worker)
    early = run_job(worker, queue_job(worker, "snapshot_repo", repo_id))
    run_job(worker, queue_job(worker, "sync_repo", repo_id))

    later = run_job(worker, queue_job(worker, "snapshot_repo", repo_id))

    assert (early, later) == ("succeeded", "succeeded")  # early: nothing indexed, nothing to do
    assert worker.snapshots.exists("pytorch/ignite", head_commit(upstream))


# ---------- The API ----------

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from atlas.api.deps import get_snapshots  # noqa: E402
from atlas.models import Repo  # noqa: E402
from tests.conftest import sign_up  # noqa: E402
from tests.test_api_jobs import add_ignite  # noqa: E402


@pytest.mark.anyio
async def test_api_gives_a_download_link_once_the_snapshot_exists(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    snapshot_store: SnapshotStore,
    upstream: Path,
) -> None:
    api_app.dependency_overrides[get_snapshots] = lambda: snapshot_store
    repo_id = await add_ignite(client, async_session)
    learner = await sign_up(client, "learner@example.com")
    commit = head_commit(upstream)
    await async_session.execute(
        update(Repo).where(Repo.id == repo_id).values(indexed_commit=commit)
    )
    await async_session.commit()

    before = await client.get(f"/repos/{repo_id}/snapshot", headers=learner)
    snapshot_store.upload("pytorch/ignite", commit, upstream)
    after = await client.get(f"/repos/{repo_id}/snapshot", headers=learner)

    assert before.json()["error"]["code"] == "snapshot_not_found"
    assert after.json()["commit"] == commit
    assert names_in(httpx.get(after.json()["url"]).content) == ["README.md", "engine.py"]


def test_download_links_use_signature_version_4(
    snapshot_store: SnapshotStore, upstream: Path
) -> None:
    # Regression (lesson 5.4): without an explicit config, boto3 signed with Version 2.
    url, _ = snapshot_store.download_url("pytorch/ignite", head_commit(upstream))

    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Expires=300" in url


def test_editing_a_signed_link_breaks_it(snapshot_store: SnapshotStore, upstream: Path) -> None:
    commit = head_commit(upstream)
    snapshot_store.upload("pytorch/ignite", commit, upstream)
    url, _ = snapshot_store.download_url("pytorch/ignite", commit)

    longer = url.replace("X-Amz-Expires=300", "X-Amz-Expires=604800")

    assert httpx.get(url).status_code == 200
    assert httpx.get(longer).status_code == 403
