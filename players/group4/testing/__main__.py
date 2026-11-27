"""Start the Group 4 dashboard or run a batch from the terminal."""

import argparse
from pathlib import Path

from .batch import PRESETS, RESULTS, BatchSpec, create_batch, run_batch
from .server import Dashboard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", type=Path, default=RESULTS, help="Batch storage directory"
    )
    modes = parser.add_subparsers(dest="mode", required=True)
    gui = modes.add_parser("gui", help="Serve the local dashboard")
    gui.add_argument("--port", type=int, default=8765)
    batch = modes.add_parser("batch", help="Run a batch without the dashboard")
    batch.add_argument("--preset", choices=PRESETS, default="class")
    batch.add_argument("--label")
    batch.add_argument("--versions")
    batch.add_argument("--seeds")
    batch.add_argument("--memory", dest="memories")
    batch.add_argument("--lifetimes")
    batch.add_argument("--days")
    batch.add_argument("--initial", type=int)
    batch.add_argument("--cap", type=int)
    batch.add_argument("--workers", type=int)
    batch.add_argument("--timeout", type=float)
    batch.add_argument("--map", dest="map_path")
    args = parser.parse_args()

    if args.mode == "gui":
        with Dashboard(args.port, args.results.resolve()) as server:
            print(
                f"Group 4 dashboard at http://127.0.0.1:{server.server_port}",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return

    overrides = {
        key: value
        for key, value in vars(args).items()
        if value is not None and key not in {"mode", "preset", "results"}
    }
    try:
        spec = BatchSpec.parse({**PRESETS[args.preset], **overrides})
        folder = create_batch(spec, args.results.resolve())
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(f"Running batch {folder.name}", flush=True)
    run_batch(folder)
    print(folder, flush=True)


if __name__ == "__main__":
    main()
