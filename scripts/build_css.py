"""Compile the Tailwind CSS that the app serves from `app/core/static/css/app.css`.

Usage: `uv run python scripts/build_css.py` (needs Node.js; run `npm install` once).
Run it after changing templates, `app/core/static/src/app.css` or `tailwind.config.js`.

The first line of the output records a hash of all sources. A test compares it with the
current sources, so a forgotten rebuild is caught in CI without needing Node.js there.
"""

import hashlib
import subprocess  # noqa: S404 - fixed command, no user input
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "app" / "core" / "static" / "src" / "app.css"
OUTPUT = ROOT / "app" / "core" / "static" / "css" / "app.css"
CONFIG = ROOT / "tailwind.config.js"
STAMP_PREFIX = "/* sources-sha256: "


def source_files() -> list[Path]:
    """All files Tailwind reads: the CSS source, the config and the templates."""
    return [SOURCE, CONFIG, *sorted(ROOT.glob("app/**/templates/**/*.html"))]


def sources_hash() -> str:
    digest = hashlib.sha256()
    for path in source_files():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def recorded_hash(css: str) -> str | None:
    first = css.split("\n", 1)[0]
    if not first.startswith(STAMP_PREFIX):
        return None
    return first.removeprefix(STAMP_PREFIX).removesuffix(" */").strip()


def main() -> int:
    command = [
        "npx",
        "--no-install",
        "tailwindcss",
        "-c",
        str(CONFIG),
        "-i",
        str(SOURCE),
        "-o",
        str(OUTPUT),
        "--minify",
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)  # noqa: S603
    if result.returncode != 0:
        return result.returncode
    css = OUTPUT.read_text(encoding="utf-8")
    OUTPUT.write_text(f"{STAMP_PREFIX}{sources_hash()} */\n{css}", encoding="utf-8")
    print(f"{OUTPUT.relative_to(ROOT)} geschrieben ({len(css) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
