#!/bin/bash
# run_send_kindles.sh
# Runner for send_kindles.py using Docker image for kcc-c2e.
# Edit only the TOP section below for your environment.

# --------- CONFIG (edit) ----------
PYTHON="/usr/bin/python3"                 # or /home/ev/.venv/bin/python
SCRIPT="/home/ev/Documents/kindle_send/send_kindles.py"
FOLDER="/home/ev/Documents/Cbz_Manga"
PROFILE="K810"

# Use a docker image that contains kcc and (optionally) kindlegen.
# Format required by the script: docker://<image>
KCC_CMD="docker://ghcr.io/ciromattia/kcc:latest"

# Logging (change if you prefer)
LOG="/home/ev/Documents/kindle_send/send_kindles.log"
mkdir -p "$(dirname "$LOG")"

export SMTP_SERVER=smtp.gmail.com
export SMTP_PORT=587
export EMAIL_USER=
export EMAIL_PASS=

# Force tempfile into home so docker/flatpak sandbox path issues are avoided.
export TMPDIR="$HOME/.cache/kcc_tmp"
mkdir -p "$TMPDIR"
chmod 700 "$TMPDIR"

# If you have a project venv at /home/ev/Documents/kindle_send/.venv use it automatically:
if [ -x "$(dirname "$SCRIPT")/.venv/bin/python" ]; then
  PYTHON="$(dirname "$SCRIPT")/.venv/bin/python"
fi

# --------- RUN ----------
"$PYTHON" "$SCRIPT" \
  --folder "$FOLDER" \
  --profile "$PROFILE" \
  --kcc-cmd "$KCC_CMD" \
  >>"$LOG" 2>&1

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
  echo "send_kindles.py exited with $EXIT_CODE; see $LOG" >&2
fi
exit $EXIT_CODE
