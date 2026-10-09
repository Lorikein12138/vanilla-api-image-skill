---
name: vanilla-api-image-skill
description: Use when generating or editing images with the Vanilla API, including text-to-image, image-to-image, masked edits, size or aspect-ratio selection, gpt-image-2 / gpt-image-2.5-flare / gpt-image-2.5-sunburst model choice, or `VANILLA_API_IMAGE` image requests.
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

- `gpt-image-2.5-sunburst` (default): higher-precision GPT Image 2.5 model, strongest at detail-preserving edits. Snapshot `gpt-image-2.5-sunburst-2026-09-08`.
- `gpt-image-2.5-flare`: faster GPT Image 2.5 model. Snapshot `gpt-image-2.5-flare-2026-09-08`.
- `gpt-image-2`: previous generation, snapshot `gpt-image-2-2026-04-21`.

Use the default model unless the user names a model or asks for speed (flare).

## Options

- `--prompt`: required generation/edit prompt, up to 32000 characters.
- `--image`: required for `edit`; png, jpeg or webp under 50MB. Repeat for up to 16 inputs.
- `--mask`: optional edit mask; PNG under 4MB, same size as the first image, which it applies to.
- `--size`: `1k`, `2k`, `3k`, `4k`, or `auto` to let the model choose.
- `--ratio`: `1:1`, `1:2`, `2:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `16:9`, `9:16`. Required unless `--size auto`.
- `--model`: see Models.
- `--quality`: `auto` (default), `low`, `medium`, `high`; `xhigh` and `max` only with gpt-image-2.5 models.
- `--n`: number of images, 1-10.
- `--background`: `auto`, `opaque`, `transparent` (transparent needs png or webp output; preview on gpt-image-2).
- `--output-format`: `png` (API default), `jpeg`, `webp`.
- `--output-compression`: 0-100, only with jpeg or webp output.
- `--moderation`: `auto` or `low`; `generate` only.
- `--partial-images`: 0-3; streams the response and saves partial previews.
- `--user`: optional end-user identifier.

Only pass options the user asked for; omitted options use API defaults.

Images save in `outputs/`; the script prints absolute paths of final images on stdout (partial previews are listed on stderr). Return those full paths to the user.
