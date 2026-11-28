#!/usr/bin/env python3
"""Fail-closed PR changed-path gate ("diff lock main") for preflight jobs.

Prevents non-workflow bypasses (#4748 style edits to .github/scripts/,
.github/actions/, build manifests, etc.) unless an explicit commit-SHA-bound
maintainer approval comment `/approve-ci-sha <head_sha>` exists on the PR.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

SENSITIVE_EXACT = {
    "pyproject.toml",
    "install.sh",
    ".pre-commit-config.yaml",
    "CODEOWNERS",
}

SENSITIVE_PREFIXES = (
    ".github/",
    "docker/",
    "scripts/ci/",
)

SENSITIVE_PATTERNS = (
    re.compile(r"^pyproject_[a-z0-9_]+\.toml$"),
    re.compile(r"^sglang_omni_mlx/native/(?:CMakeLists\.txt|scripts/.*)$"),
    re.compile(r"^tests/(?:.*/)?conftest\.py$"),
)

APPROVAL_RE = re.compile(r"^\s*/approve-ci-sha\s+([0-9a-f]{40})\s*$", re.MULTILINE)
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _github_get_paginated(token: str, repo: str, path: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        url = f"https://api.github.com/repos/{repo}{path}{sep}per_page=100&page={page}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "omni-diff-lock-main/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                chunk = json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {exc.code} on {path}: {body}") from exc
        if not chunk:
            break
        items.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return items


def is_sensitive_path(path: str) -> bool:
    norm = path
    while norm.startswith("./"):
        norm = norm[2:]
    if norm in SENSITIVE_EXACT:
        return True
    if any(norm.startswith(prefix) for prefix in SENSITIVE_PREFIXES):
        return True
    return any(pat.match(norm) for pat in SENSITIVE_PATTERNS)


def find_sha_bound_approval(comments: list[dict], head_sha: str, pr_author: str, *, allow_author_if_owner: bool) -> str | None:
    for c in comments:
        assoc = c.get("author_association")
        login = (c.get("user") or {}).get("login", "")
        if assoc not in TRUSTED_ASSOCIATIONS:
            continue
        if not allow_author_if_owner and login == pr_author:
            continue
        body = c.get("body") or ""
        for m in APPROVAL_RE.finditer(body):
            if m.group(1) == head_sha:
                return login
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-author-if-owner", action="store_true")
    args = parser.parse_args()

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name != "pull_request":
        print(f"Skipping PR path lock for event={event_name}")
        return 0

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not token or not repo or not event_path:
        raise RuntimeError("Missing GITHUB_TOKEN, GITHUB_REPOSITORY, or GITHUB_EVENT_PATH")

    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)
    pr = event["pull_request"]
    pr_number = int(pr["number"])
    head_sha = pr["head"]["sha"]
    pr_author = (pr.get("user") or {}).get("login", "")

    files = _github_get_paginated(token, repo, f"/pulls/{pr_number}/files")
    changed_paths: list[str] = []
    for f in files:
        if f.get("filename"):
            changed_paths.append(f["filename"])
        if f.get("previous_filename"):
            changed_paths.append(f["previous_filename"])

    sensitive_hits = sorted({p for p in changed_paths if is_sensitive_path(p)})
    print(f"PR #{pr_number} sha={head_sha} total_files={len(files)} sensitive_hits={len(sensitive_hits)}")
    if not sensitive_hits:
        print("Diff-lock-main PASSED: no sensitive CI/build files modified.")
        return 0

    print("Sensitive files touched:")
    for p in sensitive_hits:
        print(f"  - {p}")

    comments = _github_get_paginated(token, repo, f"/issues/{pr_number}/comments")
    approver = find_sha_bound_approval(
        comments,
        head_sha,
        pr_author,
        allow_author_if_owner=args.allow_author_if_owner,
    )
    if approver:
        print(f"Diff-lock-main PASSED via SHA-bound approval by @{approver} for {head_sha}.")
        return 0

    print(
        f"::error::Diff-lock-main BLOCKED PR #{pr_number} ({head_sha}): touches {len(sensitive_hits)} "
        "sensitive CI/build path(s) without maintainer `/approve-ci-sha <head_sha>` comment.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Diff-lock-main ERROR (fail-closed): {exc}", file=sys.stderr)
        raise SystemExit(1)
