---
name: vanilla-api-image-skill
description: Use when generating or editing images with the Vanilla API, including text-to-image, image-to-image, masked edits, size or aspect-ratio selection, or `VANILLA_API_IMAGE` image requests.
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

## Options

- `--prompt`: required generation/edit prompt.
- `--image`: required for `edit`; repeat for multiple inputs.
- `--mask`: optional edit mask.
- `--size`: `1k`, `2k`, `3k`, `4k`.
- `--ratio`: `1:1`, `1:2`, `2:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `16:9`, `9:16`.

Images save in `outputs/`; the script prints absolute paths. Return those full paths to the user.
