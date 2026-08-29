# SYSTEM CONTRACT & ARCHITECTURE

## 1. Identifikatoren & Namenskonventionen
- **Box-ID (Global):** Eindeutiger technischer String (z. B. `ccssite99`, `ccssite01`, `ccssite02`).
- **YAML-Dateiname:** Entspricht dem Box-Namen oder der Box-ID im Ordner `config/devices/*.yaml` (z. B. `dummy.yaml`, `baustellenkoffer_1.yaml`).
- **YAML Root-Key:** Name der Sektion (z. B. `dummy:`). Enthält Pflichtfelder `device_id`, `channel_recording_enabled` (bool), `auto_detection_enabled` (bool).
- **Telemetrie Payload:** Sendet zwingend `device_id` (`ccssite99`), `channel` (int, 0-7 Messkanäle, 100 Umgebung, 101 Feuchte) und `job_id` (`temp0`-`temp7`, `ambient`, `humidity`).

## 2. Datenbank-Hoheit & Schema as Code
- **DDL-Hoheit:** Tabellen-Definitionen (DDL) und Hypertables liegen exklusiv in `db/init/01_schema.sql` (Mount: `./db/init:/docker-entrypoint-initdb.d:ro`).
- **Keine Applikations-DDL:** Anwendungsdienste (`fastapi_ingest`, `concretum-control`, `server_analyzer`) führen NIEMALS DDL (`CREATE TABLE`, `ALTER TABLE`) aus. Beim Booten wird lediglich ein defensiver Verbindungstest (`SELECT 1;`) durchgeführt.
- **Migrationen:** Künftige Schema-Änderungen an Produktivdatenbanken erfolgen ausschliesslich als versionierte SQL-Patches via `db/migrations/*.sql`.
- **Primärschlüssel-Konvention:**
  - `telemetry_data`: `(time, device_id, channel)` [Timescale Hypertable]
  - `device_channel_metadata`: `(device_id, job_id)` mit `friendly_name`
  - `device_channel_metadata_history`: `(device_id, job_id, valid_from)`
  - `analyzer_state`: `(device_id, channel, job_id)`
  - `alerts_history`: `id` (PK, Serial), Indizes auf `(device_id, channel, event_time)`
  - `device_status`: `device_id` (PK)

## 3. Container- & Volume-Architektur
- **Laufzeitumgebung:** Alle Python-Services (`fastapi_ingest`, `concretum-control`, `server_analyzer`, `probe-detector`) basieren auf `python:3.11-slim` mit gemounteten Source-Ordnern (Hot-Reloading ohne Image-Rebuild).
- **Konfigurations-Mount:** `./phidget-dashboard/config:/app/config:ro` dient als zentrale Konfigurationsquelle.
- **Netzwerk:** Interne Kommunikation über `telemetry_net` Bridge; Ingest-Exponierung via Cloudflare Tunnel (`cloudflared`).

## 4. Paketmanagement & Dependency Isolation
- **Gepinnte Abhängigkeiten:** Jeder Dienst nutzt eine eigene `requirements.txt` im Dienstverzeichnis (z. B. `api/requirements.txt`).
- **Startbefehl:** Standardisiert über `sh -c "pip install -q --no-cache-dir -r requirements.txt && <start-command>"`.

## 5. Ingest-Resilienz & Puffer-Verhalten
- **Edge-Pufferung:** Phidget-SBC4-Boxen speichern Messreihen bei Verbindungsausfällen lokal (SQLite) und liefern gepufferte Pakete via `X-Pending-Count`-Header nach.
- **Downtime-Toleranz:** Kurzzeitige API-Neustarts (< 20s) führen durch asynchrones Retry-Handling auf Client-Seite zu keinem Datenverlust.