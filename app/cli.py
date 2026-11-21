"""Command line interface: `uv run python -m app.cli <command>`.

Commands of the social media module start with `social-`; all others belong to the inbox.
"""

import sys

from app.inbox.cli import main as inbox_main
from app.social.cli import COMMANDS as SOCIAL_COMMANDS
from app.social.cli import main as social_main

IMAGE_MODELS_COMMAND = "image-models"


def image_models() -> int:
    """Print the image models of the OpenAI account (for `IMAGE_MODEL`)."""
    from app.core.images.base import ImageError  # noqa: PLC0415 - only for this command
    from app.core.images.openai_provider import list_image_models  # noqa: PLC0415
    from app.core.provider_errors import ProviderConfigError  # noqa: PLC0415

    try:
        models = list_image_models()
    except (ImageError, ProviderConfigError) as error:
        print(error, file=sys.stderr)
        return 1
    print("\n".join(models) if models else "Keine Bildmodelle gefunden.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == IMAGE_MODELS_COMMAND:
        return image_models()
    if args and args[0] in SOCIAL_COMMANDS:
        return social_main(args)
    return inbox_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
