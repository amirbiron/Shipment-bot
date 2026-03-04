#!/bin/bash
set -euo pipefail

# רק בסביבת web מרוחקת
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# התקנת תלויות פיתוח (כולל בדיקות ו-linters)
pip install -r requirements-dev.txt --quiet

# תיקון cryptography — הגרסה המותקנת מ-debian שבורה
pip install --ignore-installed cryptography --quiet

# הגדרת PYTHONPATH כדי ש-imports יעבדו
echo 'export PYTHONPATH="."' >> "$CLAUDE_ENV_FILE"
