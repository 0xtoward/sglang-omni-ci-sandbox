# SPDX-License-Identifier: Apache-2.0
"""The stage suggestion names /tag-and-rerun-ci targets that make every stage test the model a pull request changes."""

import importlib
import itertools
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MODELS = "sglang_omni/models"
MINICPM_PATH = f"{MODELS}/minicpm_o/config.py"
REPOSITORY = "example/example"
PULL_REQUEST_NUMBER = 42


@pytest.fixture(scope="module")
def resolver() -> Iterator[ModuleType]:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.syspath_prepend(str(REPO_ROOT / ".github/scripts"))
        monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts/ci/utils"))

        yield importlib.import_module("resolve_ci_labels")

        sys.modules.pop("resolve_ci_labels")
        sys.modules.pop("suggest_ci_stages", None)


@pytest.fixture(scope="module")
def suggester(resolver: ModuleType) -> ModuleType:
    return importlib.import_module("suggest_ci_stages")


class RecordingGithub:
    """Answers the three reads from fixed data and records every write."""

    def __init__(
        self,
        *,
        labels: list[str],
        comments: list[dict[str, str | int]],
        is_draft: bool = False,
        changed_paths: tuple[str, ...] = (MINICPM_PATH,),
    ) -> None:
        self.changed_paths = changed_paths
        self.labels = labels
        self.comments = comments
        self.is_draft = is_draft
        self.writes: list[tuple[str, str]] = []

    def __call__(self, arguments: list[str]) -> str:
        endpoint = next(
            argument for argument in arguments if argument.startswith("repos/")
        )
        if "--method" in arguments:
            self.writes.append((arguments[1], arguments[-1].removeprefix("body=")))
            return ""
        elif endpoint.endswith("/files"):
            return "".join(f"{path}\n" for path in self.changed_paths)
        elif endpoint.endswith("/comments"):
            return "".join(f"{json.dumps(comment)}\n" for comment in self.comments)
        else:
            return json.dumps(
                {
                    "draft": self.is_draft,
                    "labels": [{"name": label} for label in self.labels],
                }
            )


def make_comment(body: str, user_type: str = "Bot") -> dict[str, str | int]:
    return {"id": 7, "body": body, "user_type": user_type}


@pytest.mark.parametrize(
    ("changed_paths", "labels", "expected_command", "expected_alternatives"),
    [
        ([MINICPM_PATH], [], ["minicpmo", "full-duplex"], []),
        ([MINICPM_PATH], ["run-minicpm-o-4.5"], ["full-duplex"], []),
        ([MINICPM_PATH], ["run-omni-ci"], ["minicpmo", "full-duplex"], []),
        ([MINICPM_PATH], ["run-minicpm-o-4.5", "run-native-duplex"], [], []),
        (
            [f"{MODELS}/higgs_tts/config.py"],
            ["run-mps-ci", "run-higgs-tts-3-4b"],
            ["tts"],
            [],
        ),
        ([f"{MODELS}/higgs_tts/config.py"], ["run-mps-ci"], ["higgs", "tts"], []),
        (
            [f"{MODELS}/fun_asr/config.py", f"{MODELS}/qwen3_asr/config.py"],
            [],
            ["fun-asr"],
            ["qwen3-asr"],
        ),
        (
            [
                f"{MODELS}/fun_cosyvoice3/config.py",
                f"{MODELS}/moss_tts_local/config.py",
            ],
            ["run-tts-ci", "run-moss-tts-local-transformer-v1.5"],
            [],
            ["cosyvoice3"],
        ),
        (
            [f"{MODELS}/qwen3_asr/config.py"],
            ["run-asr-ci", "run-whisper-large-v3"],
            ["qwen3-asr"],
            [],
        ),
        (
            [f"{MODELS}/qwen3_asr/config.py"],
            ["run-higgs-tts-3-4b", "run-moss-tts-local-transformer-v1.5"],
            [],
            [],
        ),
        (
            [f"{MODELS}/qwen3_tts/config.py"],
            ["run-qwen3-tts-12hz-1.7b-customvoice"],
            [],
            [],
        ),
        ([f"{MODELS}/moss_tts/audio_tokenizer.py"], [], ["moss"], []),
        ([f"{MODELS}/streaming_cascade/base.py"], [], ["cascaded"], []),
        (
            [f"{MODELS}/ming_tts/stages.py", "sglang_omni/serve/launcher.py"],
            [],
            [],
            [],
        ),
    ],
)
def test_command_names_the_targets_that_make_each_stage_test_the_changed_model(
    resolver: ModuleType,
    changed_paths: list[str],
    labels: list[str],
    expected_command: list[str],
    expected_alternatives: list[str],
) -> None:
    suggestions = resolver.suggest_stage_targets(changed_paths, frozenset(labels))

    command = [
        target
        for suggestion in suggestions
        if suggestion.is_in_command
        for target in suggestion.targets
    ]
    alternatives = [
        target
        for suggestion in suggestions
        if not suggestion.is_in_command
        for target in suggestion.targets
    ]
    assert command == expected_command
    assert alternatives == expected_alternatives


@pytest.mark.parametrize(
    "labels",
    [
        [],
        ["run-unit-test-ci-only"],
        ["run-mps-ci"],
        ["run-asr-ci", "run-tts-ci", "run-omni-ci"],
        ["run-whisper-large-v3", "run-moss-tts-local-transformer-v1.5"],
    ],
)
def test_the_handler_accepts_every_command_and_following_it_leaves_no_command(
    resolver: ModuleType, labels: list[str]
) -> None:
    labels_by_target = resolver.build_labels_by_target()
    model_directories = sorted(
        {
            model_directory
            for preset_directories in resolver.MODEL_DIRECTORIES_BY_PRESET.values()
            for model_directory in preset_directories
        }
        | {
            model_directory
            for stage in resolver.CI_STAGES
            for model_directory in stage.model_directories
        }
    )
    changed_directory_sets = [
        *((model_directory,) for model_directory in model_directories),
        *itertools.combinations(model_directories, 2),
    ]

    for changed_directories in changed_directory_sets:
        changed_paths = [
            f"{MODELS}/{model_directory}/config.py"
            for model_directory in changed_directories
        ]
        suggestions = resolver.suggest_stage_targets(changed_paths, frozenset(labels))
        requested_labels = frozenset(
            label
            for suggestion in suggestions
            if suggestion.is_in_command
            for target in suggestion.targets
            for label in labels_by_target[target]
        )
        tagged_labels = resolver.labels_after_tagging(
            frozenset(labels), requested_labels
        )

        if requested_labels:
            resolver.resolve_ci_plan(tagged_labels, seed="1")
        else:
            pass
        assert [
            suggestion
            for suggestion in resolver.suggest_stage_targets(
                changed_paths, tagged_labels
            )
            if suggestion.is_in_command
        ] == [], changed_directories


def test_every_preset_and_fixed_model_stage_names_an_existing_model_directory(
    resolver: ModuleType,
) -> None:
    presets = {
        preset
        for stage in resolver.CI_STAGES
        for preset in (*stage.preset_by_model_label.values(), *stage.random_presets)
    }
    model_directories = {
        model_directory
        for preset_directories in resolver.MODEL_DIRECTORIES_BY_PRESET.values()
        for model_directory in preset_directories
    } | {
        model_directory
        for stage in resolver.CI_STAGES
        for model_directory in stage.model_directories
    }

    assert presets == set(resolver.MODEL_DIRECTORIES_BY_PRESET)
    assert [
        model_directory
        for model_directory in sorted(model_directories)
        if not (REPO_ROOT / MODELS / model_directory).is_dir()
    ] == []


@pytest.mark.parametrize(
    (
        "changed_paths",
        "comments",
        "labels",
        "is_draft",
        "expected_method",
        "expected_text",
    ),
    [
        (
            (MINICPM_PATH,),
            [],
            [],
            False,
            "POST",
            "/tag-and-rerun-ci minicpmo full-duplex",
        ),
        (
            (MINICPM_PATH,),
            [make_comment("<!-- ci-stage-suggestion -->\nstale")],
            [],
            False,
            "PATCH",
            "/tag-and-rerun-ci minicpmo full-duplex",
        ),
        (
            (MINICPM_PATH,),
            [make_comment("<!-- ci-stage-suggestion -->\nstale")],
            ["run-minicpm-o-4.5", "run-native-duplex"],
            False,
            "PATCH",
            "Every CI stage that tests a changed model directory is selected.",
        ),
        (
            (MINICPM_PATH,),
            [make_comment("<!-- ci-stage-suggestion -->\npasted", user_type="User")],
            [],
            False,
            "POST",
            "/tag-and-rerun-ci minicpmo full-duplex",
        ),
        (
            (
                f"{MODELS}/fun_cosyvoice3/config.py",
                f"{MODELS}/moss_tts_local/config.py",
            ),
            [],
            ["run-tts-ci", "run-moss-tts-local-transformer-v1.5"],
            False,
            "POST",
            "left out of that run",
        ),
        (
            (MINICPM_PATH,),
            [],
            ["run-minicpm-o-4.5", "run-native-duplex"],
            False,
            None,
            None,
        ),
        ((MINICPM_PATH,), [], [], True, None, None),
    ],
)
def test_one_comment_is_written_only_by_the_bot_and_only_when_a_model_is_untested(
    suggester: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    changed_paths: tuple[str, ...],
    comments: list[dict[str, str | int]],
    labels: list[str],
    is_draft: bool,
    expected_method: str | None,
    expected_text: str | None,
) -> None:
    recording_github = RecordingGithub(
        labels=labels,
        comments=comments,
        is_draft=is_draft,
        changed_paths=changed_paths,
    )
    monkeypatch.setattr(suggester, "call_github", recording_github)

    suggester.update_stage_suggestion(REPOSITORY, PULL_REQUEST_NUMBER)

    if expected_method is None:
        assert recording_github.writes == []
    else:
        assert [method for method, body in recording_github.writes] == [expected_method]
        assert expected_text in recording_github.writes[0][1]


def test_an_unchanged_suggestion_is_not_written_again(
    suggester: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_run = RecordingGithub(labels=[], comments=[])
    monkeypatch.setattr(suggester, "call_github", first_run)
    suggester.update_stage_suggestion(REPOSITORY, PULL_REQUEST_NUMBER)
    second_run = RecordingGithub(
        labels=[], comments=[make_comment(first_run.writes[0][1])]
    )
    monkeypatch.setattr(suggester, "call_github", second_run)

    suggester.update_stage_suggestion(REPOSITORY, PULL_REQUEST_NUMBER)

    assert second_run.writes == []
