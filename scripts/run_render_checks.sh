#!/usr/bin/env bash
set -euo pipefail

# בדיקות אוטומטיות לריצה ב-Render Shell:
# - Unit tests (pytest)
# - Smoke tests מול השרת (health + webhooks) אם השרת זמין
#
# שימוש:
#   bash scripts/run_render_checks.sh
#
# משתני סביבה אופציונליים:
#   PORT: הפורט שהשירות מאזין עליו (ברנדר לרוב 10000)
#   BASE_URL: URL מלא (עוקף PORT). דוגמה: http://127.0.0.1:10000
#   RUN_UNIT_TESTS: 1/0 (ברירת מחדל 1)
#   RUN_SMOKE_TESTS: 1/0 (ברירת מחדל 1)
#   PIP_INSTALL_TEST_DEPS: 1/0 (ברירת מחדל 1 אם pytest חסר)

PORT="${PORT:-8000}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
RUN_UNIT_TESTS="${RUN_UNIT_TESTS:-1}"
RUN_SMOKE_TESTS="${RUN_SMOKE_TESTS:-1}"

echo "== Shipment Bot checks =="
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi
echo "Python: $(${PYTHON_BIN} --version)"
echo "Base URL: ${BASE_URL}"

if [[ "${RUN_UNIT_TESTS}" == "1" ]]; then
  if ! "${PYTHON_BIN}" -c "import pytest" >/dev/null 2>&1; then
    if [[ "${PIP_INSTALL_TEST_DEPS:-1}" != "1" ]]; then
      echo "pytest לא מותקן, ו-PIP_INSTALL_TEST_DEPS=0. לא ניתן להריץ unit tests."
      exit 2
    fi
    echo "pytest לא מותקן - מתקין תלות מינימלית לבדיקות..."
    # ב-Render Shell בדרך כלל יש virtualenv פעיל, ושם pip --user נכשל.
    IN_VENV="$("${PYTHON_BIN}" -c 'import sys; print(int(sys.prefix != sys.base_prefix))')"
    if [[ "${IN_VENV}" == "1" ]]; then
      "${PYTHON_BIN}" -m pip install -q \
        pytest \
        pytest-asyncio \
        pytest-cov \
        pytest-mock \
        aiosqlite
    else
      "${PYTHON_BIN}" -m pip install --user -q \
        pytest \
        pytest-asyncio \
        pytest-cov \
        pytest-mock \
        aiosqlite
    fi
  fi

  echo "מריץ unit tests..."
  "${PYTHON_BIN}" -m pytest -q
fi

if [[ "${RUN_SMOKE_TESTS}" == "1" ]]; then
  echo "מריץ smoke tests מול השרת..."
  BASE_URL="${BASE_URL}" "${PYTHON_BIN}" scripts/smoke_webhooks.py
fi

echo "הכל עבר בהצלחה."

