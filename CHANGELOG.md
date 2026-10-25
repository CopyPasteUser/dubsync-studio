## 1.5.14 – Neue Oberfläche & adaptive Leistung (2026-10-25)

- Neue dreigeteilte Oberfläche mit Sidebar: Bibliothek, Export, Aktivität & Diagnose;
  klare Navigation mit nur relevanten Bedienelementen pro Schritt.
- Stapelstart und Ausgabeoptionen auf separater Exportseite; globale Jobanzeige
  mit Pause/Abbrechen und sofortigem Wechsel zur Aktivitätsansicht.
- Vollständiges Stapel-Gesamtprotokoll und letzter Job-Verlauf direkt erreichbar.
- Profil-Schnellwahl: Leicht, Ausgewogen, Stark, Auto sowie Feineinstellungen.
- Hardwareadaptive CPU-/RAM-/Sample-Budgets mit wirklich unterschiedlichen
  Threadzahlen, NAS-schonender Metadaten-Konkurrenz und ehrlicher GPU-Fallback-Angabe.
- Inhalts-Kreuzprüfung und Sicherheitsbewertung aus 1.5.13 unverändert.

## 1.5.13 – Sichere Kreuzprüfung bei vertauschten Episoden (2026-10-25)

- Gefundene Bildanker fast ausschließlich am Anfang und Ende werden als
  möglicher Inhalts- bzw. Episodenidentitätsfehler ausgewiesen.
- Wenn zwei bislang unsichere Folgen derselben Staffel betroffen sind,
  kontrolliert der Stapel **beide** gekreuzten Zuordnungen mit vollständigen
  Videoanalysen und protokolliert die Ergebnisse.
- Nur zwei gegenseitig sichere, im Episoden-Mittelteil visuell bestätigte
  Abgleiche dürfen eine korrigierte Audiospurzuordnung auslösen. Keine
  automatische Neuverwendung schon exportierter oder manuell festgelegter
  Quellen, keine Änderung von Quelldateien.
- Zusätzliche Regressionstests für echte E02/E03-Vertauschung, einseitige
  Treffer und gemeinsame Intros/Abspänne ohne passende Hauptszenen.

## 1.5.12 – Stapel-Gesamtprotokolle für alle Episoden (2026-10-25)

- Ein persistenter JSON-Gesamtbericht pro Stapel wird beim Start angelegt und während
  des Laufs aktualisiert. Alle Episodengruppen samt Datei-IDs und Status sind enthalten.
- Beide Bildabgleichversuche (normal / gründliche Wiederholung) dokumentieren
  alle technischen Diagnose-Stufen direkt im selben Bericht.
- Exportiert, übersprungen, fehlgeschlagen, abgebrochen und noch nicht gestartet
  werden pro Episode unabhängig von der Stapelposition erfasst.
- Eigener UI-Dialog mit Episodenübersicht, Versuchsdetails, gesamter Textauswertung,
  vollständigem JSON sowie Kopieren und Speichern.
- Neue CLI-Optionen `--latest-batch`, `--list-batches`, optional `--json`.
- Bestehende Sicherheitsprüfung und frische Sitzung pro Programmstart unverändert.

## 1.5.11 – Gezielte E02-Stapeldiagnose und sichere Zweitanalyse (2026-10-25)

- Langzeitabweichende Videopaare werden bei unsicherem ersten Abgleich vor dem Überspringen mit bis zu 3600 Samples einmal gründlicher geprüft (bei 1200 zunächst 2400).
- Eine zweite Analyse führt nur dann zum Export, wenn dieselben unveränderten Sicherheitskriterien erfüllt sind.
- Stapel zeigt jede übersprungene Folge mit konkreter Begründung, Messwerten und – wenn zutreffend – dem Grund einer bereits vorhandenen Ausgabe sofort im Live-Protokoll.
- Die Stapelzusammenfassung enthält alle übersprungenen Folgen anstelle von nur fünf Beispielen.
- Regressionstests für Episode E02 und bereits existierende Ausgaben.


## 1.5.10 — Lange Laufzeitabweichung / zusätzliche Intros

- Es gibt weiterhin **keine starre 120-Sekunden-Ablehnung**. Der strenge Gleichzeitbild-Schnelltest greift nur für fast gleich lange Fassungen; bei Laufzeitabweichungen ab 30 Sekunden folgt nun **vor** dem dynamischen Sequenzabgleich eine zusätzliche visuelle Suche nach einem konstanten großen Zeitversatz.
- Der Suchschritt nutzt selten wiederholte Bild-Hashes zur Hypothesenbildung und verifiziert Kandidaten anhand **aller** gesampelten Bildpositionen mit monotoner eindeutiger Zuordnung. Beispiel: 150 Sekunden zusätzliches Intro im Bildmaster oder 165 Sekunden zusätzliches Intro im Quellvideo.
- Kandidaten werden nur übernommen, wenn ausreichend unterschiedliche Bilder über einen großen Abschnitt übereinstimmen **und** die bisherigen Export-Sicherheitsprüfungen bestehen. Beliebige Verschiebung um die Laufzeitdifferenz oder Export ohne Bildbeweis ist nicht erlaubt.
- Die Diagnose erhält `stages.long_offset` mit getesteten Versätzen, Ankerzahl, Überdeckung und Ablehnungsgründen. Scheitert die neue Suche, bleiben der vorhandene Sequenzabgleich und Fallback aktiv.
- Neu: Regressionstests für >2-Minuten-Versatz in beide Richtungen, verschiedene Episoden, nur gemeinsames Intro und wiederholte Standbilder; frische Analyse bleibt Standard.
- Ein ausdrücklich bestätigter Stapelstopp bleibt weiterhin ein Stopp; ein Bericht mit Status `cancelled` ist weiterhin **keine** abgelehnte Synchronisation.

## 1.5.9 — Neuer Start ohne Altzustand

- **Jeder App-Start = neue Sitzung:** Keine automatisch übernommenen Bibliotheksordner, Sprachregeln, Episode-/Master-Zuordnungen, Track-Anpassungen, offenen Projekte oder Synchronisationsergebnisse.
- **Einstellungen bereinigt:** Nur Ausgabeordner, Struktur, Performance und hoher Kontrast werden über Neustarts gespeichert. Historische Sitzungsfelder werden beim ersten Start aus `settings.json` entfernt.
- **Sitzungsgebundener Cache:** Neue temporäre Alignment-Cache-Datenbank je Programmlauf; alter persistenter Cache wird nicht eingelesen. Temporärer Cache wird beim Schließen entfernt.
- **Bewusstes Projekt-Öffnen weiterhin möglich:** `.dubsync`-Projektdateien werden weder automatisch geöffnet noch gelöscht.
- **Diagnose- und Jobprotokolle bleiben erhalten** für technische Fehlersuche, werden aber nicht als Bibliothek/Alignment wiederhergestellt. Videoquellen und exportierte Dateien werden nicht gelöscht.
- Deutliche Kennzeichnung „NEUE SITZUNG“ und aktualisierte Dokumentation/Regressionstests.

## 1.5.8 — Stapelstop, Episodenidentität und NAS-Priorität

- **Abbrechen im Stapel verlangt jetzt eine ausdrückliche Bestätigung.** Die Stopptaste ist keine implizite Standardaktion in Dialogen, wodurch versehentliche Tastaturauslöser seltener werden. Die Diagnose unterscheidet einen bestätigten Batch-Stopp von einer einzelnen Aufgabe.
- **Teilergebnisse nach Stopp bleiben sichtbar:** Exportierte, ausgelassene, fehlgeschlagene und wegen des Stopps nicht gestartete Episoden werden in der Zusammenfassung und im Protokoll angezeigt; der Abbruch zählt nicht als unsichere Synchronisation.
- **Stapelposition ≠ Episodennummer:** Auch der JSON-Diagnosebericht dokumentiert beide Werte getrennt; Fortschritt und Vorabprüfung zeigen `Stapelposition 1/13 · Episode S01E04` statt einer mehrdeutigen Zahl. Die Bestätigungsseite und das lokale Log zeigen, welche Dateinamen zu welcher Episode gehören.
- **Identitätsprüfung vor dem Export:** Ein Dateiname mit expliziter Kennung `S01E001` darf nicht unbemerkt in einer `S01E004`-Gruppe landen. Widersprüchliche Gruppen werden vor dem Decodieren abgelehnt; `S01E004` wird korrekt als Folge 4 interpretiert.
- **Sofort Stapel nach Scan:** Läuft noch Metadaten-Vorwärmen auf dem NAS, wird es zugunsten der eigentlichen Stapelanalyse abgebrochen, um unnötige konkurrierende Zugriffe zu vermeiden.
- **Keine Änderung an Audio-Freigabegrenzen, Quelldateien oder Encoder-Vorgaben.** Ein gleicher Dateiname garantiert weiterhin nicht, dass der Videoinhalt dieselbe Episode darstellt.

## 1.5.7 — Abbruchdiagnose und UI-Verbesserungen

- Abgebrochene Bildanalysen protokollieren nun partielle Sampling-Metriken, Bildposition und Abbruchauslöser (sofern bekannt).
- UI-Ergebnisleiste unterscheidet sicher, unsicher, abgebrochen und technischen Fehler; Direktschaltfläche für Diagnose (Ctrl+D).
- Diagnosefenster zeigt verständliche Ergebnis-Zusammenfassung und Roh-JSON in Tabs; beide lassen sich kopieren.
- Deutliche Laufzeitunterschiede werden in der Episodenauswahl erklärt, ohne automatisch falsche Schlussfolgerungen zu ziehen.
- GUI- und CLI-Auswertung nutzen dieselbe reine, separat getestete Berichtszusammenfassung.
- Sicherheitsgrenzen und Exportverhalten bleiben unverändert.

## 1.5.6 — Ausführliche Bilddiagnosen und robustere Auswahl

- Pro frischer Synchronisationsanalyse begrenzte JSON-Berichte (auch bei Fehler/Abbruch) mit Entscheidungsgrenzen und Diagnosemesswerten. Maximal 40 im privaten XDG-Statusverzeichnis.
- GUI-Berichtsübersicht mit Auswahl pro Episode, JSON-Ansicht und Kopierfunktion, sowie CLI `--list-alignments`/`--latest-alignment [--json]`.
- Alternative visuelle Zeitabbildung wird auch bei großer, aber intern widersprüchlicher globaler Bildanker-Abdeckung geprüft; sichere und zeitlich konsistente Resultate haben bei der Auswahl Vorrang.
- Stagnierende/nicht-endliche PTS und wiederholtes Progress-Reporting behandelt; rein statische Standbilder reichen nicht zur automatischen Synchronisation.
- Alte Cache-Einträge invalidiert. Bestehende Sicherheitsgrenzen für Bildanker, Drift, RMS, Überdeckung und Konfidenz werden nicht gelockert.

## 1.5.5 — Drift-toleranter visueller Fallback

- Der visuelle Fallback bewertet jetzt begrenzte affine Zeitachsen-Hypothesen statt nur konstanten Zeitversatz. Das kann gemeinsame Szenen bei leicht abweichenden Laufzeit-/Zeitstempelskalen besser finden.
- Die Hypothesenbildung ist begrenzt und deterministisch; visuelle Ähnlichkeit, monotone eindeutige Bildanker und bestehende Sicherheitsschwellen bleiben erhalten.
- Kein erzwungener Export: Falls die Bildanker weiterhin nicht ausreichen oder die Zeitabbildung nicht sicher ist, bleibt die Synchronisation als unsicher markiert.

## 1.5.4 — Direkter Gleichzeitbild-Abgleich für alternative Audiospuren

- Prüft vor dem globalen Sequenzabgleich, ob zwei ähnlich lange Fassungen an denselben Zeitpunkten dieselben Bilder zeigen.
- Wenn die visuelle Übereinstimmung über die Laufzeit stark genug ist, wird eine direkte 1:1-Zeitabbildung verwendet. Das ist für gleiche Video-Timelines mit unterschiedlicher Audiospur oder Neu-Encoding gedacht.
- Bei abweichenden Bildern, größeren Laufzeitunterschieden oder zu wenigen Treffern bleibt der bisherige vorsichtige Sequenz-/Fallback-Abgleich aktiv. Dateinamen allein lösen keinen automatischen Export aus.

## 1.5.3 — Robuster Bildabgleich für abweichende Intros und Schnittfassungen

- Wenn der globale Bild-Sequenzabgleich zu wenige Anker findet, versucht DubSync einen zweiten Abgleich über robuste visuelle Offset-Stimmen.
- Der Fallback kann gemeinsame Szenen trotz unterschiedlicher Intros, Recaps oder ausgelassener Szenen finden.
- Sicherheitsgrenzen für unbeaufsichtigte Exporte bleiben unverändert; eine gefundene Übereinstimmung wird nicht automatisch als sicher eingestuft.

## 1.5.2 — Schnellfingerprint-Import korrigiert

- Behebt den `NameError` für `fingerprint_many`, wenn der Schnellfingerprint beim Medien-Preflight aktiviert ist.
- Die optionale Fingerprint-Vorbereitung kann damit wieder laufen; bei deaktiviertem Cache bleibt sie weiterhin aus.

## 1.5.1 — Frische Episodenverarbeitung und aufgeräumtere Oberfläche

- Stapel-Exporte verwenden keine alten Synchronisations-Cache-Ergebnisse mehr; Metadaten werden für jeden Batch frisch geprüft.
- Bereits vorhandene Ausgaben werden nicht mehr standardmäßig still übersprungen. Bei einem erneuten Lauf fragt DubSync vor dem Ersetzen vorhandener MKVs nach.
- Erkennt die Stapelverarbeitung dieselbe Quelldatei oder stichproben-identischen Videoinhalt in mehreren Episodengruppen (auch bei unterschiedlichen Pfaden), werden alle betroffenen Gruppen gestoppt statt dieselbe Folge unter mehreren Episodennummern zu exportieren.
- Fortschrittszeile vereinfacht: der dekorative Mini-Graph wurde aus dem Hauptbereich entfernt; Fortschrittsbalken, Status, Pause und Abbrechen bleiben.
- Leistungseinstellungen verwenden konservative CPU-Threads und GPU-Decoding nur, wenn der lokale OpenCV-Build es anbietet. GPU-Decoding wird nicht als GPU-Encoding ausgegeben.
- Vollständige Python-Syntaxprüfung und Regressionstests ausgeführt; Qt-GUI-Smoke-Tests sind in dieser Umgebung mangels PySide6 nicht ausführbar.

## 1.5.0 — Robustere Stapelverarbeitung, Pause und Fortschrittsvisualisierung

- Fortschrittsbereich um eine Verlaufskurve, Pause/Fortsetzen und klarere Zustandsmeldungen erweitert. Pause wird kooperativ am nächsten gemeldeten Fortschrittspunkt wirksam; Abbrechen löst eine Pause sofort auf.
- Stapel-Export prüft vor der teuren Analyse alle geplanten Ausgabepfade. Wenn verschiedene Episodengruppen auf denselben Dateinamen abgebildet würden, werden alle betroffenen Gruppen mit einem konkreten Zielkonflikt gemeldet statt stillschweigend als bereits erledigt behandelt.
- GPU-Nutzung bleibt adaptiv: Hardware-Decoding wird nur angefordert, wenn der lokale OpenCV-Build und das Gerät es anbieten; bei nicht nutzbarem Hardware-Decoding bleibt der CPU-Fallback erhalten.
- Release-Tests und Dokumentation aktualisiert.\n- Hinweis ergänzt: Hardware-Decoding ist nicht gleich GPU-Encoding; das normale Muxing kopiert das Bild und vermeidet damit unnötige Neukodierung.

## 1.4.3 — Desktop-Eintrag für KDE/SteamOS korrigiert

- Desktop-Datei nutzt `Icon=dubsync-studio`, passend zum installierten hicolor-Icon.
- Den fehleranfälligen `TryExec`-Eintrag mit Shell-artiger Quotierung entfernt; `Exec` bleibt Desktop-Entry-konform gequotet.
- `StartupWMClass` ergänzt; `StartupNotify` wird weggelassen, da die Anwendung keinen Startup-Notification-Handshake garantiert.
- Wenn `desktop-file-validate` verfügbar ist, schlägt die Installation bei ungültigem Eintrag fehl; CI installiert den Validator und macht die Prüfung zum Release-Gate.
- Versionsangaben in Paket, GUI, CLI, Installer und Tests vereinheitlicht.
- Reparaturskript `repair-desktop-entry.sh` ergänzt; es repariert Menüeintrag und eine vorhandene Desktop-Verknüpfung inklusive Icon-Cache-Refresh.

## 1.4.0 — Grafischer Linux-Installationsassistent

- Neuer mehrseitiger Installationswizard mit Willkommen, Zielordner, Optionen, Systemprüfung, Fortschrittsprotokoll und Abschlussseite.
- Optionale Desktop-Verknüpfung sowie Start nach erfolgreicher Installation.
- Wizard verwendet Tkinter aus der Standardbibliothek; PySide6 muss vor dem Start des Installers nicht vorhanden sein.
- CLI-Installer unterstützt `--install-dir` einschließlich Pfaden mit Leerzeichen.
- Verwaltete Installationsorte werden für Updates und Deinstallation registriert.
- Fremde, nicht leere Zielordner werden nicht geleert; Uninstaller schützt Konfigurations-, Cache- und Statuspfade.
- Tests für Wizard-Checkmodus, Zielpfadvalidierung sowie Installation und Deinstallation an benutzerdefinierten Orten ergänzt.

## 1.3.0 — Distroübergreifende Installation und Desktop-Integration

- User-lokaler Installer unter `~/.local/share/dubsync-studio/app` mit Launcher `~/.local/bin/dubsync-studio`.
- Desktop-Menüeintrag unter `~/.local/share/applications/dubsync-studio.desktop` mit lokal installiertem SVG-Icon.
- Unterstützt gängige systemd-/freedesktop-basierte Linux-Desktops inklusive XFCE, KDE Plasma, GNOME, Cinnamon, MATE und weitere XDG-konforme Umgebungen.
- Kein Root-Zugriff erforderlich; keine Änderung von Theme-, Panel- oder Systemeinstellungen.
- Distrobezogene Paket-Hinweise für FFmpeg/FFprobe, Python, venv und pip; Abhängigkeiten werden nicht automatisch über den Systempaketmanager installiert.
- Optionaler Desktop-Shortcut (`--desktop-shortcut`) und Uninstaller; Konfiguration, Cache und Jobhistorie bleiben beim normalen Deinstallieren erhalten.
- `./setup.sh`/`./run.sh` bleiben als portable Projektmodus verfügbar.

## 1.2.0 — Workflow-UI und Quellenkomfort

- Split the crowded one-row toolbar into three labelled workflow steps: sources, synchronization, and export. Moved output path and batch options into a separate panel.
- Added drag-and-drop for source directories and video files; a dropped video adds its parent folder. Single `.dubsync` project files can be opened by dropping them onto the application. Duplicate roots are removed.
- Added source-list context actions to open folders in the file manager, copy source paths, remove roots, and open source locations by double-click.
- Added network-safe root handling: recognized network mounts are added without a synchronous directory stat on the GUI thread, reducing stalls with sleeping or temporarily unavailable NAS shares.
- Added pure tests for drop classification and GUI smoke assertions for drag-and-drop controls.
- Local regression suite: **82 unit tests passed**; two interactive GUI smoke tests were skipped locally because PySide6 is not installed in this build container. The configured GitHub Actions job installs PySide6 and runs the offscreen GUI smoke tests.

## 1.1.0 — UI, NAS und Workflow-Performance

- Dark-UI klarer gestaltet und neues vektorbasiertes DubSync-Logo in Fenster, Desktop-Starter und Assets integriert.
- NAS-/Netzwerkquelle: SMB-/SFTP-/WebDAV-URIs an Dateimanager delegieren, Authentifizierung dort belassen und eingehängte Netzwerkpfade direkt auswählen; CIFS/NFS/SSHFS/GVFS werden erkannt.
- FFprobe-Medienprüfung parallelisiert und bei NAS-Mounts konservativ auf maximal zwei Worker begrenzt.
- Gecachte Schnell-Fingerprints aus Dateistichproben für Cache-Identität; keine vollständige Datei wird dazu gelesen oder decodiert.
- Hintergrund-Vorwärmen für Medienmetadaten, ETA pro stabiler Verarbeitungsphase und Fortschrittsstatus im Job.
- Synchronisierte Audio-Hörproben für frühe/mittlere/späte/manuelle Timeline-Positionen.
- Anker-/Residualdiagramm mit Toleranzband je synchronisierter Quelle.
- Undo/Redo für manuelle Zeitkorrekturen, Tastaturbedienung, Screenreader-Beschriftungen und optional hoher Kontrast.
- GitHub-Actions-CI mit Offscreen-Qt-GUI-Smoke-Test, zusätzlich zu FFmpeg-/Kernintegrationstests.
- NAS-Metadatenzugriff und Hintergrund-Vorwärmen sind begrenzt, um Netzwerklaufwerke nicht unnötig zu belasten.

## 1.0.0 — stabile Version

### Arbeitsablauf, Projektsicherheit und Supportfähigkeit

- Version 1.0.0 mit CLI-Diagnose (`--doctor`, `--doctor --json`) und konsistenter Versionsprüfung.
- Versionierte `.dubsync`-Projektdateien mit atomarem Speichern, Größen-/Schema-Validierung, fehlende Quellordner meldend und manuellen Track-/Bildmaster-Korrekturen.
- Persistente Job-Historie in SQLite; unvollständige Läufe werden beim nächsten Start markiert. Historieneinträge sind größenbegrenzt und bleiben auch bei sehr großen Fehlerdetails gültiges JSON.
- Rotierende lokale Diagnoseprotokolle mit Exception-Hooks.
- Release-Check für Python-Kompilierung, Kern-/Medien-Integrationstests, Abhängigkeits- und Versionsmetadaten.
- Regression suite: 64 Tests bestehen im Build-Container; PySide6 war hier nicht installiert, daher ist ein interaktiver GUI-Start auf dem Zielsystem weiterhin Teil der Release-Checkliste.

### Bewusste Funktionsgrenze

- Synchronisierung bildet pro zusätzlicher Tonspur weiterhin eine lineare Zeitbeziehung ab (konstanter Offset plus gleichmäßiger Drift). Unterschiedliche Schnitte bzw. nichtlineare lokale Zeitabweichungen werden als unsicher markiert und benötigen manuelle Prüfung; sie gelten nicht als automatisch vollständig korrigiert.

# Changelog

## 1.0.0 — Stable release

### Project workflow and recovery

- Added versioned `.dubsync` project files to persist roots, language/track-name rules, output destination, folder organization, skip-existing policy and performance settings.
- Project writes use an atomic temporary-file replace, restricted file permissions on POSIX systems, input-size limits, schema checks and JSON/type validation.
- Added project open/save/save-as workflows, a dirty marker, save/discard/cancel confirmation on project changes, and a warning for source folders that are no longer available.
- Added a persistent SQLite job journal for scans, analyses and exports. Jobs left in the running state are marked interrupted on the next start; history is bounded to avoid unbounded database growth.
- Added per-user rotating logs with exception hooks, plus GUI links to job history and the log file.
- Persisted per-file audio choices, language/title overrides, explicit manual alignment values and picture-master choices in projects; batch export now respects those overrides too.
- Added a per-track "Auto" reset to discard a manual offset/scale and restore the calculated alignment.
- Replaced whole-file JSON cache rewrites with a bounded SQLite cache, per-result writes, small in-memory LRU, private file permissions and legacy JSON-cache migration. Fixed bounded job-history details so truncation always produces valid JSON.

### Operations and packaging

- Added a headless `--doctor` mode, JSON diagnostics and `--version`, without importing Qt when diagnostics are requested.
- Added a release-check script for Python compilation, version consistency, required media tools and optional GUI-dependency enforcement.
- Updated packaging metadata to stable version `1.0.0` and the Production/Stable classifier.
- Fixed a duplicate rejection signal connection in the performance settings dialog and added guards against opening projects while a background job is active.
- Added tests for project-file round trips, malformed/future schema rejection, path checks, SQLite persistence, interrupted-job recovery, retention and CLI diagnostics.

### Validated media capabilities

- Regression suite covers synthetic constant offset and approximately uniform drift, actual MKV muxing, stream metadata, source preservation, batch skip behavior, output planning, profile-aware caching and memory/CPU-aware settings.
- Automatic synchronization remains affine per added audio track. Different cuts and strongly piecewise drift are marked uncertain and are not claimed to be automatically repaired.
- PySide6 was unavailable in the release container, so the GUI has syntax and static contract tests but still requires a runtime smoke test on a target desktop after `./setup.sh`.

## 0.1.4-alpha

### Platzsparende Ausgabe und System-Voreinstellungen

- Automatically select the smallest valid video file in each group as picture master for manual default selection and unattended series-batch exports; single-episode export still allows a manual override.
- Add a per-episode output-size estimate and warn when the smallest master is materially shorter than another version. Output video duration/resolution follows the selected master.
- Add configurable AAC bitrate for synchronized alternate audio tracks; default 128 kbit/s, with compact/high-quality options exposed in the settings dialog. Existing master audio tracks are stream-copied without being re-encoded.
- Estimate the per-episode output size and check free disk space plus a reserve before expensive alignment and export work.
- Choose the first-run analysis preset based on process-visible CPU cores and RAM/cgroup memory limit, preserving desktop headroom on smaller systems.
- Select automatic hardware decoding only when OpenCV exposes a supported mode and a compatible system decoder interface is detected; otherwise CPU decoding is the default. Manual override remains available.
- Reuse already-probed metadata in single-episode and batch alignment to avoid duplicate FFprobe process starts.
- Tests cover smallest-master selection, audio bitrate, output-size estimation, system-adaptive presets, and metadata reuse.
- Regression suite: `python3 -m unittest discover -s tests -v` — **38 tests passed** in the release build environment. PySide6 was unavailable in the build container, so interactive Qt launch remains to be confirmed on the target system.

## 0.1.3-alpha

### Oberfläche und Bedienkomfort

- Added a dedicated **Einstellungen** menu with Leistungs-/Analyseeinstellungen, Systemdiagnose, cache clearing, and an About dialog.
- Added a fast library search/filter for episode names, source filenames, multilingual groups, and single-file groups that need review.
- Added keyboard shortcuts: `Ctrl+O` add folder, `F5` scan, `Ctrl+Shift+E` batch export, and `Ctrl+Q` quit.
- Added exportable library reports in JSON or CSV, including source paths, detected grouping, planned output names, current alignment metrics, and active performance settings.
- Reduced the minimum window size for more laptop/display layouts.

### Performance and diagnostics

- Added Fast, Balanced, High Accuracy, and Custom analysis profiles.
- Exposed OpenCV CPU-thread limits, sample interval, maximum samples per video, hardware-decoder request, and alignment-cache enable/disable in the settings dialog.
- Added optional OpenCV/FFmpeg hardware-decoding requests (Auto, VA-API and platform-supported modes) with CPU-decoding fallback when the requested backend cannot be opened.
- Clarified in diagnostics that GPU acceleration is decoder-dependent; perceptual hashing and dynamic sequence matching still run on the CPU.
- Changed frame sampling to use `VideoCapture.grab()` for intermediate frames and only materialize the image for selected samples, reducing per-frame array conversion overhead.
- Bound the dynamic-programming sample budget (balanced default: 1400; quality profile: 2400) to control quadratic runtime and memory use.
- Applied configured OpenCV thread limits and capped persisted alignment-cache entries.
- Versioned cache keys by sample settings and decoder request so analyses made with a different profile are not silently reused.
- Added a cache-clear action that removes both in-memory and persisted alignment results without touching source or exported media.
- Made persistent settings safe against malformed values and environment-based config paths.

### Tests

- Added tests for performance setting validation, presets, OpenCV thread caps, cache profile isolation, cache clearing, disabled batch-cache behavior, and GUI wiring for settings/search/report export.
- Existing integration tests still generate short synthetic media, estimate a two-second trim and 3% drift, and verify the resulting multilingual MKV/track metadata/source preservation.
- Regression suite: `python3 -m unittest discover -s tests -v` — **30 tests passed** in the build environment.
- The build environment did not have PySide6 installed and could not reach package indexes, so the GUI could not be launched interactively here. GUI source compiles and static UI-contract tests pass; launch it on the target system after running `./setup.sh`.

## 0.1.2-alpha

- Add a persistent source-folder language/track-title mapping dialog. Users can assign names such as `Deutsch` and `English` to every video under a selected source folder.
- Normalize language names, short codes, and common locale tags to Matroska language codes.
- Set metadata for the selected/default audio stream in the master file as well as newly added audio streams, without modifying source files.
- Add a persistent output-directory field and output-path preview.
- Organize batch output as `Output/Series/Staffel NN/Series - SNNENN.multilingual.mkv` by default, with a flat-output option.
- Skip existing output files before FFprobe and video alignment work, and require explicit confirmation to overwrite.
- Reuse media metadata already probed during the current session in batch processing.
- Improve series-name selection and carry season/episode metadata from the scanner to the output path builder.
- Expand language detection to additional languages.
- Add tests for track labels, root-folder language mapping, organized output paths, output metadata, source preservation, and fast skipping of finished outputs.

### Verification

- `python3 -m py_compile dubsync/gui/main_window.py dubsync/core/*.py tests/test_core.py` passed.
- `python3 -m unittest discover -s tests -v` passed: 17 tests.
- The previous development container did not have PySide6 installed, so the GUI could not be launched interactively here.

### Cross-distro packaging details

- `./install.sh` performs a user-local installation with a stable launcher under `~/.local/bin`, an XDG application entry, and a registered SVG icon. It copies the release into a stable XDG data path, so the download directory may be removed after installation.
- `./install.sh --desktop-shortcut` optionally adds a launch icon to the configured desktop directory.
- `./uninstall.sh` removes the application but preserves user settings/cache/job history; `--purge-data` explicitly removes those data directories.
- `./setup.sh` checks Python 3.10+, FFmpeg and FFprobe; if system dependencies are missing, it provides a distro-specific manual package hint for Debian/Ubuntu/Mint, Fedora/RHEL, Arch/Manjaro, openSUSE or Alpine without installing packages with root privileges.
- The launch scripts use a dedicated virtual environment. A failed GUI launch from the desktop menu produces a notification where `notify-send` is available and stores diagnostics in the XDG state directory.
- A static packaging regression suite validates shell syntax, installer/uninstaller options, XDG paths, desktop metadata, and the Qt desktop file identity.
