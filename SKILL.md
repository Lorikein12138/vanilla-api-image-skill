---
name: vanilla-api-image-skill
description: Use when generating or editing images with the Vanilla API, including text-to-image, image-to-image, masked edits, size or aspect-ratio selection, or `VANILLA_API_IMAGE` image requests.
---

# Vanilla API Image Skill

Check that `VANILLA_API_IMAGE` is set before generating or editing images. If it is missing, stop and ask the user to configure it.

Use `scripts/vanilla_api_image.py` for all image operations.

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

- `--prompt`: Required generation or edit prompt.
- `--image`: Required for `edit`; repeat it for multiple input images.
- `--mask`: Optional mask image for `edit`.
- `--size`: Choose `1k`, `2k`, or `4k`.
- `--ratio`: Choose `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `16:9`, or `9:16`.

Generated images save in `outputs/`, and the script prints absolute paths for each saved file.

After generating images, send the image file paths back to the user. Always use full absolute paths.
