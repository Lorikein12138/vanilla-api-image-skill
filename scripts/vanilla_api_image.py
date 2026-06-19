#!/usr/bin/env python3
import argparse
import base64
import json
import math
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


BASE_URL = "https://api.lorikein.cn"
GENERATIONS_ENDPOINT = "/v1/images/generations"
EDITS_ENDPOINT = "/v1/images/edits"
MODEL = "gpt-image-2"
QUALITY = "auto"
TIMEOUT_SECONDS = 600

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


def resolve_size(tier: str, ratio: str) -> str:
    if tier not in SUPPORTED_TIERS:
        choices = ", ".join(SUPPORTED_TIERS)
        raise ValueError(f"unsupported size tier {tier!r}; choose one of: {choices}")

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


def build_url(endpoint: str) -> str:
    return f"{BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"


def output_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "outputs"


def timestamp_name(index: int | None = None, suffix: str = ".png") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if index is None:
        return f"{stamp}{suffix}"
    return f"{stamp}_{index:02d}{suffix}"


def require_api_key() -> str:
    api_key = os.environ.get("VANILLA_API_IMAGE")
    if not api_key:
        raise RuntimeError("missing environment variable VANILLA_API_IMAGE")
    return api_key


def request_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "vanilla-api-image-skill/1.0",
    }


def read_json_response(resp) -> dict:
    body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON response. Raw body: {body}") from exc


def http_error_message(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return f"request failed with HTTP {error.code}: {body}"


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
                "User-Agent": "vanilla-api-image-skill/1.0",
            },
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            destination.write_bytes(resp.read())
        return

    raise RuntimeError("image result did not include b64_json or url")


def save_images(payload: dict) -> list[Path]:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("image result did not include a non-empty data list")

    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    multiple = len(data) > 1
    for idx, item in enumerate(data, start=1):
        name = timestamp_name(idx if multiple else None)
        destination = out_dir / name
        decode_or_download_image(item, destination)
        saved_paths.append(destination)

    return saved_paths


def post_json(url: str, api_key: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    headers = request_headers(api_key) | {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return read_json_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(http_error_message(exc)) from exc


def post_multipart(url: str, api_key: str, data: dict, image_paths: list[Path], mask_path: Path | None) -> dict:
    boundary = f"----vanilla-api-image-skill-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    chunks = []

    def add_field(name: str, value: str) -> None:
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    def add_file(field_name: str, file_path: Path) -> None:
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{file_path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(file_path.read_bytes())
        chunks.append(b"\r\n")

    for key, value in data.items():
        add_field(key, value)
    for image_path in image_paths:
        add_file("image", image_path)
    if mask_path:
        add_file("mask", mask_path)

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    headers = request_headers(api_key) | {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            return read_json_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(http_error_message(exc)) from exc


def generate_image(args: argparse.Namespace) -> list[Path]:
    api_key = require_api_key()
    size = resolve_size(args.size, args.ratio)
    payload = {
        "model": MODEL,
        "prompt": args.prompt,
        "size": size,
        "quality": QUALITY,
    }
    result = post_json(build_url(GENERATIONS_ENDPOINT), api_key, payload)
    return save_images(result)


def edit_image(args: argparse.Namespace) -> list[Path]:
    api_key = require_api_key()
    size = resolve_size(args.size, args.ratio)
    image_paths = [Path(path) for path in args.image]
    missing = [str(path) for path in image_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"image file not found: {', '.join(missing)}")

    mask_path = Path(args.mask) if args.mask else None
    if mask_path and not mask_path.is_file():
        raise FileNotFoundError(f"mask file not found: {mask_path}")

    data = {
        "model": MODEL,
        "prompt": args.prompt,
        "size": size,
        "quality": QUALITY,
    }
    result = post_multipart(build_url(EDITS_ENDPOINT), api_key, data, image_paths, mask_path)
    return save_images(result)


def print_size_table() -> None:
    table = {
        tier: {ratio: resolve_size(tier, ratio) for ratio in SUPPORTED_RATIOS}
        for tier in SUPPORTED_TIERS
    }
    print(json.dumps(table, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vanilla API image command line tool for text-to-image and image-to-image tasks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    table_parser = subparsers.add_parser("sizes", help="print the resolved size mapping table")
    table_parser.set_defaults(func=lambda args: print_size_table())

    generate_parser = subparsers.add_parser("generate", help="create an image from text")
    generate_parser.add_argument("--prompt", required=True, help="text prompt")
    generate_parser.add_argument("--size", choices=SUPPORTED_TIERS.keys(), required=True, help="size tier")
    generate_parser.add_argument("--ratio", type=normalize_ratio, required=True, help="aspect ratio")
    generate_parser.set_defaults(func=generate_image)

    edit_parser = subparsers.add_parser("edit", help="create an image from one or more input images")
    edit_parser.add_argument("--prompt", required=True, help="edit prompt")
    edit_parser.add_argument("--image", action="append", required=True, help="input image path; repeat for multiple images")
    edit_parser.add_argument("--mask", help="optional mask image path")
    edit_parser.add_argument("--size", choices=SUPPORTED_TIERS.keys(), required=True, help="size tier")
    edit_parser.add_argument("--ratio", type=normalize_ratio, required=True, help="aspect ratio")
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
