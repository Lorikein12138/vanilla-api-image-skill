---
name: vanilla-api-image-skill
description: Generate or edit images through Vanilla API using GPT Image 2, 2.5, or 2.6, including reference images, masks, markup, SVG, GIF, and async tasks.
---

# Vanilla API Image

Run `scripts/vanilla_api_image.py` from this skill directory. Use a `VANILLA_API_IMAGE` key from an image-enabled group. If it is missing, ask the user to configure it. Keep the key out of commands, logs, and files.

```bash
python scripts/vanilla_api_image.py generate --prompt "..." --size 1k --ratio 1:1
python scripts/vanilla_api_image.py edit --image input.png --prompt "..." --size 1k --ratio 1:1
```

- Pass the user's image prompt verbatim unless they ask for a rewrite. When drafting one, keep every visual requirement and omit repeated instructions. Use only reference images relevant to the task.
- Generate one final image unless more are requested. Choose the smallest size that meets the intended use. Preserve requested resolution, quality, and format. Leave quality and complexity to the model by default. Request previews only when needed. Vanilla API documents per-image billing by size tier.
- Default model is `gpt-image-2.6-sunburst`, the model listed in Vanilla API's guide. Pass an explicitly requested model ID. Other supported IDs are `gpt-image-2`, `gpt-image-2.5-sunburst`, `gpt-image-2.5-flare`, and `gpt-image-2.6-flare`. Availability depends on the key's group.
- Use `generate --help` or `edit --help` for flags as needed, and `sizes` for pixel mappings. PNG, JPEG, and WebP reference files can be passed with repeated `--image`. Vanilla API documents up to 32 MB per reference.

## Special outputs and edits

- Use `--mask` for a PNG mask or `--markup` for a transparent PNG annotation layer with 2.5 or 2.6. Match the first image's dimensions after orientation.
- Use 2.6 for `--complexity`, `generate --output-format svg`, or `generate/edit --output-format gif`. GIF accepts frame controls and `--size auto`. Vanilla API may choose the actual GIF dimensions. SVG and GIF use non-streaming requests. Transparent raster output uses PNG or WebP.

## Async and delivery

For long jobs or unstable connections, add `--async`. The script prints the task ID to stderr and polls until completion. After an interruption, run `python scripts/vanilla_api_image.py fetch <task_id>` with the same key. If submission times out before returning an ID, check Vanilla API usage before resubmitting.

Return the absolute paths printed on stdout. Inspect the saved image only when the task requires visual verification. See the [image guide](https://blog.lorikein.com/docs/vanilla-api/image/intro) and [async guide](https://blog.lorikein.com/docs/vanilla-api/image/async) for service details.
