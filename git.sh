#!/usr/bin/env bash
# ==============================================================================
# @file: git.sh
# @version: 1.0.0
# @date: 2026-08-27
# @description: Git-Deployment-Skript für den Server (PhidgetBackend)
# @author: Patrick Stähli
# ==============================================================================
set -e

APP_DIR="/volume1/docker/telemetry"
SCRIPT_VERSION="1.0.0"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=====================================================${NC}"
echo -e "${BLUE}       PhidgetBackend Server Git Deployment          ${NC}"
echo -e "${BLUE}       Skript-Version: v${SCRIPT_VERSION}            ${NC}"
echo -e "${BLUE}=====================================================${NC}"

# In das Server-Projektverzeichnis wechseln
cd "$APP_DIR"
git config --global --add safe.directory "$APP_DIR"

# 1. Alle geänderten & neuen Dateien stagen (berücksichtigt .gitignore)
git add -A

# 2. Aktuelle Version / Tags von GitHub holen
echo -e "${YELLOW}--> Prüfe aktuellen Versionsstand auf Git...${NC}"
git fetch origin >/dev/null 2>&1 || true
CURRENT_TAG=$(git describe --tags --abbrev=0 origin/main 2>/dev/null || git log -1 --oneline | awk '{print $1}')
echo -e "${GREEN}✔ Aktuelle Version auf Git: ${BLUE}${CURRENT_TAG:-Keine Tags vorhanden}${NC}"

# 3. Interaktive Release-Informationen
echo -e "\n${BLUE}--- Release-Informationen eingeben ---${NC}"

exec < /dev/tty

read -rp "Neue Versionsnummer (z. B. v2.2.0): " NEW_VERSION
if [ -z "$NEW_VERSION" ]; then
    echo -e "${RED}Fehler: Versionsnummer darf nicht leer sein.${NC}"
    exit 1
fi

read -rp "Titel des Updates (z. B. Härtung Ingest-API & SkipScan-Index): " UPDATE_TITLE
read -rp "Optionaler Kommentar / Beschreibung: " UPDATE_COMMENT

# 4. Commit zusammenstellen
COMMIT_MSG="${UPDATE_TITLE:-Update auf $NEW_VERSION}"
if [ -n "$UPDATE_COMMENT" ]; then
    COMMIT_MSG="${COMMIT_MSG} - ${UPDATE_COMMENT}"
fi

if git diff --cached --quiet; then
    echo -e "${YELLOW}Hinweis: Keine geänderten Dateien gefunden. Erstelle leeren Release-Commit...${NC}"
    git commit --allow-empty -m "$COMMIT_MSG"
else
    echo -e "${YELLOW}--> Erstelle Commit: '${COMMIT_MSG}'...${NC}"
    git commit -m "$COMMIT_MSG"
fi

# 5. Git Tag setzen
echo -e "${YELLOW}--> Setze Git Tag ${NEW_VERSION}...${NC}"
git tag -a "$NEW_VERSION" -m "${COMMIT_MSG}"

# 6. Push zu GitHub (Branch + Tag)
echo -e "${YELLOW}--> Lade Server-Änderungen und Tag ${NEW_VERSION} zu GitHub hoch...${NC}"
git push origin main
git push origin "$NEW_VERSION"

echo -e "\n${GREEN}=====================================================${NC}"
echo -e "${GREEN}    SERVER DEPLOYMENT ERFOLGREICH ABGESCHLOSSEN      ${NC}"
echo -e "${GREEN}=====================================================${NC}"