#!/usr/bin/env python3
"""
send_kindles_final_batch.py

Modified version: batch attachments and send at the end (splits into multiple emails
when the total attachment size would exceed the configured per-message limit).

Behaviour changes (high level):
 - Converted EPUBs are collected in memory (list of paths) and only sent after all
   conversions finish.
 - Files are grouped into email batches so the estimated encoded size does not
   exceed the per-message limit (default 25 MB). The per-message limit can be
   overridden with environment variable MAX_EMAIL_SIZE (bytes).
 - Files are deleted only after they are successfully sent in an email batch.
 - Dry-run mode still converts files but will NOT send or delete them.

Note: base64 increases attachment size by ~4/3; this script uses a conservative
allowed-raw-size = MAX_EMAIL_SIZE * 0.75 when building batches.

This modified copy adds two behaviors requested:
 1) Any existing .epub files under the target folder (args.folder) are included
    for sending, so "unsent" EPUBs left on disk from previous runs will be
    delivered.
 2) If a batch fails to send, the script attempts a per-file fallback: it will
    try to send each file in the failed batch as a single-file email. Files
    that succeed in the fallback are deleted; files that still fail are left on
    disk for manual inspection/retry.

"""

import os
import sys
import logging
import shutil
import subprocess
import smtplib
import traceback
import argparse
import zipfile
import struct
import re
import unicodedata
import tempfile
from pathlib import Path
from email.message import EmailMessage
from email.utils import encode_rfc2231

# ---------- Logging ----------
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "send_kindles_final.log"
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

PKG_WARN_RE = re.compile(r'pkg_resources is deprecated as an API', re.IGNORECASE)


def _filter_stderr_text(text: str) -> str:
    if not text:
        return ""
    return '\n'.join(line for line in text.splitlines() if not PKG_WARN_RE.search(line))

# Image helpers (unchanged)
def _jpeg_size(fp):
    fp.read(2)
    while True:
        marker_bytes = fp.read(2)
        if len(marker_bytes) < 2:
            raise ValueError("Invalid JPEG: truncated")
        marker, = struct.unpack(">H", marker_bytes)
        length_bytes = fp.read(2)
        if len(length_bytes) < 2:
            raise ValueError("Invalid JPEG: truncated")
        length, = struct.unpack(">H", length_bytes)
        if 0xFFC0 <= marker <= 0xFFC3:
            fp.read(1)
            h_w = fp.read(4)
            if len(h_w) < 4:
                raise ValueError("Invalid JPEG: truncated")
            height, width = struct.unpack(">HH", h_w)
            return width, height
        fp.read(length - 2)


def _png_size(fp):
    sig = fp.read(8)
    if len(sig) < 8:
        raise ValueError("Invalid PNG: truncated")
    length_bytes = fp.read(4)
    if len(length_bytes) < 4:
        raise ValueError("Invalid PNG: truncated")
    length, = struct.unpack(">I", length_bytes)
    chunk_type = fp.read(4)
    if chunk_type != b'IHDR':
        raise ValueError("Not a valid PNG (missing IHDR)")
    ihdr = fp.read(8)
    if len(ihdr) < 8:
        raise ValueError("Invalid PNG: truncated IHDR")
    width, height = struct.unpack(">II", ihdr)
    return width, height


def get_image_size_from_bytes(data: bytes):
    from io import BytesIO
    sig = data[:8]
    fp = BytesIO(data)
    if sig.startswith(b'\xff\xd8'):
        return _jpeg_size(fp)
    elif sig == b'\x89PNG\r\n\x1a\n':
        return _png_size(fp)
    else:
        raise ValueError("Unsupported image format")


def is_webtoon(cbz_path: Path, ratio_threshold: float = 2.0) -> bool:
    with zipfile.ZipFile(cbz_path, 'r') as z:
        for name in sorted(z.namelist()):
            if not name.lower().endswith(('.jpg', '.jpeg', '.png')):
                continue
            try:
                data = z.read(name)
                w, h = get_image_size_from_bytes(data)
                ratio = h / w
                logger.debug(f"Page {name!r}: width={w}, height={h}, ratio={ratio:.2f}")
                if ratio > ratio_threshold:
                    logger.debug(f"  -> {name!r} exceeds threshold {ratio_threshold} (ratio={ratio:.2f}); marking as webtoon")
                    return True
            except Exception as exc:
                logger.warning(f"Skipping {name!r}: {exc}")
    logger.debug("No pages exceeded the webtoon ratio threshold")
    return False

# Filename helpers (unchanged)
CHAPTER_PATTERNS = [
    r'(?i)(?:ch|chapter|cap|capitulo)[\._\s-]*#?(\d{1,4})',
    r'(?i)ch[\._\s-]*(\d{1,4})',
    r'(?i)cap(?:itulo)?[\._\s-]*(\d{1,4})',
    r'(\d{1,4})'
]
SAFE_NAME_RE = re.compile(r'[^A-Za-z0-9\s\-\._]')


def extract_chapter_number_from_name(name: str) -> str:
    for pat in CHAPTER_PATTERNS:
        m = re.search(pat, name)
        if m:
            try:
                return str(int(m.group(1)))
            except Exception:
                continue
    return ""


def sanitize_manga_folder_name(folder: Path) -> str:
    raw = folder.name
    raw = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw)
    raw = raw.strip()
    raw = unicodedata.normalize('NFC', raw)
    raw = SAFE_NAME_RE.sub('', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()
    safe = raw.replace(' ', '_')
    if len(safe) > 120:
        safe = safe[:120]
    return safe or 'manga'


def safe_filename(path: Path) -> Path:
    name = unicodedata.normalize('NFC', path.name)
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', name)
    stem, suffix = Path(name).stem, Path(name).suffix
    if len(stem) > 120:
        stem = stem[:120]
    return path.with_name(stem + suffix)

# Rezip internals forcing UTF-8 (unchanged)
def rezip_force_utf8(src_path: Path, out_path: Path) -> Path:
    with zipfile.ZipFile(src_path, 'r') as zin:
        with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for zi in zin.infolist():
                try:
                    data = zin.read(zi.filename)
                except KeyError:
                    logger.warning(f"Failed to read {zi.filename!r} from zip; skipping")
                    continue
                new_zi = zipfile.ZipInfo(zi.filename)
                new_zi.date_time = zi.date_time
                new_zi.compress_type = zi.compress_type if hasattr(zi, 'compress_type') else zipfile.ZIP_DEFLATED
                new_zi.flag_bits = (zi.flag_bits if hasattr(zi, 'flag_bits') else 0) | 0x800
                if hasattr(zi, 'external_attr'):
                    new_zi.external_attr = zi.external_attr
                zout.writestr(new_zi, data)
    return out_path

    
# Conversion function (unchanged except return signature)
def convert_cbz(cbz_path: Path, profile: str, kcc_cmd: str, out_dir: Path | None = None) -> Path:
    """
    Convert cbz_path to epub. kcc_cmd may be:
      - a local command like "kcc-c2e" (existing behavior), or
      - a docker-image spec prefixed with "docker://", e.g. "docker://ghcr.io/ciromattia/kcc:latest".
        In docker mode we call docker run mounting the temp workdir and executing the container's converter.
    """
    out_dir = (cbz_path.parent if out_dir is None else Path(out_dir))
    logger.info(f"Starting conversion: {cbz_path}")

    webtoon_flag = is_webtoon(cbz_path)
    logger.debug(f"Webtoon mode: {webtoon_flag}")

    safe_cbz = safe_filename(cbz_path)
    safe_stem = Path(safe_cbz).stem

    with tempfile.TemporaryDirectory(prefix='kcc_work_') as tmpdir:
        tmpdir_p = Path(tmpdir).resolve()
        tmp_cbz = tmpdir_p / safe_cbz.name
        shutil.copy2(cbz_path, tmp_cbz)

        # If the user supplied docker://image then run conversion inside that container
        if isinstance(kcc_cmd, str) and kcc_cmd.startswith('docker://'):
            image = kcc_cmd.split('docker://', 1)[1]

            # Use /work inside container to avoid overwriting image files.
            # Use the c2e wrapper that exists in this image (/usr/local/bin/c2e).
            cbz_basename = tmp_cbz.name
            docker_cmd = [
                'docker', 'run', '--rm',
                '-v', f'{str(tmpdir_p)}:/work',
                '--entrypoint', '/usr/local/bin/c2e',
                image,
                *(['-w'] if webtoon_flag else []),
                '-p', profile,
                '-f', 'EPUB',
                '-q', '-u',
                '-o', '/work',
                f'/work/{cbz_basename}'
            ]

            cmd = docker_cmd
        else:
            # legacy local binary invocation
            cmd = [
                kcc_cmd,
                '-p', profile,
                '-f', 'EPUB',
                *(['-w'] if webtoon_flag else []),
                '-q', '-u',
                '-o', str(tmpdir_p), str(tmp_cbz)
            ]

        logger.debug('Running: %s', ' '.join(cmd))

        env = os.environ.copy()
        env['PYTHONWARNINGS'] = 'ignore:pkg_resources is deprecated as an API:UserWarning'
        env['PYTHONUTF8'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        env.setdefault('LANG', 'C.UTF-8')

        try:
            # use check=True so exceptions are raised on non-zero exit
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)
            filtered = _filter_stderr_text(result.stderr)
            logger.debug('kcc stdout: %s', result.stdout)
            if filtered:
                logger.debug('kcc stderr (filtered): %s', filtered)
            else:
                logger.debug('kcc stderr: <filtered>')
        except subprocess.CalledProcessError as exc:
            filtered = _filter_stderr_text(exc.stderr or '')
            logger.error('kcc failed: %s\nstdout:%s\nstderr:%s', exc, exc.stdout, filtered)
            raise

        epubs = list(tmpdir_p.glob('*.epub'))
        if not epubs:
            raise FileNotFoundError(f'No EPUB produced for {cbz_path.name}')
        tmp_epub = max(epubs, key=lambda p: p.stat().st_mtime)

        # Build final filename using the parent folder name (manga name) and zero-padded 3-digit chapter
        manga_name = sanitize_manga_folder_name(cbz_path.parent)
        chapter = extract_chapter_number_from_name(cbz_path.stem)
        if chapter:
            try:
                chnum = int(chapter)
                base = f"{manga_name}_Ch{chnum:03d}"
            except Exception:
                base = f"{manga_name}_Ch{chapter}"
        else:
            base = f"{manga_name}_{safe_stem}"

        final_name = base + ".epub"
        dest = out_dir / final_name
        counter = 1
        while dest.exists():
            dest = out_dir / f"{base}_{counter}.epub"
            counter += 1

        shutil.move(str(tmp_epub), str(dest))
        logger.info(f"Moved EPUB to: {dest}")
        return dest

# Email sending helpers (batch-capable)
def _attach_file_to_msg(msg: EmailMessage, file_path: Path):
    """Attach a single file to the EmailMessage and set a correct UTF-8 filename

    IMPORTANT: set headers only on the newly added attachment. Previous code iterated
    over all attachments and overwrote filenames of earlier attachments with the
    current file's name — causing every attachment in a batch to appear with the
    same filename (the last one added).
    """
    with file_path.open('rb') as fh:
        data = fh.read()

    # Add the attachment
    msg.add_attachment(data, maintype='application', subtype='epub+zip', filename=file_path.name)

    # Retrieve only the newly added attachment part and set its Content-Disposition
    try:
        last_part = list(msg.iter_attachments())[-1]
    except Exception:
        last_part = None

    if last_part is not None:
        try:
            last_part.set_param('filename', file_path.name, header='Content-Disposition', charset='utf-8')
        except Exception:
            try:
                enc = encode_rfc2231(file_path.name, 'utf-8')
                last_part.replace_header('Content-Disposition', f'attachment; filename*={enc}')
            except Exception:
                try:
                    last_part.replace_header('Content-Disposition', f'attachment; filename="{file_path.name}"')
                except Exception:
                    logger.exception('Failed to set Content-Disposition filename for %s', file_path.name)


def send_email_batch(file_paths, smtp_server: str, smtp_port: int, email_user: str, email_pass: str, kindle_address: str, batch_index: int, dry_run: bool = False):
    """Send a single email containing multiple file_paths as attachments."""
    if not file_paths:
        return True

    subj = f'Automated Kindle Delivery: batch {batch_index} ({len(file_paths)} files)'
    logger.info('Preparing email %d with %d attachments (total raw bytes=%d)', batch_index, len(file_paths), sum(p.stat().st_size for p in file_paths))

    msg = EmailMessage()
    msg['Subject'] = subj
    msg['From'] = email_user
    msg['To'] = kindle_address

    # Attach each file
    for p in file_paths:
        _attach_file_to_msg(msg, p)

    if dry_run:
        logger.info('[dry-run] Would send email %d with files: %s', batch_index, ', '.join(p.name for p in file_paths))
        return True

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)
        logger.info('Email sent (batch %d): %s', batch_index, ', '.join(p.name for p in file_paths))
        return True
    except Exception:
        logger.exception('Failed to send email batch %d', batch_index)
        return False

# Utility: build batches based on MAX_EMAIL_SIZE environment variable
def build_batches(paths, max_email_size_bytes: int):
    """Return list of batches (each batch is a list of Paths).

    We conservatively account for base64 expansion by using allowed_raw = max_email_size_bytes * 0.75
    so that encoded attachments are unlikely to exceed the provider limit.
    """
    if not paths:
        return []

    allowed_raw = int(max_email_size_bytes * 0.75)
    batches = []
    cur = []
    cur_total = 0

    for p in paths:
        try:
            sz = p.stat().st_size
        except Exception:
            sz = 0
        # If a single file alone is bigger than allowed_raw, put it in its own batch
        if sz > allowed_raw and cur:
            # finish current batch
            batches.append(cur)
            cur = []
            cur_total = 0

        if sz > allowed_raw:
            # single huge file — create its own batch (may still exceed provider raw limit after encoding)
            logger.warning('File %s raw size %d exceeds conservative allowed_raw %d; sending in its own email (may fail)', p.name, sz, allowed_raw)
            batches.append([p])
            continue

        # If adding this file would exceed allowed_raw, finish current batch and start a new one
        if cur_total + sz > allowed_raw:
            batches.append(cur)
            cur = [p]
            cur_total = sz
        else:
            cur.append(p)
            cur_total += sz

    if cur:
        batches.append(cur)

    return batches

# Main
def main():
    parser = argparse.ArgumentParser(description='Convert CBZ to EPUB and send to Kindle (batched)')
    parser.add_argument('--folder', type=Path, default=Path(r'/home/ev/Documents/Cbz Manga'))
    parser.add_argument('--profile', default='K810')
    parser.add_argument('--kcc-cmd', default='kcc-c2e')
    parser.add_argument('--kindle-address', default='evema_senddasilva@kindle.com')
    parser.add_argument('--force-zip-utf8', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    email_user = os.environ.get('EMAIL_USER')
    email_pass = os.environ.get('EMAIL_PASS')
    if not all([smtp_server, email_user, email_pass]):
        logger.critical('Missing SMTP_SERVER, EMAIL_USER or EMAIL_PASS in environment')
        sys.exit(1)

    # Size limit for a single message in bytes (default 25 MB for Gmail)
    MAX_EMAIL_SIZE = int(os.environ.get('MAX_EMAIL_SIZE', str(25 * 1024 * 1024)))

    logger.info('=== Start ===')

    # Preflight: discover CBZ files in a case-insensitive and robust way so we
    # don't accidentally miss files due to extension case or stray characters.
    all_files = list(args.folder.rglob('*'))
    cbz_candidates = [p for p in all_files if p.is_file() and p.suffix.lower() == '.cbz']
    logger.info('Discovered %d CBZ file(s) under %s', len(cbz_candidates), args.folder)
    for p in sorted(cbz_candidates):
        logger.debug('Candidate CBZ: %s', p)

    ready_files = []

    for cbz in sorted(cbz_candidates):
        logger.debug('Found: %s', cbz)
        try:
            # remember the source path we started with; if we rename the file we
            # will update `source_cbz` to point to the new path so cleanup is
            # deterministic.
            source_cbz = cbz

            safe = safe_filename(cbz)
            if safe != cbz:
                if args.dry_run:
                    logger.info('Sanitizing filename (dry-run, not renaming): %s -> %s', cbz.name, safe.name)
                else:
                    logger.info('Sanitizing filename: %s -> %s', cbz.name, safe.name)
                    new_cbz = cbz.rename(safe)
                    cbz = new_cbz
                    source_cbz = new_cbz

            to_process = cbz
            tmp_created = None
            if args.force_zip_utf8:
                fd, tmp_path = tempfile.mkstemp(suffix='.cbz')
                os.close(fd)
                tmpf = Path(tmp_path)
                logger.info('Rewriting CBZ to force UTF-8 internals: %s', tmpf.name)
                rezip_force_utf8(cbz, tmpf)
                to_process = tmpf
                tmp_created = tmpf

            logger.debug('About to convert: %s (dry-run=%s)', to_process, args.dry_run)
            epub_file = convert_cbz(to_process, args.profile, args.kcc_cmd)

            # Collect for batch sending later (do not delete yet)
            ready_files.append(epub_file)

            # cleanup the temporary created cbz immediately
            if tmp_created and tmp_created.exists():
                try:
                    tmp_created.unlink()
                except Exception:
                    logger.warning('Failed to remove temporary cbz %s', tmp_created)

        except Exception:
            logger.exception('Error processing %s', cbz.name)
        else:
            try:
                # Only delete the source file if not in dry-run. Use the
                # deterministic `source_cbz` which points to either the original
                # or the renamed path (if we renamed above).
                if not args.dry_run:
                    if source_cbz.exists():
                        source_cbz.unlink()
                logger.info('Cleaned source CBZ: %s', source_cbz.name)
            except Exception:
                logger.warning('Failed to clean up files for %s', source_cbz.name)

    # === NEW: include any existing EPUBs under args.folder (unsent files) ===
    try:
        existing_epubs = [p for p in args.folder.rglob('*.epub') if p.is_file()]
        # Avoid duplicates: compare resolved paths
        ready_resolved = {p.resolve() for p in ready_files}
        for p in sorted(existing_epubs):
            if p.resolve() not in ready_resolved:
                logger.info('Including existing EPUB for sending: %s', p)
                ready_files.append(p)
    except Exception:
        logger.exception('Failed to discover existing EPUBs under %s', args.folder)

    # Now send all ready files in batches
    if ready_files:
        batches = build_batches(ready_files, MAX_EMAIL_SIZE)
        logger.info('Built %d email batch(es) for %d file(s)', len(batches), len(ready_files))

        batch_index = 1
        for batch in batches:
            ok = send_email_batch(batch, smtp_server, smtp_port, email_user, email_pass, args.kindle_address, batch_index, dry_run=args.dry_run)
            if ok:
                # remove files that were successfully sent (unless dry-run)
                if not args.dry_run:
                    for p in batch:
                        try:
                            if p.exists():
                                p.unlink()
                                logger.info('Deleted sent file: %s', p.name)
                        except Exception:
                            logger.warning('Failed to delete sent file: %s', p.name)
            else:
                logger.error('Batch %d failed to send; attempting per-file fallback', batch_index)
                # Try sending each file individually so problematic attachments
                # don't block the whole batch forever.
                for i, p in enumerate(batch, start=1):
                    logger.info('Attempting single-file send for %s (batch %d, item %d)', p.name, batch_index, i)
                    single_ok = send_email_batch([p], smtp_server, smtp_port, email_user, email_pass, args.kindle_address, batch_index * 100 + i, dry_run=args.dry_run)
                    if single_ok:
                        if not args.dry_run:
                            try:
                                if p.exists():
                                    p.unlink()
                                    logger.info('Deleted sent file after fallback: %s', p.name)
                            except Exception:
                                logger.warning('Failed to delete sent file after fallback: %s', p.name)
                    else:
                        logger.error('Failed to send %s individually; leaving on disk for retry', p.name)
            batch_index += 1

    logger.info('=== End ===')


if __name__ == '__main__':
    main()
