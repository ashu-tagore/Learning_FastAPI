"""Repository snapshots in object storage (lesson 5.4).

A snapshot is the repository's files at one commit, as a .tar.gz made by `git archive`:
the same thing as GitHub's "Download ZIP", for the exact commit Atlas indexed. Objects are
keyed by commit, so a snapshot is written once and never changes:

    snapshots/<owner>/<name>/<commit>.tar.gz

Locally the S3 API is served by SeaweedFS; on AWS by S3 itself. This module is the only
code that knows that object storage exists.
"""

import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from mypy_boto3_s3 import S3Client

from atlas.config import Settings

# Signature Version 4, always. Left to itself, boto3 1.44 signed download links with the
# older Version 2 (AWSAccessKeyId, Signature and Expires in the URL), which AWS has
# deprecated (lesson 5.4).
S3_CONFIG = Config(signature_version="s3v4")


def make_s3_client(settings: Settings) -> S3Client:
    # Credentials are not passed: boto3 finds them in the environment, or on AWS from the
    # task's IAM role. Code that never handles a key can't leak one.
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        config=S3_CONFIG,
    )


class SnapshotStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @staticmethod
    def key(repo: str, commit: str) -> str:
        return f"snapshots/{repo}/{commit}.tar.gz"

    def exists(self, repo: str, commit: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(repo, commit))
        except ClientError as error:
            is_missing = error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey")
            if is_missing:
                return False
            raise
        return True

    def upload(self, repo: str, commit: str, clone: Path) -> bool:
        """Archive the clone at `commit` and store it; False if that snapshot already exists.

        The archive goes through a temporary file, never into memory: a large repository's
        archive is gigabytes, and upload_file sends a file in parts (multipart upload).
        """
        if self.exists(repo, commit):
            return False
        with tempfile.NamedTemporaryFile(suffix=".tar.gz") as archive:
            subprocess.run(
                ["git", "archive", "--format=tar.gz", commit],
                cwd=clone,
                stdout=archive,
                check=True,
            )
            archive.flush()
            committed_at = subprocess.run(
                ["git", "show", "-s", "--format=%cI", commit],
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.client.upload_file(
                archive.name,
                self.bucket,
                self.key(repo, commit),
                # Stored with the object as an x-amz-meta-committed-at header; prune() sorts
                # by it, because LastModified is only accurate to the second.
                ExtraArgs={"Metadata": {"committed-at": committed_at}},
            )
        return True

    def download_url(self, repo: str, commit: str, expires_in: int = 300) -> tuple[str, datetime]:
        """A link anyone can use to download the snapshot, until it expires.

        Signing happens here, with no request to the storage server: the URL carries a
        signature made with Atlas's credentials over the bucket, key and expiry time.
        """
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self.key(repo, commit)},
            ExpiresIn=expires_in,
        )
        return url, datetime.now(UTC) + timedelta(seconds=expires_in)

    def prune(self, repo: str, keep: int) -> list[str]:
        """Delete all but the snapshots of the `keep` newest commits; return deleted keys.

        Called after every upload, so only a few snapshots exist per repository and one
        listing page (up to 1,000 keys) holds them all.
        """
        listing = self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"snapshots/{repo}/")
        keys = [item["Key"] for item in listing.get("Contents", [])]
        if len(keys) <= keep:
            return []
        committed_at = {key: self._committed_at(key) for key in keys}
        newest_first = sorted(keys, key=lambda key: committed_at[key], reverse=True)
        old_keys = newest_first[keep:]
        for key in old_keys:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        return old_keys

    def _committed_at(self, key: str) -> str:
        metadata = self.client.head_object(Bucket=self.bucket, Key=key)["Metadata"]
        # ISO 8601 with an offset, as Git prints it; normalised to UTC so text order is time order.
        return datetime.fromisoformat(metadata["committed-at"]).astimezone(UTC).isoformat()
