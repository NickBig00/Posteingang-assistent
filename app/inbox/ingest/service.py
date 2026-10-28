"""Storing incoming files and creating inbox items."""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path, PurePath

from sqlmodel import Session, select

from app.core.config import PROJECT_ROOT
from app.core.storage import storage_dir
from app.inbox.ingest.normalize import SUPPORTED_SUFFIXES, file_suffix, normalize
from app.inbox.models import InboxItem, ItemSource, ItemStatus
from app.inbox.seed import seed_members
from app.inbox.status import transition_status

logger = logging.getLogger(__name__)

SAMPLES_ROOT = PROJECT_ROOT / "samples"
DEFAULT_SAMPLES_DIR = SAMPLES_ROOT / "fitness"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def samples_dir_for(profile_key: str) -> Path:
    """Sample data of a profile: `samples/<profile>/`."""
    return SAMPLES_ROOT / profile_key


@dataclass
class IngestResult:
    item: InboxItem
    created: bool


@dataclass
class ImportSummary:
    imported: int
    skipped: int
    members_seeded: int


def ingest_file(
    session: Session,
    filename: str,
    content: bytes,
    source: ItemSource,
    raw_dir: Path,
) -> IngestResult:
    """Store `content`, extract its text and create an inbox item (skips duplicates)."""
    safe_name = PurePath(filename).name
    suffix = file_suffix(safe_name)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("file too large")
    document = normalize(safe_name, content)

    content_hash = hashlib.sha256(content).hexdigest()
    existing = session.exec(select(InboxItem).where(InboxItem.content_hash == content_hash)).first()
    if existing is not None:
        return IngestResult(item=existing, created=False)

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{content_hash}{suffix}"
    raw_path.write_bytes(content)

    item = InboxItem(
        source=source,
        raw_content_ref=str(raw_path),
        filename=safe_name,
        content_hash=content_hash,
        text=document.text or None,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    if not document.text and not document.needs_ocr:
        # Nothing to process automatically (e.g. empty e-mail): a human has to look.
        # Images and PDFs without text layer stay `new`: the pipeline runs OCR on them.
        transition_status(session, item, ItemStatus.NEEDS_MANUAL_REVIEW)
    logger.info("inbox item %s created (source=%s, type=%s)", item.id, source, suffix)
    return IngestResult(item=item, created=True)


def import_samples(
    session: Session, samples_dir: Path = DEFAULT_SAMPLES_DIR, raw_dir: Path | None = None
) -> ImportSummary:
    """Import all sample files and seed the fictitious member data."""
    raw_dir = raw_dir or storage_dir()
    imported = skipped = 0
    for path in sorted(samples_dir.iterdir()):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        result = ingest_file(session, path.name, path.read_bytes(), ItemSource.SAMPLE, raw_dir)
        if result.created:
            imported += 1
        else:
            skipped += 1
    members_file = samples_dir / "members.json"
    members_seeded = seed_members(session, members_file) if members_file.exists() else 0
    return ImportSummary(imported=imported, skipped=skipped, members_seeded=members_seeded)
