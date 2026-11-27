"""Define matched trials and save their inputs and results."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core import constants as c
from core.args import PLAYERS, MapArgs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESULTS = HERE / "results"
PLAYER_FILE = Path("players/group4/player.py")
BATCH_ID = re.compile(r"[0-9a-f]{12}")
REPLAY_SEEDS = (95527, 94339, 75384)
VALIDATION_SEEDS = (
    10861,
    14720,
    20843,
    25068,
    30581,
    39621,
    46418,
    53649,
    61872,
    71502,
    83251,
    97642,
)
PRESETS = {
    "class": {
        "seeds": REPLAY_SEEDS,
        "memories": (1024,),
        "lifetimes": (205,),
        "days": (1800,),
    },
    "replacement": {
        "seeds": VALIDATION_SEEDS,
        "memories": (8, 92, 1024),
        "lifetimes": (3,),
        "days": (3600,),
    },
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def git(*arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, capture_output=True, check=False
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Git failed")
    return result.stdout


def maps() -> list[str]:
    return sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "maps").rglob("*.json")
    )


def number[T: (int, float)](value: object, kind: type[T], low: float, high: float) -> T:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("Use a numeric value")
    if kind is int and isinstance(value, float) and not value.is_integer():
        raise ValueError("Use a whole number")
    parsed = kind(value)
    if not low <= parsed <= high:
        raise ValueError(f"Use a value from {low:g} to {high:g}")
    return parsed


def field_values(value: object) -> list:
    if isinstance(value, str):
        values = re.split(r"[,\s]+", value.strip()) if value.strip() else []
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise TypeError("Use a comma-separated list")
    if not values:
        raise ValueError("Enter at least one value")
    return values


@dataclass(frozen=True)
class BatchSpec:
    label: str
    versions: tuple[str, ...]
    seeds: tuple[int, ...]
    memories: tuple[int, ...]
    lifetimes: tuple[float, ...]
    days: tuple[int, ...]
    initial: int
    cap: int
    workers: int
    timeout: float
    map_path: str

    @classmethod
    def parse(cls, data: dict) -> BatchSpec:
        def grid(key, kind, low, high):
            try:
                return tuple(
                    dict.fromkeys(
                        number(v, kind, low, high) for v in field_values(data[key])
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{key.capitalize()} — {error}") from None

        versions = field_values(data.get("versions", "313562f, worktree"))
        if not all(
            isinstance(ref, str) and re.fullmatch(r"[\w./:@~^+\-]{1,160}", ref)
            for ref in versions
        ):
            raise ValueError("Use Git refs, worktree, or selectors such as group:3")
        spec = cls(
            label=str(data.get("label") or "Simulation batch").strip()[:120],
            versions=tuple(dict.fromkeys(versions)),
            seeds=grid("seeds", int, -(2**53 - 1), 2**53 - 1),
            memories=grid("memories", int, c.MIN_MEMORY_BYTES, c.MAX_MEMORY_BYTES),
            lifetimes=grid("lifetimes", float, 0.001, 1_000_000),
            days=grid("days", int, 1, c.MAX_DAYS),
            initial=number(data.get("initial", 100), int, 1, 100_000),
            cap=number(data.get("cap", 2000), int, 1, 100_000),
            workers=number(data.get("workers", 3), int, 1, 16),
            timeout=number(data.get("timeout", 120), float, 0.1, 86_400),
            map_path=str(data.get("map_path", "")),
        )
        if spec.cap < spec.initial:
            raise ValueError("The cap must be at least the starting population")
        if spec.map_path and spec.map_path not in maps():
            raise ValueError("Select a map from the repository")
        size = math.prod(
            map(
                len,
                (spec.versions, spec.seeds, spec.memories, spec.lifetimes, spec.days),
            )
        )
        if size > 4096:
            raise ValueError("Limit a batch to 4,096 runs")
        return spec


def freeze_simulator(folder: Path) -> str:
    """Capture the actual sources used, including any working-tree edits."""
    digest = hashlib.sha256()
    for root in ("core", "players"):
        for source in sorted((ROOT / root).rglob("*.py")):
            if HERE in source.parents or "tests" in source.relative_to(ROOT).parts:
                continue
            relative = source.relative_to(ROOT)
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            contents = source.read_bytes()
            target.write_bytes(contents)
            digest.update(
                relative.as_posix().encode() + b"\0" + hashlib.sha256(contents).digest()
            )
    return digest.hexdigest()


def freeze_versions(folder: Path, references: tuple[str, ...]) -> list[dict]:
    versions = []
    for index, ref in enumerate(references):
        if ref.startswith("group:"):
            key = ref.removeprefix("group:")
            if key not in PLAYERS:
                raise ValueError(f"Unknown player {key}")
            versions.append(
                {"label": ref, "player": key, "commit": None, "source": None}
            )
            continue
        if ref == "worktree":
            contents = (folder / "simulator" / PLAYER_FILE).read_bytes()
            commit = None
        else:
            commit = (
                git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
                .decode()
                .strip()
            )
            contents = git("show", f"{commit}:{PLAYER_FILE.as_posix()}")
        source = f"versions/{index}.py"
        (folder / source).write_bytes(contents)
        versions.append(
            {
                "label": ref,
                "player": "4",
                "commit": commit,
                "source": source,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        )
    return versions


def create_batch(spec: BatchSpec, results: Path) -> Path:
    batch_id = uuid4().hex[:12]
    results.mkdir(parents=True, exist_ok=True)
    final = results / batch_id
    with tempfile.TemporaryDirectory(dir=results) as temporary:
        folder = Path(temporary)
        (folder / "versions").mkdir()
        (folder / "runs").mkdir()
        (folder / "histories").mkdir()
        digest = freeze_simulator(folder / "simulator")
        versions = freeze_versions(folder, spec.versions)
        map_sha = None
        if spec.map_path:
            contents = (ROOT / spec.map_path).read_bytes()
            (folder / "map.json").write_bytes(contents)
            MapArgs.read(folder / "map.json")
            map_sha = hashlib.sha256(contents).hexdigest()
        trials = itertools.product(
            range(len(versions)), spec.seeds, spec.memories, spec.lifetimes, spec.days
        )
        jobs = [
            {
                "id": index,
                "version": version,
                "seed": seed,
                "memory": memory,
                "lifetime": lifetime,
                "days": days,
            }
            for index, (version, seed, memory, lifetime, days) in enumerate(trials, 1)
        ]
        write_json(
            folder / "manifest.json",
            {
                "schema": 1,
                "id": batch_id,
                "created": datetime.now(UTC).isoformat(),
                "spec": asdict(spec),
                "versions": versions,
                "jobs": jobs,
                "simulator_commit": git("rev-parse", "HEAD").decode().strip(),
                "simulator_sha256": digest,
                "map_sha256": map_sha,
                "python": sys.version.split()[0],
            },
        )
        folder.rename(final)
    return final


def run_file(folder: Path, run_id: int) -> Path:
    return folder / "runs" / f"{run_id:04}.json"


def history_file(folder: Path, run_id: int) -> Path:
    return folder / "histories" / f"{run_id:04}.json"


def run_data(folder: Path, run_id: int) -> dict:
    result = read_json(run_file(folder, run_id))
    if "stats" in result:
        result["history"] = read_json(history_file(folder, run_id))
    return result


def worker_command(folder: Path, run_id: int, *, replay: bool = False) -> list[str]:
    command = [sys.executable, "-B", str(HERE / "worker.py"), str(folder), str(run_id)]
    return command + (["--replay"] if replay else [])


def run_batch(folder: Path) -> None:
    manifest = read_json(folder / "manifest.json")
    spec = manifest["spec"]

    def run(job: dict) -> None:
        path = run_file(folder, job["id"])
        with tempfile.TemporaryFile() as log:
            try:
                process = subprocess.run(
                    worker_command(folder, job["id"]),
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=spec["timeout"],
                    check=False,
                )
            except subprocess.TimeoutExpired:
                result = {
                    "status": "timeout",
                    "error": f"Exceeded {spec['timeout']:g} seconds",
                }
            except OSError as error:
                result = {"status": "error", "error": str(error)}
            else:
                if process.returncode == 0 and path.exists():
                    return
                log.seek(max(0, log.tell() - 4096))
                result = {
                    "status": "error",
                    "error": log.read().decode(errors="replace").strip()
                    or f"Worker exited with code {process.returncode}",
                }
        write_json(path, result)

    with ThreadPoolExecutor(max_workers=spec["workers"]) as pool:
        for _ in pool.map(run, manifest["jobs"]):
            pass


def batch_data(folder: Path, *, include_history: bool = False) -> dict:
    manifest = read_json(folder / "manifest.json")
    results = {}
    for path in sorted((folder / "runs").glob("*.json")):
        run_id = int(path.stem)
        results[str(run_id)] = (
            run_data(folder, run_id) if include_history else read_json(path)
        )
    return {"manifest": manifest, "results": results}
