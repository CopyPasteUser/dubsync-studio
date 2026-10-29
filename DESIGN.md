---
version: alpha
name: DubSync Studio
description: Native desktop audio synchronization workstation
colors:
  primary: '#a8dfd3'
  background: '#17191c'
  surface: '#202327'
  text: '#eef0f2'
  muted: '#adb5be'
  border: '#424950'
typography:
  sans: {fontFamily: 'Noto Sans, Segoe UI, sans-serif'}
  mono: {fontFamily: 'Noto Sans Mono, DejaVu Sans Mono, monospace'}
rounded:
  DEFAULT: '5px'
  lg: '8px'
spacing:
  panel: '14px'
  gap: '10px'
components:
  button: {}
  table: {}
  dialog: {}
---

# DubSync Studio Design System

## Overview

### Creative North Star
A quiet desktop editing bench: graphite surfaces, silver typography, pale sea-glass
controls. Paired source strips and a persistent job transport connect the UI to
its actual job: retaining a picture master while aligning other language tracks.

### Product context and register
German-language Linux desktop tool for local/NAS media collections. Product
register, not a marketing site. Existing PySide6/Qt stack remains canonical.
User requested a complete GUI redesign; the workflow and safety evidence is in
README.md and docs/ARCHITECTURE.md. No Japan-specific market requirements.
Avoid decorative gradients, dashboard statistics and fabricated waveforms.
`dubsync/gui/theme.py:TOKENS` is the canonical runtime token source. This document
mirrors its main semantic roles; `studio_stylesheet` is the single Qt adapter.
Legacy analysis plots retain their domain chart palette in main_window.py.

## Colors
Canvas #17191c, surface #202327, primary #a8dfd3 with dark ink #142c29.
Text #eef0f2; secondary text #adb5be. Success, warning and error always have text,
not just colors. High-contrast mode raises borders and secondary-text contrast.

## Typography
Noto Sans / Segoe UI for labels and restrained 21–26px headings. Noto Sans Mono /
DejaVu Sans Mono for the activity log. Body 13px. No web fonts or font downloads.

## Layout
Horizontal workspace navigation. Source list can collapse after scanning;
episodes, the visual Abgleich-Monitor and the track inspector share a resizable
three-pane splitter. Each owns its scroll. The center monitor has "Zeitachse"
and "Framevergleich" tabs so the anchor plot and images remain usable in a
compact viewport. At heights below 950px the redundant Studio heading and
brand subtitle hide.
Minimum desktop viewport 980×680. Export owns a QScrollArea, allowing all options
to remain reachable in small windows. Long table columns scroll horizontally.

## Elevation & Depth
Flat tonal layers and 1px borders; no blur, shadows, gradients or GPU compositing.
Native dialogs retain platform focus behavior.

## Shapes
5px controls, 8px panels. A narrow accent edge identifies a source strip.

## Components

### Foundational visual states
Explicit hover, pressed, focus, disabled and selected rules. Disabled primary
buttons lose their accent. Busy state uses real progress and existing worker
signals. Empty library and no-results states explain the next action.

### Buttons and actions
One accent action per decision area. Cancel is outlined in danger tone, never the
implicit default. Native Qt buttons preserve keyboard activation and semantics.

### Navigation and data display
Exclusive tab-style button group; arrow keys and Alt+1/2/3. Table keeps native
single-row selection. `Ctrl+F` focuses episode search. “Abgleich offen” describes
unanalysed pairs honestly. Source/master previews remain unverified unless the
core matching proof passes its existing safety conditions.

### Forms and overlays
Native Qt comboboxes, dialogs and file pickers own keyboard/popup behavior.
App-owned styles apply to Qt descendants. Keep source selection and export values
when switching pages. Existing overwrite/cancel confirmations remain authoritative.
The performance panel displays detected CPU, available thread/RAM budgets and
unverified GPU candidates as compact technical evidence. Leicht/Mittel/Stark
labels describe resource load, not differing synchronization confidence.

### Iconography
Existing DubSync SVG identity. Text labels on actions; Undo/Redo retain named icons.

### Motion
No added animations, timers or polling. Native progress is functional feedback.

### Content and data visualization
German actions name their result. No speculative live CPU/GPU data. Technical logs
retain only 3000 on-screen blocks; persistent rotating logs and reports remain.
The 2.0 timeline uses only real anchors/segments in the domain object; empty
intervals are not painted as verified coverage. Side-by-side stills are read
on demand through a bounded, cancellable FFmpeg process, not at startup.
Stored queue jobs are a direct view of the private SQLite source of truth.

## Do's and Don'ts
- Do preserve exact source/master mappings, diagnostics and performance settings.
- Do validate both empty and populated editors at the minimum desktop size.
- Don't call a group safely exportable merely because it has two files.
- Don't duplicate theme constants across screen-local stylesheets.
