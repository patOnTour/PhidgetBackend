==============================================================================
DEV-06 BENCHMARK & SIMULATOR - KURZANLEITUNG
Datum: 24./25. August 2026
Autor: Patrick Staehli
==============================================================================

1. ZIEL & TESTAUFBAU (DEV-06)
------------------------------------------------------------------------------
Vergleich der Trigger-Erkennung beim Abbindebeginn von Beton:
  - Algorithmus A: Savitzky-Golay Filter (Standard)
  - Algorithmus B: Reiner Polynomfilter 2. Ordnung
Ziel: Reaktionszeit (Delta t), Rauschtoleranz und Fehlalarm-Sicherheit.


2. DATENERFASSUNG IN DER NACHT
------------------------------------------------------------------------------
Das Skript 'raw_recorder.py' laeuft im Hintergrund im Container 'server_analyzer'.
  - Aktivitaetsfenster: 00:00 bis 05:00 Uhr MESZ (Schweizer Lokalzeit)
  - Zielgeraete: ccssite01 (Koffer 1) & ccssite02 (Koffer 2)
  - Zieltabelle: telemetry_raw_benchmark (geschuetzt vor Housekeeping-Loeschung)
  - Logdatei: /app/raw_recorder.log


3. SCHRITTE AM MORGEN
------------------------------------------------------------------------------
Schritt 1: Erfasste Daten in der Datenbank kontrollieren
Befehl auf der Synology:
  docker exec -it timescale_db psql -U postgres -d telemetry_db -c "
  SELECT device_id, channel, MIN(time) as start_utc, MAX(time) as end_utc, COUNT(*) as messpunkte
  FROM telemetry_raw_benchmark
  GROUP BY device_id, channel
  ORDER BY device_id, channel;
  "

Schritt 2: Benchmark-Simulation starten
Befehl auf der Synology:
  docker exec -it server_analyzer python benchmark_filters.py <DEVICE_ID> <KANAL> "<START_LOKAL>" "<ENDE_LOKAL>"

Beispiel Koffer 1, Kanal 2:
  docker exec -it server_analyzer python benchmark_filters.py ccssite01 2 "2026-08-25 01:00" "2026-08-25 04:30"

Beispiel Koffer 2, Kanal 0:
  docker exec -it server_analyzer python benchmark_filters.py ccssite02 0 "2026-08-25 01:00" "2026-08-25 04:30"


4. ERGEBNIS-BEWERTUNG
------------------------------------------------------------------------------
Das Skript gibt eine Vergleichstabelle aus:
  - Trigger-Zeitpunkt (Lokalzeit MESZ)
  - Trigger-Temperatur (Grad Celsius)
  - Zeitdifferenz (Poly - SG in Sekunden)
==============================================================================
