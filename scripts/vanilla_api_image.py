#!/usr/bin/env python3
import argparse
import base64
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


BASE_URL = "https://api.lorikein.com"
GENERATIONS_ENDPOINT = "/v1/images/generations"
EDITS_ENDPOINT = "/v1/images/edits"
TASKS_ENDPOINT = "/v1/images/tasks"
DEFAULT_MODEL = "gpt-image-2.6-sunburst"
TIMEOUT_SECONDS = 600
POLL_INTERVAL_SECONDS = 3
TASK_TIMEOUT_SECONDS = 30 * 60
USER_AGENT = "vanilla-api-image-skill/1.3"

GPT_IMAGE_25_MODELS = (
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
)
GPT_IMAGE_26_MODELS = (
    "gpt-image-2.6-sunburst",
    "gpt-image-2.6-flare",
)
SUNBURST_FLARE_MODELS = GPT_IMAGE_25_MODELS + GPT_IMAGE_26_MODELS
SUPPORTED_MODELS = ("gpt-image-2",) + SUNBURST_FLARE_MODELS

BASE_QUALITIES = ("auto", "low", "medium", "high")
EXTENDED_QUALITIES = ("xhigh", "max")
SUPPORTED_QUALITIES = BASE_QUALITIES + EXTENDED_QUALITIES
SUPPORTED_COMPLEXITIES = ("low", "medium", "high")
SVG_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
SUPPORTED_BACKGROUNDS = ("auto", "opaque", "transparent")
RASTER_OUTPUT_FORMATS = ("png", "jpeg", "webp")
SUPPORTED_OUTPUT_FORMATS = RASTER_OUTPUT_FORMATS + ("gif", "svg")
SUPPORTED_MODERATION = ("auto", "low")
OUTPUT_SUFFIXES = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "gif": ".gif", "svg": ".svg"}

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
    if args.quality in EXTENDED_QUALITIES and args.model not in SUNBURST_FLARE_MODELS:
        raise ValueError(f"quality {args.quality!r} requires a GPT Image 2.5 or 2.6 model")
    if args.n is not None and not 1 <= args.n <= MAX_N:
        raise ValueError(f"--n must be between 1 and {MAX_N}")
    if args.partial_images is not None and not 0 <= args.partial_images <= MAX_PARTIAL_IMAGES:
        raise ValueError(f"--partial-images must be between 0 and {MAX_PARTIAL_IMAGES}")
    if args.asynchronous and args.partial_images is not None:
        raise ValueError("--async cannot be combined with --partial-images")
    if args.output_compression is not None:
        if not 0 <= args.output_compression <= 100:
            raise ValueError("--output-compression must be between 0 and 100")
        if args.output_format not in ("jpeg", "webp"):
            raise ValueError("--output-compression requires --output-format jpeg or webp")
    if args.background == "transparent" and args.output_format not in (None, "png", "webp"):
        raise ValueError("--background transparent requires --output-format png or webp")

    output_format = args.output_format
    if args.complexity is not None or output_format in ("gif", "svg"):
        if args.model not in GPT_IMAGE_26_MODELS:
            raise ValueError("--complexity and gif/svg output require a GPT Image 2.6 model")
    if args.markup and args.model not in SUNBURST_FLARE_MODELS:
        raise ValueError("--markup requires a GPT Image 2.5 or 2.6 model")
    if output_format in ("gif", "svg") and args.partial_images is not None:
        raise ValueError(f"{output_format} output does not support streaming or --partial-images")
    if output_format == "svg" and (args.command != "generate" or args.n not in (None, 1)):
        raise ValueError("svg output requires generate with a single image")
    if output_format == "gif" and args.mask:
        raise ValueError("gif output does not support --mask")
    if args.frame_count is not None or args.frame_duration_ms is not None:
        if output_format != "gif":
            raise ValueError("--frame-count and --frame-duration-ms require --output-format gif")
        if args.frame_count is not None and not 2 <= args.frame_count <= 16:
            raise ValueError("--frame-count must be between 2 and 16")
        if args.frame_duration_ms is not None and args.frame_duration_ms < 20:
            raise ValueError("--frame-duration-ms must be at least 20")


def resolve_request_size(args: argparse.Namespace) -> str:
    if args.output_format == "gif":
        dimensions = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", args.size)
        if dimensions:
            if args.ratio:
                raise ValueError("--ratio cannot be combined with explicit GIF dimensions")
            width, height = map(int, dimensions.groups())
            if width % MULTIPLE or height % MULTIPLE:
                raise ValueError(f"GIF dimensions must be multiples of {MULTIPLE}px")
            return args.size
    return resolve_size(args.size, args.ratio)


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
        raise RuntimeError("Vanilla API returned invalid JSON") from exc


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
            raise RuntimeError("Vanilla API returned an invalid stream event") from exc
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


def send_request(
    url: str,
    api_key: str,
    body: bytes | None = None,
    content_type: str | None = None,
    stream: bool = False,
    timeout: float = TIMEOUT_SECONDS,
) -> dict | list[dict]:
    headers = request_headers(api_key, stream)
    if content_type is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            # Fall back to a plain JSON body when the server ignores `stream`.
            if stream and "text/event-stream" in resp.headers.get("Content-Type", ""):
                return read_event_stream(resp)
            return read_json_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(http_error_message(exc)) from exc


def post_json(url: str, api_key: str, payload: dict) -> dict | list[dict]:
    body = json.dumps(payload).encode("utf-8")
    return send_request(url, api_key, body, "application/json", bool(payload.get("stream")))


def wait_for_task(task_id: str, api_key: str) -> dict:
    if not isinstance(task_id, str) or not re.fullmatch(r"imgtask_[A-Za-z0-9_-]+", task_id):
        raise ValueError("invalid image task ID")
    url = build_url(f"{TASKS_ENDPOINT}/{task_id}")
    deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
    while (remaining := deadline - time.monotonic()) > 0:
        task = send_request(url, api_key, timeout=min(30, remaining))
        status = task.get("status")
        if status == "completed":
            result = task.get("result")
            if isinstance(result, dict) and result.get("data"):
                return result
            if task.get("image_url"):
                return {"data": [{"url": task["image_url"]}]}
            raise RuntimeError(f"image task {task_id} completed without an image")
        if status == "failed":
            error = task.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(f"image task {task_id} failed: {message or 'unknown error'}")
        if status != "processing":
            raise RuntimeError(f"image task {task_id} returned unexpected status {status!r}")
        time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"timed out waiting for image task {task_id}; use fetch to check it again")


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
    edit_layers: dict[str, tuple[Path, str]],
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
    for field_name, upload in edit_layers.items():
        add_file(field_name, *upload)

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
        "size": resolve_request_size(args),
        "quality": args.quality,
        "complexity": args.complexity,
        "n": args.n,
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "frame_count": args.frame_count,
        "frame_duration_ms": args.frame_duration_ms,
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
                "Accept": "image/*,*/*;q=0.8",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            destination.write_bytes(resp.read())
        return

    raise RuntimeError("image result did not include b64_json or url")


def save_items(items: list[dict], stem: str, default_format: str | None, label: str = "") -> list[Path]:
    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    multiple = len(items) > 1
    for idx, item in enumerate(items, start=1):
        image_format = item.get("output_format") or default_format
        suffix = OUTPUT_SUFFIXES.get(image_format)
        if suffix is None:
            url_suffix = Path(urlsplit(item.get("url") or "").path).suffix.lower()
            suffix = url_suffix if url_suffix in (*OUTPUT_SUFFIXES.values(), ".jpeg") else ".png"
        name = f"{stem}{label}_{idx:02d}" if multiple else f"{stem}{label}"
        destination = unique_path(out_dir, name, suffix)
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
    default_format = result.get("output_format") or requested_format
    return save_items(data, stem, default_format)


def finish_request(result: dict | list[dict], args: argparse.Namespace, api_key: str) -> list[Path]:
    if args.asynchronous:
        task_id = result.get("task_id") or result.get("id")
        if not task_id:
            raise RuntimeError("Vanilla API did not return an image task ID")
        print(f"task: {task_id}", file=sys.stderr, flush=True)
        result = wait_for_task(task_id, api_key)
    return save_result(result, args.output_format)


def fetch_task(args: argparse.Namespace) -> list[Path]:
    return save_result(wait_for_task(args.task_id, require_api_key()), None)


def generate_image(args: argparse.Namespace) -> list[Path]:
    validate_options(args)
    payload = common_options(args)
    if args.moderation is not None:
        payload["moderation"] = args.moderation
    if args.svg_model is not None or args.svg_reasoning_effort is not None:
        if args.output_format != "svg" or not (args.svg_model or "").strip():
            raise ValueError("SVG author options require --output-format svg and --svg-model")
        payload["svg"] = {"model": args.svg_model.strip()}
        if args.svg_reasoning_effort is not None:
            payload["svg"]["reasoning"] = {"effort": args.svg_reasoning_effort}
    api_key = require_api_key()
    endpoint = GENERATIONS_ENDPOINT + ("/async" if args.asynchronous else "")
    result = post_json(build_url(endpoint), api_key, payload)
    return finish_request(result, args, api_key)


def edit_image(args: argparse.Namespace) -> list[Path]:
    validate_options(args)
    if len(args.image) > MAX_EDIT_IMAGES:
        raise ValueError(f"at most {MAX_EDIT_IMAGES} input images are supported")
    images = [
        checked_input_file(path, "image", MAX_IMAGE_BYTES, ("image/png", "image/jpeg", "image/webp"))
        for path in args.image
    ]
    edit_layers = {}
    if args.mask is not None:
        edit_layers["mask"] = checked_input_file(args.mask, "mask", MAX_MASK_BYTES, ("image/png",))
    if args.markup is not None:
        edit_layers["markup"] = checked_input_file(args.markup, "markup", MAX_IMAGE_BYTES, ("image/png",))

    data = common_options(args)
    api_key = require_api_key()
    endpoint = EDITS_ENDPOINT + ("/async" if args.asynchronous else "")
    result = post_multipart(build_url(endpoint), api_key, data, images, edit_layers)
    return finish_request(result, args, api_key)


def print_size_table() -> None:
    table = {
        tier: {ratio: resolve_size(tier, ratio) for ratio in SUPPORTED_RATIOS}
        for tier in SUPPORTED_TIERS
    }
    print(json.dumps(table, ensure_ascii=False, indent=2))


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", choices=SUPPORTED_MODELS, default=DEFAULT_MODEL, help=f"image model (default: {DEFAULT_MODEL})")
    parser.add_argument("--size", required=True, help="1k, 2k, 3k, 4k, auto, or explicit WIDTHxHEIGHT for GIF")
    parser.add_argument("--ratio", type=normalize_ratio, help="aspect ratio; required with 1k, 2k, 3k, and 4k")
    parser.add_argument("--quality", choices=SUPPORTED_QUALITIES, help="API default: auto; xhigh and max require GPT Image 2.5 or 2.6")
    parser.add_argument("--complexity", choices=SUPPORTED_COMPLEXITIES, help="planning complexity for GPT Image 2.6")
    parser.add_argument("--n", type=int, help=f"number of images, 1-{MAX_N}")
    parser.add_argument("--background", choices=SUPPORTED_BACKGROUNDS, help="transparent requires png or webp output")
    parser.add_argument("--output-format", choices=SUPPORTED_OUTPUT_FORMATS, help="returned image format (API default: png)")
    parser.add_argument("--output-compression", type=int, help="0-100; only for jpeg or webp output")
    parser.add_argument("--frame-count", type=int, help="2-16 frames for GIF output")
    parser.add_argument("--frame-duration-ms", type=int, help="delay of at least 20 ms per GIF frame")
    parser.add_argument("--partial-images", type=int, help=f"stream the response and save 0-{MAX_PARTIAL_IMAGES} partial images")
    parser.add_argument("--async", dest="asynchronous", action="store_true", help="submit once and poll the Vanilla API task")
    parser.add_argument("--user", help="end-user identifier for abuse monitoring")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vanilla API image command line tool for text-to-image and image-to-image tasks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    table_parser = subparsers.add_parser("sizes", help="print the resolved size mapping table")
    table_parser.set_defaults(func=lambda args: print_size_table())

    fetch_parser = subparsers.add_parser("fetch", help="wait for and download an existing asynchronous image task")
    fetch_parser.add_argument("task_id", help="task ID printed when submitted; use the same API key")
    fetch_parser.set_defaults(func=fetch_task)

    generate_parser = subparsers.add_parser("generate", help="create an image from text")
    generate_parser.add_argument("--prompt", required=True, help="text prompt")
    add_common_arguments(generate_parser)
    generate_parser.add_argument("--moderation", choices=SUPPORTED_MODERATION, help="content moderation level")
    generate_parser.add_argument("--svg-model", help="SVG author model, if model selection is enabled")
    generate_parser.add_argument("--svg-reasoning-effort", choices=SVG_REASONING_EFFORTS, help="reasoning effort for --svg-model")
    generate_parser.set_defaults(func=generate_image, mask=None, markup=None)

    edit_parser = subparsers.add_parser("edit", help="create an image from one or more input images")
    edit_parser.add_argument("--prompt", required=True, help="edit prompt")
    edit_parser.add_argument("--image", action="append", required=True, help="input image path; repeat for up to 16 images")
    edit_layers = edit_parser.add_mutually_exclusive_group()
    edit_layers.add_argument("--mask", help="optional PNG mask applied to the first image")
    edit_layers.add_argument("--markup", help="transparent PNG annotation layer for GPT Image 2.5 or 2.6 edits")
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
