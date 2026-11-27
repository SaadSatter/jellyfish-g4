# Group 4 testing

From the repository root, start the dashboard.

```sh
uv run python -m players.group4.testing gui
```

Open `http://127.0.0.1:8765`. From `players/group4`, you can start the same app with `uv run --directory ../.. python -m players.group4.testing gui`. Use `gui --port 8766` if port 8765 is busy.

## Run and compare

- Use **Class replay** for seeds 95527, 94339, and 75384 at the original class settings.
- Use **Replacement** for a fixed set of 12 seeds, a three-year lifespan, a ten-year run, and several memory sizes. Use the class replay seeds or a separate seed list for tuning.
- Enter a Git ref to test Group 4's `player.py` at that revision, `worktree` to test the current file, or a selector such as `group:3` for a peer. Only run code from sources you trust.
- Select two versions and a set of settings. Click a seed to overlay its population histories. The final-population list includes pairs with a completed outcome for both versions.
- Use **All runs** to filter, inspect, export CSV, or open a replay in the existing simulator viewer. **Full results JSON** includes the exact batch manifest and the daily population histories.

To run the class preset without the dashboard:

```sh
uv run python -m players.group4.testing batch --preset class
```

The batch command accepts comma-separated lists for `--versions`, `--seeds`, `--memory`, `--lifetimes`, and `--days`. Run `batch --help` for more options. The global `--results PATH` option goes before the mode. Use the same results path in both modes to view a terminal batch later.

## What a batch records

Each run starts alone in slot zero. With the same seed and settings, players start with the same ocean, initial positions, and random streams. Different policies can use those streams in a different order.

The suite saves the simulator sources, selected player sources, map, and exact settings in its local `results/` folder. Replay uses the saved sources and settings. Each completed job saves its summary and population history separately. Crashes and timeouts have their own status and do not count as extinction.

The dashboard shows the numerator and denominator of each rate. Replacement requires at least the starting population after founder lifespan. The 10J reach rate requires a cap of at least ten times the starting population.

Run the suite's checks with `uv run python -m pytest players/group4/testing`.

Dashboard design pass based on unslop by Damir Sokolovsky (@heydamir), CC BY 4.0.
