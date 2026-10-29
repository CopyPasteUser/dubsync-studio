# DubSync Studio – Roadmap nach 2.0.0b2

**Stand:** 2026-10-29 · **Planung, keine Implementierungszusage oder
Kalenderfreigabe.** Version 2.0.0b2 ist weiterhin Beta.

Die vollständigen Freigabekriterien für 2.0 liegen in
[ROADMAP_2_0.md](ROADMAP_2_0.md) und
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

| Ziel | Schwerpunkt | Freigabekriterium |
| --- | --- | --- |
| 2.0.0b3 | Hardware-Selbsttest, messbare GPU-Auswahl, individuelle Leistungsbudgets, Diagnose im UI | Nachweisbarer schneller und korrekter Decodepfad, reproduzierbare CPU-/GPU-Messung, sicherer Fallback |
| 2.0 RC1 | NAS-Recovery, Crash-/Queue-Fortsetzung, Installations- und Accessibility-QA | Echte unterbrochene Stapel, verschiedene Systeme und beschädigte/fehlende Mounts geprüft |
| 2.0 Stable | Korrekte Exporte und langfristig wartbare Basis | Sämtliche 2.0-Release-Gates erfüllt; keine stillen Quellenänderungen oder ungesicherten Exportfreigaben |
| 2.1 | Audio-Waveforms, A/B-Hörvergleich, Timeline-Werkzeuge und Konfidenz-Navigation | Verbesserte manuelle Prüfung ohne automatische Freigabe manuell veränderter Abschnitte |
| 2.2 | Sicherere Episoden-Kandidatenwahl, Staffelvorlagen, Queue-Drill-down | Widersprüche werden sichtbar, falsche Fassung wird nicht still automatisch gewählt |
| 2.3 | Adaptives Sampling, begrenzte Frame-Caches, NAS-aware I/O und Laufzeitprofile | Reale Geschwindigkeit gemessen, keinerlei geringere Zuordnungs- oder Exportintegrität |
| 3.0 | Unterschiedliche Schnittfassungen, segmentweiser Drift und mehrstufiger Inhaltsabgleich | Szenenzuordnung anhand unabhängiger Signale überprüfbar; unsichere Stellen bleiben gesperrt |

## Nächster Arbeitsauftrag: 2.0.0b3

1. **Hardware inventarisieren:** CPU-/RAM-Limits, verfügbare GPUs und FFmpeg-
   Codecpfade erkennen, ohne Hardwareausführung allein aus einem Gerätenamen
   oder der FFmpeg-Option abzuleiten.
2. **Sicheren Selbsttest bieten:** einen kurzen lokalen Dekodierlauf je
   relevantem Kandidaten mit konsistenter Bildfingerprint-Referenz vergleichen.
   Incompatible oder langsamere Pfade fallen auf CPU zurück.
3. **Ressourcenprofile kalibrieren:** Leicht/Mittel/Stark weiterhin anhand
   der konkreten Maschine und ihrer aktuellen Last ableiten; alle drei benutzen
   dieselben Sicherheitsbedingungen für Matching und Export. Stark heißt
   maximaler *Durchsatz*, nicht zwingend 100 % CPU-Last.
4. **Transparenz im Studio:** effektiven Decoder, Workerzahl, reale und
   angeforderte Beschleunigung, Fehlergrund und Benchmarkresultat anzeigen.
5. **NAS- und Crash-Tests vorbereiten:** deterministische lokale Ausfalltests
   erweitern; echte SMB-/NFS- und Strom-/Prozessabbruchtests bei wieder
   erreichbarem NAS nachholen.

## Leitlinien für 2.1 bis 3.0

- Wellenformen und Hörproben dienen der **Prüfung**, nicht als Ersatz für
  die unabhängige videobasierte Episoden- und Szenenfreigabe.
- Neue Timeline-Interaktionen brauchen Undo/Redo, Grenzenkontrolle und
  explizite Rückmeldung zum unsicheren/manuellen Zustand.
- Automatische Stapelwiederaufnahme darf niemals eine veränderte Quelle,
  ungültige Rezeptur oder beschädigte MKV ungeprüft überspringen.
- Vorschaudaten und Caches sind begrenzt; ein bloßer Episodenwechsel soll
  keine unnötigen großen NAS-Lesevorgänge auslösen.
- Ein Feature gilt erst nach Regressionstests und möglichst realen
  CPU-/GPU-/Speicher-/I/O-Messungen als performant oder releasefähig.
