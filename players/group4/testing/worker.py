"""Run one frozen trial in a separate interpreter."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from time import perf_counter


def run(folder: Path, run_id: int, *, replay: bool = False) -> None:
    sys.path.insert(0, str(folder / "simulator"))
    from core.args import PLAYERS, Args, MapArgs
    from core.runner import JellyRunner

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    job = manifest["jobs"][run_id - 1]
    version = manifest["versions"][job["version"]]
    settings = manifest["spec"]
    player = version["player"]
    if version["source"]:
        name = "players.group4.benchmark_version"
        module_spec = importlib.util.spec_from_file_location(
            name, folder / version["source"]
        )
        assert module_spec is not None and module_spec.loader is not None
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module
        module_spec.loader.exec_module(module)
        PLAYERS[player] = module.Player4

    args = Args(
        gui=replay,
        seed=job["seed"],
        players=[player],
        memory=job["memory"],
        lifetime_years=job["lifetime"],
        initial_jellyfish=settings["initial"],
        days=job["days"],
        max_population=settings["cap"],
        json_only=not replay,
        map=MapArgs.read(folder / "map.json") if settings["map_path"] else None,
    )
    runner = JellyRunner(args)
    if replay:
        runner.run_gui()
        return

    start = perf_counter()
    results = runner.run()
    group = runner.engine.groups[0]
    summary = results["groups"][group.name]
    status = (
        "crash" if group.error else "extinct" if not group.population else "complete"
    )
    record = {
        "status": status,
        "error": group.error,
        "stats": summary,
        "founders_out": job["days"] > runner.engine.config.lifetime_days,
        "elapsed_s": round(perf_counter() - start, 4),
    }
    from batch import history_file, run_file, write_json

    write_json(history_file(folder, run_id), group.stats.history)
    write_json(run_file(folder, run_id), record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("run_id", type=int)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    run(args.folder, args.run_id, replay=args.replay)
