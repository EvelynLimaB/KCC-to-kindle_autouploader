# KCC-to-Kindle Autouploader

Automatically convert `.cbz` manga/comic archives to `.epub` with KCC and send them to your Kindle delivery address by email.

The workflow is designed to be simple:

1. Find CBZ files in a folder.
2. Convert each file to EPUB using KCC.
3. Batch the converted EPUBs so the total email attachment size stays under a safe limit.
4. Send the batches to your Kindle email address.
5. Delete files only after a successful delivery.

---

## Features

* Recursive scan for `.cbz` files inside a target folder.
* KCC conversion support in two modes:

  * local `kcc-c2e` command
  * Docker image mode via `docker://...`
* Webtoon detection based on page aspect ratio.
* Filename sanitization and chapter-based output naming.
* Batched email delivery to avoid exceeding attachment limits.
* Automatic retry with per-file fallback when a batch fails.
* Optional inclusion of any already-existing `.epub` files in the target folder.
* `--dry-run` mode for safe testing.
* Optional ZIP internals rewrite to force UTF-8 filenames.

---

## Requirements

* **Python** 3.10 or newer.
* **KCC** installed locally as `kcc-c2e`, or **Docker** if you want to use a KCC container image.
* A valid email account capable of sending mail through SMTP.
* Your Kindle Send-to-Kindle address.

The script expects these environment variables:

```bash
SMTP_SERVER
SMTP_PORT
EMAIL_USER
EMAIL_PASS
```

You can also tune the maximum allowed size for one message with:

```bash
MAX_EMAIL_SIZE
```

The default is **25 MB**.

---

## Installation

### 1) Clone the repository

```bash
git clone https://github.com/EvelynLimaB/KCC-to-kindle_autouploader.git
cd KCC-to-kindle_autouploader
```

### 2) Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 3) Install dependencies

This project currently uses only the Python standard library.

### 4) Install KCC

Choose one of the supported modes:

#### Local KCC binary

Install KCC so the `kcc-c2e` command is available in your shell.

#### Docker-based KCC

Use a Docker image that provides KCC. The script supports image references in this format:

```text
docker://ghcr.io/ciromattia/kcc:latest
```

---

## Configuration

Before running the script, export the SMTP credentials and server settings.

### Linux / macOS

```bash
export SMTP_SERVER="smtp.gmail.com"
export SMTP_PORT="587"
export EMAIL_USER="your-email@example.com"
export EMAIL_PASS="your-app-password"
```

### Optional: set a custom attachment limit

```bash
export MAX_EMAIL_SIZE=$((25 * 1024 * 1024))
```

If you need more or less headroom, adjust the value accordingly.

---

## Usage

### Basic run

```bash
python send_kindles.py \
  --folder "/path/to/Cbz_Manga" \
  --profile "K810" \
  --kcc-cmd "kcc-c2e" \
  --kindle-address "your-kindle@kindle.com"
```

### Docker-based KCC run

```bash
python send_kindles.py \
  --folder "/path/to/Cbz_Manga" \
  --profile "K810" \
  --kcc-cmd "docker://ghcr.io/ciromattia/kcc:latest" \
  --kindle-address "your-kindle@kindle.com"
```

### Dry run

Use this first to verify the workflow without sending mail or deleting files.

```bash
python send_kindles.py \
  --folder "/path/to/Cbz_Manga" \
  --profile "K810" \
  --kcc-cmd "docker://ghcr.io/ciromattia/kcc:latest" \
  --kindle-address "your-kindle@kindle.com" \
  --dry-run
```

### Force UTF-8 ZIP internals

If you have archives with filename encoding problems, add:

```bash
--force-zip-utf8
```

Example:

```bash
python send_kindles.py \
  --folder "/path/to/Cbz_Manga" \
  --profile "K810" \
  --kcc-cmd "kcc-c2e" \
  --kindle-address "your-kindle@kindle.com" \
  --force-zip-utf8
```

---

## What the script does

The script processes your library in a few stages:

### 1. Discover CBZ files

It scans the target folder recursively and looks for files ending in `.cbz`.

### 2. Normalize filenames

It sanitizes CBZ filenames so conversion and output filenames are more stable.

### 3. Detect webtoon layout

For CBZ files containing tall pages, the script enables KCC's webtoon mode automatically.

### 4. Convert to EPUB

Each CBZ is converted into an EPUB using KCC.

### 5. Batch email delivery

Converted EPUBs are grouped into batches to keep the email size within the configured limit.

### 6. Retry failed batches

If a batch fails, the script tries each file individually so one bad attachment does not block everything.

### 7. Cleanup

Source `.cbz` files are deleted after a successful conversion (unless `--dry-run` is enabled). Converted `.epub` files are deleted only after a batch is successfully sent. If sending fails, the files are kept on disk for retry.

---

## What the script does

The script processes your library in a few stages:

### 1. Discover CBZ files

It scans the target folder recursively and looks for files ending in `.cbz`.

### 2. Normalize filenames

It sanitizes CBZ filenames so conversion and output filenames are more stable.

### 3. Detect webtoon layout

For CBZ files containing tall pages, the script enables KCC's webtoon mode automatically.

### 4. Convert to EPUB

Each CBZ is converted into an EPUB using KCC.

### 5. Batch email delivery

Converted EPUBs are grouped into batches to keep the email size within the configured limit.

### 6. Retry failed batches

If a batch fails, the script tries each file individually so one bad attachment does not block everything.

### 7. Cleanup

Files are deleted only after they have been successfully sent.

---

## Suwayomi integration

This project works well with **Suwayomi** as a manga downloader.

Suwayomi is a manga reader server that supports automated chapter downloads, can store downloads in a configurable `downloadsPath`, and can save downloaded chapters as CBZ archives when `server.downloadAsCbz = true`. Suwayomi also supports a `Local Source` mode that can read CBZ, ZIP, EPUB, and folder-based chapters from its `local` directory. ([github.com](https://github.com/Suwayomi/Suwayomi-Server))

### Recommended workflow

1. Use Suwayomi to browse sources and download chapters automatically.
2. Configure Suwayomi to save downloads into a dedicated folder.
3. Point this script's `--folder` option at that same folder.
4. Let this script convert the downloaded CBZ files to EPUB and deliver them to Kindle.

This creates a simple two-step pipeline:

**Suwayomi downloads manga → this script converts and emails it to Kindle**.

### Why this fits the project

The current script recursively scans the target folder for `.cbz` files, converts them to EPUB, and sends them in batches. That means it can be used as the delivery stage after Suwayomi finishes downloading the chapters. It does not need a direct Suwayomi API connection to work well in this setup. fileciteturn0file1

### Practical notes

* If you want a clean handoff, keep Suwayomi downloads in a dedicated folder and point `--folder` there.
* Suwayomi's Local Source supports folders with images, ZIP/CBZ, RAR/CBR, and EPUB chapters, but the folder structure inside archives is ignored. For local manga folders, the outer directory structure matters.
* For best results, keep archive filenames and chapter names reasonably clean so the EPUB output names remain readable.

## Output naming

Converted files are named using the source folder name and chapter number when available.

Examples:

* `My Manga Ch001.epub`
* `My Manga Ch012.epub`
* `My Manga_some_file_name.epub`

If a file with the same name already exists, the script adds a numeric suffix.

---

## Logging

The project writes logs to both the console and a log file.

The included shell runner uses a log file path under the project folder, and the Python script also writes to a `logs/` directory beside the script.

Check the logs whenever conversion or delivery fails.

---

## Included helper scripts

The repository includes launcher scripts for easier execution.

* `run_send_kindles.sh` — Linux shell launcher.
* `run_send_kindles_dry.bat` — Windows batch launcher example.

These scripts are meant to reduce setup friction and provide a repeatable way to start the program. Edit the launcher files for your own machine before using them, especially paths, SMTP settings, and credentials.

---

## Troubleshooting

### The script exits immediately

Check that the SMTP environment variables are set:

* `SMTP_SERVER`
* `SMTP_PORT`
* `EMAIL_USER`
* `EMAIL_PASS`

### KCC conversion fails

Make sure one of the following is true:

* `kcc-c2e` is installed and available in your PATH, or
* your Docker setup can run the image specified in `--kcc-cmd`.

### EPUBs are not sent

Confirm that:

* your Kindle email address is correct,
* the email account is allowed to send to that address,
* the batch size is not too large,
* the logs do not show SMTP authentication errors.

### Files are not deleted

Files are only deleted after a successful send. If delivery fails, the script leaves them on disk so you can retry safely.

---

## Development notes

The codebase is intentionally small and easy to extend. Good next improvements include:

* adding automated tests,
* adding a sample `.env.example`,
* improving Windows documentation,
* adding a proper release workflow,
* documenting the expected KCC installation in more detail.

---

## Contributing

Contributions are welcome.

### Suggested workflow

1. Open an issue describing the problem or improvement.
2. Keep pull requests focused on a single change.
3. Test with `--dry-run` before changing defaults.
4. Include clear notes about any behavior changes.

### Good contribution ideas

* add tests for batch building and fallback sending,
* improve error messages,
* add documentation for common setup issues,
* refine filename normalization rules,
* improve platform-specific launch scripts.

---


## Acknowledgements

This project builds on KCC for comic-to-EPUB conversion and uses SMTP email delivery for Kindle transfer.
