#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from pathlib import Path

# Basisverzeichnis des Projekts
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "project_code_dump.txt"

# Ausschliesslich diese Ordner scannen
RELEVANT_DIRS = ["analyzer", "api", "phidget-dashboard"]

# Dateitypen, die echten Code/Konfiguration enthalten
CODE_EXTENSIONS = {".py", ".yml", ".yaml", ".json", ".sql", ".sh", ".html", ".css", ".js", ".env"}

# Explizite Ignore-Muster (Daten, Logs, Caches)
IGNORE_DIRS = {
    "grafana_data", "import_logs", "ntfy_cache", "timescale_data", 
    "archive", "logs", "__pycache__", ".git"
}
IGNORE_FILES = {
    "project_code_dump.txt", "dump_project.py", ".DS_Store"
}

def is_text_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            f.read(1024)
        return True
    except Exception:
        return False

def collect_code():
    collected = []
    
    # 1. Root-Dateien pruefen (z.B. docker-compose.yml, .env)
    for item in BASE_DIR.iterdir():
        if item.is_file() and item.name not in IGNORE_FILES:
            if item.suffix in CODE_EXTENSIONS or item.name in ["Dockerfile", "requirements.txt"]:
                collected.append(item)

    # 2. Relevante Sub-Ordner scannen
    for rel_dir in RELEVANT_DIRS:
        target_dir = BASE_DIR / rel_dir
        if not target_dir.exists():
            continue

        for root, dirs, files in os.walk(target_dir):
            # Unerwuenschte Ordner direkt im Walk skippen
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for file in sorted(files):
                if file.startswith(".") or file in IGNORE_FILES:
                    continue

                fpath = Path(root) / file
                if fpath.suffix in CODE_EXTENSIONS or file in ["Dockerfile", "requirements.txt"]:
                    collected.append(fpath)

    # 3. Zusammenfassung in TXT schreiben
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        out.write("=== PROJEKT STRUKTUR & QUELLCODE DUMP ===\n")
        out.write(f"Erstellt am: {Path(BASE_DIR).resolve()}\n\n")

        # Struktur-Uebersicht
        out.write("--- DATEIUEBERSICHT ---\n")
        for fpath in collected:
            rel_path = fpath.relative_to(BASE_DIR)
            out.write(f"- {rel_path}\n")
        out.write("\n" + "=" * 60 + "\n\n")

        # Datei-Inhalte
        for fpath in collected:
            rel_path = fpath.relative_to(BASE_DIR)
            out.write(f"FILE: {rel_path}\n")
            out.write("-" * 50 + "\n")
            
            if is_text_file(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        out.write(f.read())
                except Exception as e:
                    out.write(f"// FEHLER BEIM LESEN: {e}\n")
            else:
                out.write("// [BINAERDATEI UEBERSPRUNGEN]\n")
                
            out.write("\n\n" + "=" * 60 + "\n\n")

    print(f"Fertig! {len(collected)} relevante Dateien wurden zusammengefasst in:\n-> {OUTPUT_FILE}")

if __name__ == "__main__":
    collect_code()