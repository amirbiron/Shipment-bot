#!/usr/bin/env bash
set -euo pipefail

# סקריפט ליצירת ריפו מסונן לסקירת קוד
#
# יוצר עותק של הפרויקט עם הקבצים הרלוונטיים בלבד לסקירה,
# ללא קבצי תשתית, deployment, סכמה, ותיעוד פנימי.
# אופציונלית - יוצר ריפו פרטי ב-GitHub ודוחף אליו.
#
# שימוש:
#   bash scripts/create_review_repo.sh [--github REPO_NAME] [--target DIR]
#
# דוגמאות:
#   # יצירה מקומית בלבד
#   bash scripts/create_review_repo.sh
#
#   # יצירה + דחיפה לריפו פרטי ב-GitHub
#   bash scripts/create_review_repo.sh --github shipment-review
#
#   # יצירה בתיקייה מותאמת
#   bash scripts/create_review_repo.sh --target /path/to/review
#
# לאחר יצירת הריפו ב-GitHub:
#   1. הוסף את הסוקר כ-collaborator עם הרשאת Read
#   2. שתף את הקישור לריפו
#   3. לאחר הסקירה - מחק את הריפו או הסר גישה

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

# ברירות מחדל
TARGET_DIR=""
GITHUB_REPO=""

# פרסור ארגומנטים
while [[ $# -gt 0 ]]; do
    case $1 in
        --github)
            if [[ $# -lt 2 || "$2" == --* ]]; then
                echo "שגיאה: --github דורש שם ריפו כארגומנט"
                exit 1
            fi
            GITHUB_REPO="$2"
            shift 2
            ;;
        --target)
            if [[ $# -lt 2 || "$2" == --* ]]; then
                echo "שגיאה: --target דורש נתיב תיקייה כארגומנט"
                exit 1
            fi
            TARGET_DIR="$2"
            shift 2
            ;;
        --help|-h)
            # הצגת עזרה
            echo "שימוש: bash scripts/create_review_repo.sh [--github REPO_NAME] [--target DIR]"
            echo ""
            echo "אפשרויות:"
            echo "  --github REPO_NAME   יצירת ריפו פרטי ב-GitHub ודחיפה אליו"
            echo "  --target DIR         תיקיית יעד מותאמת (ברירת מחדל: /tmp/shipment-review)"
            echo "  --help, -h           הצגת הודעה זו"
            exit 0
            ;;
        *)
            echo "ארגומנט לא מוכר: $1"
            exit 1
            ;;
    esac
done

# קביעת תיקיית יעד
if [[ -z "$TARGET_DIR" ]]; then
    TARGET_DIR="/tmp/shipment-review"
fi

echo "=== יצירת ריפו מסונן לסקירת קוד ==="
echo "מקור: $SOURCE_DIR"
echo "יעד:  $TARGET_DIR"
echo ""

# ניקוי תיקיית יעד קיימת
if [[ -d "$TARGET_DIR" ]]; then
    echo "תיקיית יעד קיימת - מוחק..."
    rm -rf "$TARGET_DIR"
fi

mkdir -p "$TARGET_DIR"

# העתקה עם סינון באמצעות tar
# מעתיק הכל חוץ מהקבצים שלא רלוונטיים לסקירה
tar -C "$SOURCE_DIR" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.mypy_cache' \
    --exclude='.pytest_cache' \
    --exclude='.coverage' \
    --exclude='node_modules' \
    --exclude='uploads' \
    --exclude='.env' \
    \
    --exclude='docker-compose.yml' \
    --exclude='Dockerfile' \
    --exclude='render.yaml' \
    --exclude='.env.example' \
    --exclude='schema.sql' \
    \
    --exclude='migrations' \
    --exclude='scripts' \
    --exclude='docs' \
    --exclude='whatsapp_gateway' \
    \
    --exclude='.github' \
    --exclude='.claude' \
    \
    --exclude='CLAUDE.md' \
    --exclude='README.md' \
    --exclude='ARCHITECTURE.md' \
    --exclude='DATABASE.md' \
    --exclude='DEPLOYMENT.md' \
    --exclude='STATE_MACHINE.md' \
    --exclude='API_DOCS_GUIDE.md' \
    --exclude='PROJECT_MAP.md' \
    --exclude='CODE_REVIEW.md' \
    --exclude='CODE_REVIEW_REPORT.md' \
    --exclude='ISSUE_WEBHOOK_REFACTOR.md' \
    \
    --exclude='requirements.txt' \
    --exclude='requirements-dev.txt' \
    --exclude='.cursorrules' \
    --exclude='.gitignore' \
    \
    --exclude='frontend/package-lock.json' \
    --exclude='frontend/dist' \
    \
    -cf - . | tar -xf - -C "$TARGET_DIR"

# ספירת קבצים שהועתקו ובדיקה שיש תוכן
FILE_COUNT=$(find "$TARGET_DIR" -type f | wc -l)
if [[ "$FILE_COUNT" -eq 0 ]]; then
    echo "שגיאה: לא הועתקו קבצים - התיקייה ריקה"
    exit 1
fi
echo "הועתקו $FILE_COUNT קבצים"
echo ""

# הצגת מבנה התיקיות
echo "מבנה הריפו המסונן:"
if command -v tree >/dev/null 2>&1; then
    tree -L 2 --dirsfirst "$TARGET_DIR"
else
    find "$TARGET_DIR" -maxdepth 2 -type d | sort | while read -r dir; do
        # הצגת רמת הזחה לפי עומק
        depth=$(echo "$dir" | sed "s|$TARGET_DIR||" | tr -cd '/' | wc -c)
        indent=$(printf '%*s' $((depth * 2)) '')
        basename_dir=$(basename "$dir")
        echo "${indent}${basename_dir}/"
    done
fi
echo ""

# אתחול ריפו git מקומי
cd "$TARGET_DIR"
git init -q
git add -A
git commit -q -m "קוד לסקירה"
echo "ריפו Git מקומי אותחל בהצלחה"

# יצירת ריפו ב-GitHub אם התבקש
if [[ -n "$GITHUB_REPO" ]]; then
    echo ""
    echo "=== יצירת ריפו פרטי ב-GitHub ==="

    # בדיקה ש-gh CLI מותקן
    if ! command -v gh >/dev/null 2>&1; then
        echo "שגיאה: gh CLI לא מותקן. התקן מ-https://cli.github.com/"
        echo "הריפו המקומי נוצר בהצלחה ב: $TARGET_DIR"
        exit 1
    fi

    # בדיקה ש-gh מחובר
    if ! gh auth status >/dev/null 2>&1; then
        echo "שגיאה: gh CLI לא מחובר. הרץ: gh auth login"
        echo "הריפו המקומי נוצר בהצלחה ב: $TARGET_DIR"
        exit 1
    fi

    # יצירת ריפו פרטי
    # אם GITHUB_REPO כבר מכיל owner/repo (למשל org/name) - להשתמש כמות שהוא
    if [[ "$GITHUB_REPO" == */* ]]; then
        FULL_REPO="$GITHUB_REPO"
    else
        GITHUB_USER=$(gh api user --jq '.login') || {
            echo "שגיאה: לא ניתן לזהות את המשתמש ב-GitHub (בעיית רשת או אימות)"
            exit 1
        }
        if [[ -z "$GITHUB_USER" ]]; then
            echo "שגיאה: לא ניתן לזהות את המשתמש ב-GitHub (תשובה ריקה)"
            exit 1
        fi
        FULL_REPO="${GITHUB_USER}/${GITHUB_REPO}"
    fi

    # בדיקה שהריפו לא קיים
    if gh repo view "$FULL_REPO" >/dev/null 2>&1; then
        echo "שגיאה: ריפו $FULL_REPO כבר קיים"
        echo "הריפו המקומי נוצר בהצלחה ב: $TARGET_DIR"
        exit 1
    fi

    gh repo create "$GITHUB_REPO" --private --source="$TARGET_DIR" --push

    echo ""
    echo "=== הריפו נוצר בהצלחה ==="
    echo "קישור: https://github.com/${FULL_REPO}"
    echo ""
    echo "צעדים הבאים:"
    echo "  1. הוסף את הסוקר כ-collaborator עם הרשאת Read:"
    echo "     gh repo add-collaborator ${FULL_REPO} USERNAME --permission read"
    echo ""
    echo "  2. שתף את הקישור לריפו"
    echo ""
    echo "  3. לאחר סיום הסקירה, מחק את הריפו:"
    echo "     gh repo delete ${FULL_REPO} --yes"
else
    echo ""
    echo "=== הריפו המקומי נוצר בהצלחה ==="
    echo "מיקום: $TARGET_DIR"
    echo ""
    echo "לדחיפה לריפו פרטי ב-GitHub, הרץ שוב עם:"
    echo "  bash scripts/create_review_repo.sh --github shipment-review"
fi
