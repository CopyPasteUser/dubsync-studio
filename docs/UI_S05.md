# S05 — macOS-inspired native workspace

Consolidated with S04 in integration PR #37. Default light appearance; dark and high contrast under
Einstellungen → Erscheinungsbild / Hoher Kontrast. Semantic tokens are shared
with the custom plots, timeline, waveform and segment editor. No blur, custom
window chrome or new dependencies. Existing Qt controls, media handlers,
export authorization and project state remain authoritative.

Library and inspector have distinct surfaces. Existing episode actions move
above the review splitter. Batch continuation remains below. Inspector can
be independently collapsed (Alt+I); the existing focus mode (Alt+0) preserves
whether it was collapsed. Appearance is persisted as a user preference, not
a project edit.

## Partial verification

Three isolated Python appearance tests passed, including 36 semantic
foreground/background combinations at a minimum 4.5:1 contrast, invalid
preferences and palette isolation. These do not certify actual Qt rendering
or full accessibility. New Qt workspace tests cover inspector/focus and
appearance persistence; not executed here.

## Remaining acceptance

- Full Qt, session and release suite on the stacked checkout.
- Actual screenshots at 980x680 and 1440x900 in both appearances, verify
  navigation, long labels, layout, keyboard, scrolling, busy/error states,
  native popup and custom painter visibility.
- Startup/PSS comparison and physical display verification.
- Further information-density and inspector simplification after visual QA.

No full checkout or PySide6 available locally. No visual acceptance, merge
or release claim. Review S04 and S05 together on the unified integration branch.

See [current appearance schematics and capture instructions](DEVELOPMENT_STATUS.md).
