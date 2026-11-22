# SPDX-License-Identifier: Apache-2.0
"""Keep one pull request comment naming the CI stages that would not test a changed model."""

from __future__ import annotations

import json
import os
import subprocess

from resolve_ci_labels import (
    MODEL_DIRECTORY_ROOT,
    StageSuggestion,
    build_labels_by_target,
    suggest_stage_targets,
)

COMMENT_MARKER = "<!-- ci-stage-suggestion -->"


def call_github(arguments: list[str]) -> str:
    return subprocess.run(
        ["gh", "api", *arguments], check=True, capture_output=True, text=True
    ).stdout


def build_comment_body(suggestions: list[StageSuggestion]) -> str:
    if not suggestions:
        return f"{COMMENT_MARKER}\nEvery CI stage that tests a changed model directory is selected."
    else:
        pass
    labels_by_target = build_labels_by_target()
    bullets_by_is_in_command: dict[bool, list[str]] = {True: [], False: []}
    for suggestion in suggestions:
        added_labels = sorted(
            label for target in suggestion.targets for label in labels_by_target[target]
        )
        bullets_by_is_in_command[suggestion.is_in_command].append(
            f"- `{MODEL_DIRECTORY_ROOT}{suggestion.model_directory}/`: the {suggestion.stage_name} stage, with `{' '.join(suggestion.targets)}` (adds {', '.join(f'`{label}`' for label in added_labels)})"
        )
    lines = [COMMENT_MARKER]
    if bullets_by_is_in_command[True]:
        command_targets = dict.fromkeys(
            target
            for suggestion in suggestions
            if suggestion.is_in_command
            for target in suggestion.targets
        )
        lines += [
            "These CI stages would not test a model directory this pull request changes:",
            "",
            *bullets_by_is_in_command[True],
            "",
            "A maintainer can start them with:",
            "",
            f"`/tag-and-rerun-ci {' '.join(command_targets)}`",
            "",
        ]
    else:
        pass
    if bullets_by_is_in_command[False]:
        lines += [
            "A stage tests one model per run. These changed models are left out of that run, with the target that tests each one instead:",
            "",
            *bullets_by_is_in_command[False],
            "",
        ]
    else:
        pass
    lines.append(
        "This comment adds no `run-*` label; a maintainer decides when GPU CI starts."
    )
    return "\n".join(lines)


def update_stage_suggestion(repository: str, pull_request_number: int) -> None:
    """Create the comment when a stage is missing, and rewrite it only when its text changes."""
    pull_request = json.loads(
        call_github([f"repos/{repository}/pulls/{pull_request_number}"])
    )
    if pull_request["draft"]:
        return
    else:
        pass
    changed_paths = call_github(
        [
            "--paginate",
            f"repos/{repository}/pulls/{pull_request_number}/files",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
        ]
    ).splitlines()
    comments = [
        json.loads(line)
        for line in call_github(
            [
                "--paginate",
                f"repos/{repository}/issues/{pull_request_number}/comments",
                "--jq",
                ".[] | {id, body, user_type: .user.type} | @json",
            ]
        ).splitlines()
    ]
    suggestions = suggest_stage_targets(
        changed_paths, frozenset(label["name"] for label in pull_request["labels"])
    )
    body = build_comment_body(suggestions)
    # note (0xtoward): a person can paste the marker, so only a comment the bot wrote is ever rewritten.
    own_comments = [
        comment
        for comment in comments
        if comment["user_type"] == "Bot" and COMMENT_MARKER in comment["body"]
    ]
    if own_comments and own_comments[0]["body"] != body:
        call_github(
            [
                "--method",
                "PATCH",
                f"repos/{repository}/issues/comments/{own_comments[0]['id']}",
                "-f",
                f"body={body}",
            ]
        )
    elif not own_comments and suggestions:
        call_github(
            [
                "--method",
                "POST",
                f"repos/{repository}/issues/{pull_request_number}/comments",
                "-f",
                f"body={body}",
            ]
        )
    else:
        pass


if __name__ == "__main__":
    update_stage_suggestion(
        os.environ["GITHUB_REPOSITORY"], int(os.environ["PR_NUMBER"])
    )
