# Environment sensor heatmap (“pixel display”)

This document describes how the **Sensor field (reference)** heatmap on the **Silo: Environment** Grafana dashboard works end to end: game data in Postgres, the Flask job that writes it, and how Grafana is configured (including dashboard hacks for square pixels and clean axes).

## What it is supposed to look like

The visualization is a **48×48** grid used as a low-res **pixel display**:

- **Horizontal axis = time.** There are **48 distinct timestamps** (synthetic “history”), shown as 48 columns along time.
- **Vertical axis = pixel row** (0–47). Each timestamp carries **one vertical strip** of 48 luminance samples.

So the logical picture is **48 time slices × 48 rows = 2304 cells**, all values in **[0, 1]** for the Turbo color scale.

Grafana’s heatmap plugin is fed data in a shape it understands as **heatmap rows** (wide format): see [Grafana Heatmap data](https://grafana.com/developers/dataplane/heatmap).

## Constants (`app/constants.py`)

| Constant | Role |
|----------|------|
| `ENVIRONMENT_PIXEL_GRID_COLS` | Width of the sampled frame (48). Must match the number of time columns for this layout. |
| `ENVIRONMENT_PIXEL_GRID_ROWS` | Height of the frame / length of each stored strip (48). |
| `ENVIRONMENT_PIXEL_TIME_COLUMNS` | Same as the number of backfilled timestamps (48). |
| `ENVIRONMENT_PIXEL_BACKFILL_SAMPLES` | Equals `ENVIRONMENT_PIXEL_TIME_COLUMNS`: one DB row per timestamp. |
| `ENVIRONMENT_PIXEL_BACKFILL_SPAN_SECONDS` | Seconds spanned from **oldest** strip to **newest** (= latest tick time). Default **900** (15 minutes). |
| `ENVIRONMENT_PIXEL_REFERENCE_TICK_NOISE_HALF_RANGE` | Uniform jitter ± this amount on top of reference luminance each tick (clamped to [0, 1]). |
| `BAD_APPLE_FRAME_COUNT` | How many PNGs must exist under **`app/assets/images/bad_apple`** before the sequence is used ( **`frame_00.png`** through **`frame_{N−1}.png`** with **N** = this constant). |

**Grafana alignment:** the Environment dashboard defines a hidden template variable **`heatmap_history_seconds`** (default **900**). The panel SQL only reads rows in **`NOW() − heatmap_history_seconds … NOW()`**. Keep that value in sync with `ENVIRONMENT_PIXEL_BACKFILL_SPAN_SECONDS` so the synthetic history always fits the query window.

## Reference image and noise (`app/environment_pixel_reference.py`)

- Static PNG: **`app/static/environment_pixel_reference.png`**.
- `environment_pixel_cells_from_reference_image(grid_cols, grid_rows)` downsamples **RGB → luminance** (simple sRGB-ish weights) onto the grid cell centers and returns a row-major **`list[list[float]]`** in **[0, 1]**, or **`None`** if the file is missing or Pillow is unavailable.
- **`apply_uniform_tick_noise`** adds independent uniform noise in **`[-half_range, +half_range]`** per cell and clamps.

If the reference cannot be loaded, `record_environment_pixel_noise_sample` falls back to **pure uniform random** noise for the frame.

## Bad Apple PNG sequence (`app/bad_apple_frames.py`)

When **every** file **`frame_00.png` … `frame_{N−1}.png`** (**N** = **`BAD_APPLE_FRAME_COUNT`**) exists under **`app/assets/images/bad_apple/`**, the heatmap uses that clip instead of the static goat reference:

- Frames are loaded with Pillow, converted to RGB, then downsampled with the same luminance helper as the reference path (`luminance_grid_from_rgb_buffer` in `app/environment_pixel_reference.py`).
- **`ENVIRONMENT_PIXEL_REFERENCE_TICK_NOISE_HALF_RANGE`** jitter is still applied on top of each sampled frame.

**Tick vs new player:** each **`game_tick`** calls **`advance_bad_apple_frame_index()`** once (shared counter for all players), then passes that index into **`record_environment_pixel_noise_sample`**. Minting a player calls **`current_bad_apple_frame_index()`** so new users see the **current** clip frame without advancing the sequence.

Fallback order if assets are incomplete: **Bad Apple** → **`environment_pixel_reference.png`** → **uniform random**.

### Extracting frames from a video (ffmpeg)

Assets live next to the Flask package: **`app/assets/images/bad_apple/`** (same folder **`bad_apple_frames_dir()`** resolves to). You need exactly **`BAD_APPLE_FRAME_COUNT`** files with zero-padded two-digit indices.

**Example:** extract **20** frames, **one per second** from the start of a clip:

```bash
mkdir -p app/assets/images/bad_apple
ffmpeg -y -i /path/to/source.mp4 -vf "fps=1" -frames:v 20 \
  app/assets/images/bad_apple/frame_%02d.png
```

**ffmpeg numbering:** the **`image2`** muxer usually writes **`frame_01.png` … `frame_20.png`** (1-based). The game expects **`frame_00.png` … `frame_19.png`**. After extraction, rename in the target directory:

```bash
cd app/assets/images/bad_apple
for i in $(seq 1 20); do mv "frame_$(printf '%02d' "$i").png" "_tmp_$((i-1)).png"; done
for i in $(seq 0 19); do mv "_tmp_$i.png" "frame_$(printf '%02d' "$i").png"; done
```

(Adjust **`20`** / loop bounds if you change **`BAD_APPLE_FRAME_COUNT`** in `app/constants.py`.)

**Start offset:** seek before the input for speed (keyframe-aligned):

```bash
ffmpeg -y -ss 02:48 -i /path/to/source.mp4 -vf "fps=1" -frames:v 20 \
  app/assets/images/bad_apple/frame_%02d.png
```

**Different spacing:** use a fractional frame rate — e.g. one frame every **2** seconds: **`-vf "fps=1/2"`**; every **3** seconds: **`-vf "fps=1/3"`**.

Requires **ffmpeg** on your PATH. Resolution and aspect of the source PNGs are arbitrary; they are resampled to **`ENVIRONMENT_PIXEL_GRID_COLS` × `ENVIRONMENT_PIXEL_GRID_ROWS`** for the heatmap.

**Other clips:** the Social dashboard theater heatmap uses separate subfolders under **`app/assets/images/`** (see **`MOVIE_PIXEL_ASSET_SUBDIR_BY_ID`** in **`app/movie_pixel_frames.py`**). Use the same **`frame_00.png` … `frame_{N−1}.png`** naming (**N** = **`SOCIAL_MOVIE_PIXEL_SEQUENCE_FRAME_COUNT`** in **`app/constants.py`**, currently **60**) and the same ffmpeg ideas as above; spacing in source video is a content choice per movie.

## Writing samples (`app/jobs.py`)

**`record_environment_pixel_noise_sample(user_id, tick_time, animation_frame_index=0)`**

1. **Deletes** all `environment_pixel_noise_samples` for that user.
2. Builds one **48×48** frame: **Bad Apple** frame **`animation_frame_index`** if the sequence is complete; else **reference goat PNG** + jitter; else **random** noise.
3. Computes timestamp spacing: **`delta_s = span_s / (n_snapshots − 1)`** when `n_snapshots > 1`, so the oldest sample is at **`tick_time − span_s`** and the newest at **`tick_time`** (endpoints span the full window).
4. For **`i = 0 … 47`**, inserts a row with:
   - **`timestamp`**: `tick_time − (n_snapshots − 1 − i) * delta_s`  
     (column **`i`** of the frame maps forward in time toward **`tick_time`**).
   - **`cells`**: **`[frame[0][i], frame[1][i], …, frame[47][i]]`** — one JSON array of **48** floats (the vertical strip at spatial column **`i`**).
   - **`grid_cols = 1`**, **`grid_rows = 48`**.

Called from **`game_tick`** for each player (with the advanced Bad Apple index) and when a **new player** is created (`app/routes.py`, using **`current_bad_apple_frame_index()`**), so data exists immediately and refreshes every tick.

## Database model (`app/models.py`)

**`EnvironmentPixelNoiseSample`**

- Table: **`environment_pixel_noise_samples`**.
- **`cells`**: JSON array of **48** floats (strip); not a full 48×48 matrix per row.
- **`timestamp`**: identifies the strip’s position on the **time** axis.

## Grafana query (`grafana/dashboards/environment.json`, panel id **27**)

The datasource is Postgres. The query:

1. **`in_range`** — rows for `$user_id` whose **`timestamp`** falls in **`[NOW() − heatmap_history_seconds, NOW()]`**.
2. **`strip`** — expands **`cells`** JSON with **`jsonb_array_elements(... WITH ORDINALITY)`** → `(timestamp, row_idx, val)` where **`row_idx`** is **0 … 47**.
3. **Pivot** — **`GROUP BY timestamp`** with **`MAX(val) FILTER (WHERE row_idx = k) AS "k"`** for **`k = 0 … 47`**.

Result shape Grafana expects for wide heatmaps:

- **`time`** — X axis (time).
- **Numeric columns `"0"` … `"47"`** — Y buckets (pixel rows).

### Panel options that matter

- **`calculate`: `false`** — Data is already bucketed. With **`calculate`: `true`**, Grafana recomputes buckets from raw points and breaks both coloring and semantics (e.g. saturated reds).
- **`legend.show`: `false`** — Saves horizontal space for the square-layout hack.

The panel also uses **`timeFrom`: `"15m"`** so the panel’s local range matches the synthetic span.

## Square grid (browser / uPlot)

Grafana sizes the heatmap’s uPlot from the **panel’s rectangle**, so the logical **48×48** grid is drawn on a **non-square** viewport and pixels **stretch**.

The **first Text panel** (dashboard header, id **1**) runs **`siloSquareHeatmapPanel27`**:

- Finds the heatmap wrapper (**`[data-panelid="27"]`**, with fallbacks).
- Measures the panel body and sets **`S = min(rawW, rawH)`**.
- Clips the VizLayout host (**`uplot.closest('[data-testid*="viz-layout"]')`**) to **`S × S`** and centers it.
- Applies **`transform: scale(S/rawW, S/rawH)`** on **`.uplot`** with **`transform-origin: top left`** so **non-uniform** scale turns equal X/Y bucket counts into **square** cells.

This runs on **`requestAnimationFrame`** so it survives refreshes and React/uPlot updates.

## Axis tick labels

- **Y:** panel option **`yAxis.axisPlacement`: `"hidden"`** (no ordinal labels / axis strip).
- **X (time):** the header Text panel injects CSS (**`#silo-env-heatmap-hide-axis-labels`**) hiding **`.u-axis text`** and **`.u-value`** inside panel **27**’s uPlot (the heatmap JSON does not expose full X-axis hiding).

## Files to touch when changing behavior

| Concern | Location |
|---------|----------|
| Grid size, span, noise | `app/constants.py` |
| What gets written each tick | `app/jobs.py` → `record_environment_pixel_noise_sample`, `advance_bad_apple_frame_index` / `current_bad_apple_frame_index` |
| Bad Apple sequence | `app/bad_apple_frames.py`, `app/assets/images/bad_apple/frame_*.png` |
| Goat reference PNG / luminance helpers | `app/environment_pixel_reference.py`, `app/static/environment_pixel_reference.png` |
| SQL / panel options / hacks | `grafana/dashboards/environment.json` |
| Query window constant | Dashboard templating → **`heatmap_history_seconds`** |

After editing the provisioned dashboard JSON, reload Grafana or restart the **`grafana`** container so provisioning picks up changes.
