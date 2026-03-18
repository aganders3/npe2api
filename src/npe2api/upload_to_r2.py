#!/usr/bin/env python3
"""
Upload generated JSON files to R2 bucket.

Reads all JSON files from public/ directory and uploads them to R2.

For remote R2, create a .env file with:
    CLOUDFLARE_ACCOUNT_ID=your_account_id
    R2_ACCESS_KEY_ID=your_access_key
    R2_SECRET_ACCESS_KEY=your_secret_key

Usage:
    uv run -m npe2api.upload_to_r2 npe2api           # Upload to remote R2 bucket "npe2api"
    uv run -m npe2api.upload_to_r2 npe2api --local   # Upload to local R2 for pywrangler dev --local
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3

# Load .env file if it exists (for local development)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # dotenv not installed, use environment variables only


def calculate_md5(file_path: Path) -> str:
    """Calculate MD5 hash of a file."""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


def get_content_type(key: str) -> str:
    """Determine content type based on file extension."""
    if key.endswith(".html"):
        return "text/html"
    elif key.endswith(".json"):
        return "application/json"
    return "application/octet-stream"


class R2Remote:
    """Remote R2 backend using boto3."""

    def __init__(self, bucket_name: str):
        self.bucket_name = bucket_name
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        access_key_id = os.environ["R2_ACCESS_KEY_ID"]
        secret_access_key = os.environ["R2_SECRET_ACCESS_KEY"]

        session = boto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

        self.s3 = session.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        )

    def create(self):
        """Create bucket (ignore if it already exists)."""
        try:
            self.s3.create_bucket(Bucket=self.bucket_name)
            print(f"✓ Created bucket: {self.bucket_name}")
        except self.s3.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                print(f"Using bucket: {self.bucket_name}")
            else:
                raise

    def put(self, file_path: Path, key: str) -> bool:
        """Upload file if it has changed (compares MD5 with ETag). Returns True if uploaded."""
        # Check if upload is needed
        try:
            response = self.s3.head_object(Bucket=self.bucket_name, Key=key)
            remote_etag = response["ETag"].strip('"')
            local_md5 = calculate_md5(file_path)
            if remote_etag == local_md5:
                return False  # File unchanged
        except self.s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "404":
                raise

        # Upload file
        self.s3.upload_file(
            str(file_path),
            self.bucket_name,
            key,
            ExtraArgs={
                "ContentType": get_content_type(key),
                "CacheControl": "public, max-age=3600",
            },
        )
        return True

    def list(self) -> set[str]:
        """List all JSON keys in the bucket."""
        keys = set()
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name):
            if "Contents" in page:
                for obj in page["Contents"]:
                    key = obj["Key"]
                    if key.endswith(".json"):
                        keys.add(key)
        return keys

    def delete(self, key: str):
        """Delete an object from the bucket."""
        self.s3.delete_object(Bucket=self.bucket_name, Key=key)


class R2Local:
    """Local R2 backend using pywrangler CLI."""

    def __init__(self, bucket_name: str):
        self.bucket_name = bucket_name

    def create(self):
        """Local buckets are created automatically by wrangler."""
        print(f"Using local bucket: {self.bucket_name}")

    def put(self, file_path: Path, key: str, retries: int = 3) -> bool:
        """Upload file using pywrangler with retry logic. Returns True if uploaded."""
        cmd = [
            "pywrangler", "r2", "object", "put",
            f"{self.bucket_name}/{key}",
            "--file", str(file_path),
            "--local",
            "--content-type", get_content_type(key),
            "--cache-control", "public, max-age=3600",
        ]

        for attempt in range(retries):
            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                return True
            except subprocess.CalledProcessError as e:
                if attempt == retries - 1:
                    # Final attempt failed
                    print(f"✗ Failed to upload {key} after {retries} attempts: {e.stderr}")
                    return False
                # Retry with exponential backoff
                time.sleep(0.1 * (2 ** attempt))

        return False

    def list(self) -> set[str]:
        """List all JSON keys in the local bucket."""
        cmd = ["pywrangler", "r2", "object", "list", self.bucket_name, "--local"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            return {obj["key"] for obj in data if obj["key"].endswith(".json")}
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            return set()

    def delete(self, key: str, retries: int = 3):
        """Delete an object from the local bucket with retry logic."""
        cmd = ["pywrangler", "r2", "object", "delete", f"{self.bucket_name}/{key}", "--local"]

        for attempt in range(retries):
            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                return
            except subprocess.CalledProcessError as e:
                if attempt == retries - 1:
                    print(f"✗ Failed to delete {key} after {retries} attempts: {e.stderr}")
                    return
                time.sleep(0.1 * (2 ** attempt))


def upload_to_r2(bucket_name: str, use_local: bool = False):
    """Sync all JSON files from public/ directory to R2, removing stale files."""
    # Create R2 client
    r2 = R2Local(bucket_name) if use_local else R2Remote(bucket_name)
    r2.create()

    # Upload all files from public/ directory
    public_dir = Path("public")
    if not public_dir.exists():
        print(f"Error: {public_dir} directory does not exist")
        return

    # Collect all JSON files to upload
    files_to_process = [f for f in public_dir.rglob("*.json") if f.is_file()]
    local_keys = {str(f.relative_to(public_dir)) for f in files_to_process}
    print(f"Processing {len(files_to_process)} JSON files...")

    uploaded_count = 0
    skipped_count = 0

    # Upload files in parallel
    def upload_one(file_path):
        key = str(file_path.relative_to(public_dir))
        was_uploaded = r2.put(file_path, key)
        return key, was_uploaded

    max_workers = 50 if use_local else 10
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(upload_one, f): f for f in files_to_process}

        for future in as_completed(futures):
            key, was_uploaded = future.result()
            if was_uploaded:
                print(f"✓ Uploaded: {key}")
                uploaded_count += 1
            else:
                print(f"⊝ Skipped (unchanged): {key}")
                skipped_count += 1

    # Delete files from R2 that no longer exist locally
    print("\nChecking for stale files in R2...")
    remote_keys = r2.list()
    stale_keys = remote_keys - local_keys
    deleted_count = 0

    if stale_keys:
        print(f"Found {len(stale_keys)} stale files to delete...")
        for key in stale_keys:
            r2.delete(key)
            print(f"✗ Deleted: {key}")
            deleted_count += 1
    else:
        print("No stale files found")

    print("")
    print(f"Uploaded {uploaded_count} files, skipped {skipped_count} (unchanged), deleted {deleted_count} (stale)")
    print(f"  Bucket: {bucket_name}")
    if use_local:
        print("")
        print("You can now run: pywrangler dev --local")


def main():
    parser = argparse.ArgumentParser(
        description="Sync JSON files from public/ directory to R2 bucket"
    )
    parser.add_argument(
        "bucket_name",
        help="Name of the R2 bucket (e.g., npe2api or npe2api-pr-123)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Upload to local R2 bucket (.wrangler/state) for pywrangler dev --local",
    )

    args = parser.parse_args()
    upload_to_r2(args.bucket_name, use_local=args.local)


if __name__ == "__main__":
    main()
