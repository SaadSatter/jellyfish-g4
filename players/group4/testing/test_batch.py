import sys
from dataclasses import replace

import pytest

from core.parse_args import parse_args
from core.runner import build_engine
from players.group4.testing import batch


def settings(**overrides):
    return {
        "versions": "worktree, group:4",
        "seeds": "94339, 94339",
        "memories": "92",
        "lifetimes": "0.02",
        "days": "20",
        "initial": 5,
        **overrides,
    }


def test_frozen_batch_matches_the_engine_and_separates_histories(tmp_path):
    spec = batch.BatchSpec.parse(settings())
    folder = batch.create_batch(spec, tmp_path)
    manifest = batch.read_json(folder / "manifest.json")
    assert len(manifest["jobs"]) == 2
    assert manifest["versions"][0]["sha256"]
    assert (folder / "simulator/core/engine.py").is_file()
    assert (folder / "versions/0.py").is_file()

    engine = build_engine(
        parse_args(
            [
                "--players",
                "4",
                "--seed",
                "94339",
                "-J",
                "5",
                "-M",
                "92",
                "-S",
                "0.02",
                "-T",
                "20",
            ]
        )
    )
    engine.run()
    expected = engine.groups[0].stats
    batch.run_batch(folder)
    summaries = batch.batch_data(folder)["results"]
    assert set(summaries) == {"1", "2"}
    for run_id, summary in summaries.items():
        assert summary["status"] == "extinct"
        assert summary["founders_out"]
        assert "history" not in summary
        assert batch.run_data(folder, int(run_id))["history"] == expected.history
        assert all(
            summary["stats"][key] == value for key, value in expected.to_dict().items()
        )


def test_timeout_is_recorded_without_an_extinction(tmp_path, monkeypatch):
    spec = replace(batch.BatchSpec.parse(settings(versions="worktree")), timeout=0.1)
    folder = batch.create_batch(spec, tmp_path)
    monkeypatch.setattr(
        batch,
        "worker_command",
        lambda *args: [sys.executable, "-c", "from time import sleep; sleep(10)"],
    )
    batch.run_batch(folder)
    result = batch.run_data(folder, 1)
    assert result["status"] == "timeout"
    assert "stats" not in result


def test_player_exception_is_recorded_as_a_crash(tmp_path):
    folder = batch.create_batch(
        batch.BatchSpec.parse(settings(versions="worktree")), tmp_path
    )
    (folder / "versions/0.py").write_text(
        "from core.player import Player\n"
        "class Player4(Player):\n"
        "    def act(self, snapshot):\n"
        "        raise RuntimeError('test crash')\n",
        encoding="utf-8",
    )
    batch.run_batch(folder)
    record = batch.run_data(folder, 1)
    assert record["status"] == "crash"
    assert "test crash" in record["error"]


def test_invalid_settings_leave_no_saved_batch(tmp_path):
    with pytest.raises(ValueError, match="cap"):
        batch.BatchSpec.parse(settings(cap=4))
    with pytest.raises(ValueError, match="Unknown player"):
        batch.create_batch(
            batch.BatchSpec.parse(settings(versions="group:999")), tmp_path
        )
    assert list(tmp_path.iterdir()) == []
