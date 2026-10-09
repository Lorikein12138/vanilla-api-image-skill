#!/usr/bin/env python3
import argparse
import base64
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


BASE_URL = "https://api.lorikein.cn"
GENERATIONS_ENDPOINT = "/v1/images/generations"
EDITS_ENDPOINT = "/v1/images/edits"
DEFAULT_MODEL = "gpt-image-2.5-sunburst"
DEFAULT_QUALITY = "auto"
TIMEOUT_SECONDS = 600
USER_AGENT = "vanilla-api-image-skill/1.1"

GPT_IMAGE_25_MODELS = (
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
)
SUPPORTED_MODELS = GPT_IMAGE_25_MODELS + ("gpt-image-2",)

BASE_QUALITIES = ("auto", "low", "medium", "high")
GPT_IMAGE_25_QUALITIES = ("xhigh", "max")
SUPPORTED_QUALITIES = BASE_QUALITIES + GPT_IMAGE_25_QUALITIES
SUPPORTED_BACKGROUNDS = ("auto", "opaque", "transparent")
SUPPORTED_OUTPUT_FORMATS = ("png", "jpeg", "webp")
SUPPORTED_MODERATION = ("auto", "low")
OUTPUT_SUFFIXES = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}

MAX_PROMPT_CHARS = 32_000
MAX_N = 10
MAX_PARTIAL_IMAGES = 3
MAX_EDIT_IMAGES = 16
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_MASK_BYTES = 4 * 1024 * 1024

AUTO_SIZE = "auto"
SUPPORTED_TIERS = {
    "1k": 1024,
    "2k": 2048,
    "3k": 3072,
    "4k": 3840,
}
SUPPORTED_RATIOS = {
    "1:1": (1, 1),
    "1:2": (1, 2),
    "2:1": (2, 1),
    "2:3": (2, 3),
    "3:2": (3, 2),
    "3:4": (3, 4),
    "4:3": (4, 3),
    "4:5": (4, 5),
    "5:4": (5, 4),
    "16:9": (16, 9),
    "9:16": (9, 16),
}

MAX_EDGE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MULTIPLE = 16


def normalize_ratio(value: str) -> str:
    ratio = value.strip().replace("：", ":")
    if ratio not in SUPPORTED_RATIOS:
        choices = ", ".join(SUPPORTED_RATIOS)
        raise argparse.ArgumentTypeError(f"unsupported ratio {value!r}; choose one of: {choices}")
    return ratio


def resolve_size(tier: str, ratio: str | None) -> str:
    if tier == AUTO_SIZE:
        if ratio:
            raise ValueError("--ratio cannot be combined with --size auto")
        return AUTO_SIZE
    if tier not in SUPPORTED_TIERS:
        choices = ", ".join([*SUPPORTED_TIERS, AUTO_SIZE])
        raise ValueError(f"unsupported size tier {tier!r}; choose one of: {choices}")
    if not ratio:
        raise ValueError(f"--ratio is required with --size {tier}")

    width_ratio, height_ratio = SUPPORTED_RATIOS[ratio]
    target_long = SUPPORTED_TIERS[tier]
    ratio_long = max(width_ratio, height_ratio)
    ratio_short = min(width_ratio, height_ratio)

    if ratio_long / ratio_short > 3:
        raise ValueError(f"ratio {ratio} exceeds the 3:1 long-edge limit")

    unit = math.lcm(
        MULTIPLE // math.gcd(width_ratio, MULTIPLE),
        MULTIPLE // math.gcd(height_ratio, MULTIPLE),
    )
    max_by_edge = MAX_EDGE // (ratio_long * unit)
    max_by_tier = target_long // (ratio_long * unit)
    max_by_pixels = int(math.floor(math.sqrt(MAX_PIXELS / (width_ratio * height_ratio * unit * unit))))

    scale = min(max_by_edge, max_by_tier, max_by_pixels)
    if scale < 1:
        raise ValueError(f"cannot map {tier} {ratio} into a valid image size")

    width = width_ratio * unit * scale
    height = height_ratio * unit * scale

    if width * height < MIN_PIXELS:
        min_by_pixels = int(math.ceil(math.sqrt(MIN_PIXELS / (width_ratio * height_ratio * unit * unit))))
        scale = min_by_pixels
        width = width_ratio * unit * scale
        height = height_ratio * unit * scale

    validate_dimensions(width, height)
    return f"{width}x{height}"


def validate_dimensions(width: int, height: int) -> None:
    pixels = width * height
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge > MAX_EDGE:
        raise ValueError(f"long edge {long_edge}px exceeds {MAX_EDGE}px")
    if width % MULTIPLE or height % MULTIPLE:
        raise ValueError(f"dimensions must be multiples of {MULTIPLE}px")
    if long_edge / short_edge > 3:
        raise ValueError("long edge to short edge ratio exceeds 3:1")
    if pixels < MIN_PIXELS or pixels > MAX_PIXELS:
        raise ValueError(f"pixel count {pixels} is outside {MIN_PIXELS}-{MAX_PIXELS}")


def validate_options(args: argparse.Namespace) -> None:
    if not args.prompt.strip():
        raise ValueError("prompt must not be empty")
    if len(args.prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")
    if args.quality in GPT_IMAGE_25_QUALITIES and args.model not in GPT_IMAGE_25_MODELS:
        raise ValueError(f"quality {args.quality!r} is only supported by gpt-image-2.5-flare and gpt-image-2.5-sunburst")
    if args.n is not None and not 1 <= args.n <= MAX_N:
        raise ValueError(f"--n must be between 1 and {MAX_N}")
    if args.partial_images is not None and not 0 <= args.partial_images <= MAX_PARTIAL_IMAGES:
        raise ValueError(f"--partial-images must be between 0 and {MAX_PARTIAL_IMAGES}")
    if args.output_compression is not None:
        if not 0 <= args.output_compression <= 100:
            raise ValueError("--output-compression must be between 0 and 100")
        if args.output_format not in ("jpeg", "webp"):
            raise ValueError("--output-compression requires --output-format jpeg or webp")
    if args.background == "transparent" and args.output_format == "jpeg":
        raise ValueError("--background transparent requires --output-format png or webp")


def build_url(endpoint: str) -> str:
    return f"{BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"


def output_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "outputs"


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def require_api_key() -> str:
    api_key = os.environ.get("VANILLA_API_IMAGE")
    if not api_key:
        raise RuntimeError("missing environment variable VANILLA_API_IMAGE")
    return api_key


def request_headers(api_key: str, stream: bool) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream" if stream else "application/json",
        "User-Agent": USER_AGENT,
    }


def read_json_response(resp) -> dict:
    body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON response. Raw body: {body}") from exc


def raise_for_event_error(event: dict) -> None:
    if event.get("type") == "error" or ("error" in event and "type" not in event):
        error = event.get("error", event)
        raise RuntimeError(f"stream returned an error: {json.dumps(error, ensure_ascii=False)}")


def read_event_stream(resp) -> list[dict]:
    events = []
    data_lines: list[str] = []

    def flush() -> None:
        data = "\n".join(data_lines)
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse stream event: {data}") from exc
        raise_for_event_error(event)
        events.append(event)

    for raw_line in resp:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    flush()
    return events


def http_error_message(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return f"request failed with HTTP {error.code}: {body}"


def send_request(url: str, api_key: str, body: bytes, content_type: str, stream: bool) -> dict | list[dict]:
    headers = request_headers(api_key, stream) | {"Content-Type": content_type}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            # Fall back to a plain JSON body when the server ignores `stream`.
            if stream and "text/event-stream" in resp.headers.get("Content-Type", ""):
                return read_event_stream(resp)
            return read_json_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(http_error_message(exc)) from exc


def post_json(url: str, api_key: str, payload: dict) -> dict | list[dict]:
    body = json.dumps(payload).encode("utf-8")
    return send_request(url, api_key, body, "application/json", bool(payload.get("stream")))


def form_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def quoted_filename(name: str) -> str:
    return name.replace("\\", "_").replace('"', "%22").replace("\r", "").replace("\n", "")


def post_multipart(
    url: str,
    api_key: str,
    data: dict,
    images: list[tuple[Path, str]],
    mask: tuple[Path, str] | None,
) -> dict | list[dict]:
    boundary = f"----vanilla-api-image-skill-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    chunks = []

    def add_field(name: str, value) -> None:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(form_value(value).encode("utf-8"))
        chunks.append(b"\r\n")

    def add_file(field_name: str, file_path: Path, mime_type: str) -> None:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{quoted_filename(file_path.name)}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(file_path.read_bytes())
        chunks.append(b"\r\n")

    for key, value in data.items():
        add_field(key, value)
    # OpenAI encodes an image array as repeated `image[]` parts and a single image as `image`.
    image_field = "image[]" if len(images) > 1 else "image"
    for image_path, mime_type in images:
        add_file(image_field, image_path, mime_type)
    if mask:
        add_file("mask", *mask)

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    return send_request(url, api_key, body, f"multipart/form-data; boundary={boundary}", bool(data.get("stream")))


def detect_image_mime(file_path: Path) -> str | None:
    with file_path.open("rb") as handle:
        header = handle.read(12)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def checked_input_file(path: str, label: str, max_bytes: int, allowed: tuple[str, ...]) -> tuple[Path, str]:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"{label} file not found: {file_path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{label} file {file_path} is {size} bytes; limit is {max_bytes} bytes")
    mime_type = detect_image_mime(file_path)
    if mime_type not in allowed:
        kinds = ", ".join(kind.split("/")[1] for kind in allowed)
        raise ValueError(f"{label} file {file_path} must be one of: {kinds}")
    return file_path, mime_type


def common_options(args: argparse.Namespace) -> dict:
    options = {
        "model": args.model,
        "prompt": args.prompt,
        "size": resolve_size(args.size, args.ratio),
        "quality": args.quality,
        "n": args.n,
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "user": args.user,
    }
    if args.partial_images is not None:
        options["stream"] = True
        options["partial_images"] = args.partial_images
    return {key: value for key, value in options.items() if value is not None}


def decode_or_download_image(item: dict, destination: Path) -> None:
    b64_json = item.get("b64_json")
    if b64_json:
        destination.write_bytes(base64.b64decode(b64_json))
        return

    url = item.get("url")
    if url:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            destination.write_bytes(resp.read())
        return

    raise RuntimeError("image result did not include b64_json or url")


def save_items(items: list[dict], stem: str, default_format: str, label: str = "") -> list[Path]:
    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    multiple = len(items) > 1
    for idx, item in enumerate(items, start=1):
        image_format = item.get("output_format") or default_format
        name = f"{stem}{label}_{idx:02d}" if multiple else f"{stem}{label}"
        destination = unique_path(out_dir, name, OUTPUT_SUFFIXES.get(image_format, ".png"))
        decode_or_download_image(item, destination)
        saved_paths.append(destination)
    return saved_paths


def save_result(result: dict | list[dict], requested_format: str | None) -> list[Path]:
    stem = datetime.now().strftime("%Y%m%d_%H%M%S")

    if isinstance(result, list):
        partials = [event for event in result if str(event.get("type", "")).endswith(".partial_image")]
        finals = [event for event in result if str(event.get("type", "")).endswith(".completed")]
        if not finals:
            raise RuntimeError("image stream ended without a completed event")
        for path in save_items(partials, stem, requested_format or "png", "_partial"):
            print(f"partial: {path}", file=sys.stderr)
        return save_items(finals, stem, requested_format or "png")

    data = result.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("image result did not include a non-empty data list")
    default_format = result.get("output_format") or requested_format or "png"
    return save_items(data, stem, default_format)


def generate_image(args: argparse.Namespace) -> list[Path]:
    validate_options(args)
    payload = common_options(args)
    if args.moderation is not None:
        payload["moderation"] = args.moderation
    api_key = require_api_key()
    result = post_json(build_url(GENERATIONS_ENDPOINT), api_key, payload)
    return save_result(result, args.output_format)


def edit_image(args: argparse.Namespace) -> list[Path]:
    validate_options(args)
    if len(args.image) > MAX_EDIT_IMAGES:
        raise ValueError(f"at most {MAX_EDIT_IMAGES} input images are supported")
    images = [
        checked_input_file(path, "image", MAX_IMAGE_BYTES, ("image/png", "image/jpeg", "image/webp"))
        for path in args.image
    ]
    mask = checked_input_file(args.mask, "mask", MAX_MASK_BYTES, ("image/png",)) if args.mask else None

    data = common_options(args)
    api_key = require_api_key()
    result = post_multipart(build_url(EDITS_ENDPOINT), api_key, data, images, mask)
    return save_result(result, args.output_format)


def print_size_table() -> None:
    table = {
        tier: {ratio: resolve_size(tier, ratio) for ratio in SUPPORTED_RATIOS}
        for tier in SUPPORTED_TIERS
    }
    print(json.dumps(table, ensure_ascii=False, indent=2))


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", choices=SUPPORTED_MODELS, default=DEFAULT_MODEL, help=f"image model (default: {DEFAULT_MODEL})")
    parser.add_argument("--size", choices=[*SUPPORTED_TIERS, AUTO_SIZE], required=True, help="size tier, or auto to let the model decide")
    parser.add_argument("--ratio", type=normalize_ratio, help="aspect ratio; required unless --size auto")
    parser.add_argument("--quality", choices=SUPPORTED_QUALITIES, default=DEFAULT_QUALITY, help="xhigh and max require a gpt-image-2.5 model")
    parser.add_argument("--n", type=int, help=f"number of images, 1-{MAX_N}")
    parser.add_argument("--background", choices=SUPPORTED_BACKGROUNDS, help="transparent requires png or webp output")
    parser.add_argument("--output-format", choices=SUPPORTED_OUTPUT_FORMATS, help="returned image format (API default: png)")
    parser.add_argument("--output-compression", type=int, help="0-100; only for jpeg or webp output")
    parser.add_argument("--partial-images", type=int, help=f"stream the response and save 0-{MAX_PARTIAL_IMAGES} partial images")
    parser.add_argument("--user", help="end-user identifier for abuse monitoring")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vanilla API image command line tool for text-to-image and image-to-image tasks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    table_parser = subparsers.add_parser("sizes", help="print the resolved size mapping table")
    table_parser.set_defaults(func=lambda args: print_size_table())

    generate_parser = subparsers.add_parser("generate", help="create an image from text")
    generate_parser.add_argument("--prompt", required=True, help="text prompt")
    add_common_arguments(generate_parser)
    generate_parser.add_argument("--moderation", choices=SUPPORTED_MODERATION, help="content moderation level")
    generate_parser.set_defaults(func=generate_image)

    edit_parser = subparsers.add_parser("edit", help="create an image from one or more input images")
    edit_parser.add_argument("--prompt", required=True, help="edit prompt")
    edit_parser.add_argument("--image", action="append", required=True, help="input image path; repeat for up to 16 images")
    edit_parser.add_argument("--mask", help="optional PNG mask applied to the first image")
    add_common_arguments(edit_parser)
    edit_parser.set_defaults(func=edit_image)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        result = args.func(args)
        if isinstance(result, list):
            for path in result:
                print(path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
