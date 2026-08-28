from __future__ import annotations

import argparse
import os
from pathlib import Path
import posixpath
from urllib.parse import urlparse

import paramiko


def parse_host(raw: str, default_port: int) -> tuple[str, int]:
    """Accepts either a bare hostname or a sftp://user@host:port URL."""
    if "://" in raw:
        parsed = urlparse(raw)
        return parsed.hostname, parsed.port or default_port
    if ":" in raw:
        host, _, port = raw.partition(":")
        return host, int(port)
    return raw, default_port


INCLUDE_PATHS = [
    "ios_bot",
    "migrations",
    "main.py",
    "requirements.txt",
    "readme.txt",
]

SKIP_PARTS = {
    ".git",
    ".github",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    ".deploy_stage",
}

SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
}


def should_skip(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    return path.suffix.lower() in SKIP_SUFFIXES


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    current = ""
    for chunk in remote_dir.strip("/").split("/"):
        current = f"{current}/{chunk}" if current else f"/{chunk}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def upload_path(sftp: paramiko.SFTPClient, local_root: Path, remote_root: str, relative_path: str) -> int:
    uploaded = 0
    source = local_root / relative_path
    if not source.exists():
        return 0

    if source.is_file():
        remote_path = posixpath.join(remote_root, relative_path.replace("\\", "/"))
        ensure_remote_dir(sftp, posixpath.dirname(remote_path))
        sftp.put(str(source), remote_path)
        return 1

    for item in source.rglob("*"):
        if should_skip(item):
            continue
        if item.is_dir():
            ensure_remote_dir(sftp, posixpath.join(remote_root, item.relative_to(local_root).as_posix()))
            continue
        remote_path = posixpath.join(remote_root, item.relative_to(local_root).as_posix())
        ensure_remote_dir(sftp, posixpath.dirname(remote_path))
        sftp.put(str(item), remote_path)
        uploaded += 1
    return uploaded


def main() -> None:
    # Login credentials come from the environment, not CLI flags, so they
    # never show up in a process listing or shell history on the runner.
    parser = argparse.ArgumentParser(description="Deploy bot runtime files over SFTP.")
    parser.add_argument("--host", default=os.environ.get("SPARKED_SERVER_HOST", ""))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SPARKED_SERVER_PORT") or 2022))
    parser.add_argument("--remote-path", default=os.environ.get("SPARKED_SERVER_REMOTE_PATH", ""))
    parser.add_argument("--local-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()

    login_name = os.environ.get("SPARKED_SERVER_USERNAME")
    login_secret = os.environ.get("SPARKED_SERVER_PASSWORD")
    if not args.host or not login_name or not login_secret or not args.remote_path:
        raise SystemExit(
            "Missing connection details: set SPARKED_SERVER_HOST, "
            "SPARKED_SERVER_USERNAME, SPARKED_SERVER_PASSWORD, and "
            "SPARKED_SERVER_REMOTE_PATH (or pass --host/--remote-path)."
        )

    local_root = Path(args.local_root).resolve()
    remote_root = args.remote_path.rstrip("/") or "."

    host, port = parse_host(args.host, args.port)
    transport = paramiko.Transport((host, port))
    transport.connect(username=login_name, password=login_secret)
    sftp = paramiko.SFTPClient.from_transport(transport)

    try:
        ensure_remote_dir(sftp, remote_root)
        uploaded = 0
        for relative_path in INCLUDE_PATHS:
            uploaded += upload_path(sftp, local_root, remote_root, relative_path)
        print(f"Uploaded {uploaded} files to {remote_root}")
    finally:
        sftp.close()
        transport.close()


if __name__ == "__main__":
    main()
