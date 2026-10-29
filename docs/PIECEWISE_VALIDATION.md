# Validierung der abschnittsweisen Synchronisation (Vorschau)

Der neue Segmentmodus ist bewusst konservativ und muss vor unbeaufsichtigten
Serienstapeln an echten Schnittfassungen geprüft werden. Ein erfolgreicher
synthetischer FFmpeg-Test allein belegt nicht die Korrektheit aller Filmfassungen.

## Erkennbares Ergebnis

- Normale Fassungen behalten die bisherige lineare Korrektur.
- Bei einer eindeutig nachgewiesenen Einfügung/Entfernung erscheint die Meldung
  `2 Abschnitte bestätigt` (bzw. mehr) und die Diagnose enthält
  `method: piecewise_verified` mit `stages.piecewise_fit.segments`.
- Die Zusatztonspur enthält Ton nur dort, wo Szenen visuell zusammenpassen.
  Für nicht zuordenbare Szenen des Bildmasters enthält sie Stille. Die
  Originalaudiospuren des Bildmasters bleiben erhalten.
- Der Ankerplot zeigt die Abweichung relativ zum jeweiligen Segment. Die
  Hörprobe lässt keine Vorschau aus einer nicht zugeordneten Masterstelle zu.
- Eine absichtliche manuelle Offset- oder Skalenänderung schaltet die
  betroffene Zusatzspur wieder in den alten linearen Modus.

## Praktischer Abnahmetest

1. **Identische Videotimeline:** Ein Bildmaster mit alternativer Audiosprache
   muss weiterhin ohne Segmentmodus exportieren. Am Anfang, in der Mitte und
   am Ende die Tonspur kurz vorhören.
2. **Zusätzliche Szene im Master:** Beide Seiten eines bekannten Schnitts
   mit Bildankern bestätigen. Die neue Audiofassung darf im hinzugefügten
   Masterabschnitt keine Dialoge aus der Quellfassung abspielen.
3. **Entfernte Szene im Master:** Die Quellaudioszene muss übersprungen werden;
   der Dialog danach muss zum neuen Bildzeitpunkt passen.
4. **Falsche Episoden mit gleichem Intro/Abspann:** Kein automatischer Export;
   einen entsprechenden Ablehnungsgrund im Stapelbericht prüfen.
5. **Schnittkante prüfen:** Etwa 10 Sekunden davor und danach auf fehlende
   Silben, Knackser, Echo und unerwünschte Überschneidungen hören. Ein
   einzelner Ankerfehler darf keine ungeprüfte Tonverschiebung freigeben.
6. **Abbruch und Wiederholung:** Einen Export abbrechen; das Original und eine
   bereits bestehende Ausgabe müssen unverändert bleiben. Anschließend
   dieselbe Folge erneut verarbeiten.

## Grenzen

Der automatische Modus unterstützt derzeit zwei bis fünf monotone Abschnitte.
Er benötigt mindestens acht gute Anker und acht unterschiedliche Bilder je
Abschnitt, nahe beieinanderliegende Videozeiten innerhalb jedes Abschnitts
und ausreichend lange bestätigte Überlappung. Die Bilderkennung behandelt
keine vertauschte Szenenreihenfolge, keine freie Audiomontage und keine
unbelegten langen Zwischenbereiche.

Die vorhandenen Performanceprofile gelten weiterhin. Die NumPy-Optimierung
beschleunigt den CPU-Sequenzabgleich; Hardware-Decoding wird nur genutzt,
wenn es der installierte OpenCV-/FFmpeg-Stack tatsächlich unterstützt.
Das Mastervideo wird beim Export weiterhin kopiert, nicht GPU-neukodiert.

## Bekannte QA-Lücke

Für eine produktive Freigabe müssen OpenCV-Videoanalyse, PySide6-GUI und die
übliche lokale NAS-/Linux-Umgebung mit echten Medien geprüft werden. Die
synthetischen Integrationstests decken Ton-Lücken und übersprungene Szenen ab,
ersetzten aber keine Hörkontrolle konkreter Episoden.
