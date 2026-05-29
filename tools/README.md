# Background upscaler (`upscale.py`)

Most AI image models top out around 1024–1536px on the long edge, but the booth
wants **6000×4000** backgrounds (see `make_samples.py`). `upscale.py` runs the
[Real-ESRGAN x4plus](https://github.com/xinntao/Real-ESRGAN) model to 4× an
image, then fits the result to the target size.

The model is loaded via [`spandrel`](https://github.com/chaiNNer-org/spandrel),
which sidesteps the broken `basicsr`/`realesrgan` packages on modern
torch/Python.

## Setup (one time)

The upscaler pulls in a large PyTorch dependency (~2–3 GB) that the booth itself
does **not** need, so its requirements are kept separate from the runtime
`requirements.txt`. Install into a dedicated venv:

```bash
python -m venv .venv-upscale
.venv-upscale/bin/pip install -r tools/requirements-upscale.txt
```

The model weights (~65 MB) download once to `tools/.cache/` on first run.
On Apple Silicon it uses the GPU via MPS automatically.

## Usage

```bash
# AI art -> backgrounds/my_art.jpg, fit to 6000x4000 (center-cropped)
.venv-upscale/bin/python tools/upscale.py my_art.png

# a whole folder of art -> backgrounds/
.venv-upscale/bin/python tools/upscale.py incoming/ -o backgrounds/

# explicit output, raw 4x (no resize/crop)
.venv-upscale/bin/python tools/upscale.py in.png -o out.png --size none
```

If `-o/--output` is omitted, output goes to `backgrounds/<name>.jpg`.

## Options

| Flag | Default | What it does |
| --- | --- | --- |
| `--size WxH` \| `none` | `6000x4000` | Final size after upscaling. `none` keeps the raw 4× output. |
| `--fit cover` \| `contain` \| `exact` | `cover` | How to reach `--size`. `cover` fills then center-crops; `contain` fits inside; `exact` ignores aspect ratio. |
| `--tile` | `512` | Tile size in input px (memory tuning). |
| `--pad` | `32` | Tile overlap in input px (hides seams). |
| `--device auto` \| `cpu` \| `mps` \| `cuda` | `auto` | Compute device. |

## Tips

- Feed it the **largest** your AI model will produce. 4× of 1536×1024 is
  6144×4096, which `cover` trims to 6000×4000 with almost no crop.
- A **3:2** source wastes the least — `cover` center-crops to 3:2, so if the
  source isn't already 3:2, keep anything important near the center.
- The model runs in overlapping tiles, so output memory stays bounded even at
  4000px tall. Lower `--tile` if you hit memory pressure; raise it for speed.
