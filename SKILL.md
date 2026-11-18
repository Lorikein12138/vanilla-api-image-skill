---
name: vanilla-api-image-skill
description: Generate or edit images with the Vanilla API using GPT Image 2, 2.5, or 2.6. Use for Vanilla API model selection, reference images, masks, annotated edits, SVG or animated GIF output, and requests using VANILLA_API_IMAGE.
---

# Vanilla API Image Skill

Before every image request, check `VANILLA_API_IMAGE`. If missing, stop and ask the user to store their Vanilla API image key in that environment variable. Never write or commit the key.

Use `scripts/vanilla_api_image.py` for all image operations. Pass the user's prompt directly unless they ask for prompt rewriting.

## Commands

- Text to image:

```bash
python scripts/vanilla_api_image.py generate --prompt "..." --size 1k --ratio 1:1
```

- Image to image:

```bash
python scripts/vanilla_api_image.py edit --image input.png --prompt "..." --size 1k --ratio 1:1
```

- Show resolved pixel sizes:

```bash
python scripts/vanilla_api_image.py sizes
```

## Models

| Family | Model IDs | Supported options |
| --- | --- | --- |
| GPT Image 2 | `gpt-image-2` | Shared options below |
| GPT Image 2.5 | `gpt-image-2.5-sunburst` (default), `gpt-image-2.5-flare` | Adds `xhigh`, `max`, and markup |
| GPT Image 2.6 | `gpt-image-2.6-sunburst`, `gpt-image-2.6-flare` | Adds `complexity`, SVG, and animated GIF to the 2.5 options |

Use the requested model when specified. Otherwise use 2.5 Sunburst, or 2.5 Flare when speed is requested. Select the corresponding 2.6 model when the task needs a 2.6 feature. Pass the full model ID to `--model`.

## Options

- `--prompt`: required generation/edit prompt, up to 32000 characters.
- `--image`: required for `edit`; png, jpeg or webp under 50MB. Repeat for up to 16 inputs.
- `--mask`: optional edit mask; PNG under 4MB, same size as the first image, which it applies to.
- `--size`: `1k`, `2k`, `3k`, `4k`, or `auto` to let the model choose.
- `--ratio`: `1:1`, `1:2`, `2:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `16:9`, `9:16`. Required unless `--size auto`.
- `--model`: see Models.
- `--quality`: `auto` (default), `low`, `medium`, `high`. Use `xhigh` or `max` with 2.5 or 2.6.
- `--n`: number of images, 1-10.
- `--background`: `auto`, `opaque`, `transparent` (transparent needs png or webp output; preview on gpt-image-2).
- `--output-format`: `png` (API default), `jpeg`, `webp`.
- `--output-compression`: 0-100, only with jpeg or webp output.
- `--moderation`: `auto` or `low`; `generate` only.
- `--partial-images`: 0-3; streams the response and saves partial previews.
- `--user`: optional end-user identifier.

Only pass options the user asked for; omitted options use API defaults.

## Model-specific options

- `edit --markup overlay.png` uploads a transparent PNG annotation layer with 2.5 or 2.6. Match the oriented dimensions of the first input image, leave transparent pixels, and keep both images within 16,777,216 pixels each. The overlay has a 50 MB limit. Use either `--markup` or `--mask`.
- `--complexity low|medium|high` controls planning for 2.6 generation and edits. The model defaults to `high`. Rendering quality still uses `--quality`.
- `generate --output-format svg` uses 2.6 and produces one SVG. Use `--svg-model` and optionally `--svg-reasoning-effort` only when SVG author selection is enabled for the account.
- `generate --output-format gif` or `edit --output-format gif` uses 2.6. Optional `--frame-count` is 2–16 (default 9), and `--frame-duration-ms` is at least 20 (default 200). Prefer `--size auto` for GIF or specify frame dimensions such as `--size 512x512` using multiples of 16. The provider applies its GIF preview access and total pixel limits across frames and images.
- SVG and GIF use non-streaming requests and API-managed compression. GIF edits accept still references and exclude masks.

Images save in `outputs/`; the script prints absolute paths of final images on stdout (partial previews are listed on stderr). Return those full paths to the user.
