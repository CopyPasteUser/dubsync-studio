"""Canonical Qt theme. DESIGN.md documents roles; all surfaces consume these tokens."""
from string import Template

TOKENS = {
    "canvas": "#17191c", "surface": "#202327", "raised": "#292d32",
    "border": "#424950", "text": "#eef0f2", "muted": "#adb5be",
    "accent": "#a8dfd3", "ink": "#142c29", "selection": "#344d49",
    "warning": "#efc586", "danger": "#f2a5a5", "success": "#a8dfb2",
    "thumb": "#68747e", "hover": "#363d43", "disabled": "#858c94",
}


def studio_stylesheet(high_contrast: bool = False) -> str:
    colors = dict(TOKENS)
    if high_contrast:
        colors.update(canvas="#080a0b", surface="#111416", border="#adb5be",
                      text="#ffffff", muted="#e2e6ea", thumb="#adb5be")
    return Template('''
    QWidget { color: $text; font-family: "Noto Sans", "Segoe UI", sans-serif; font-size: 13px; }
    QMainWindow, QDialog { background: $canvas; }
    QMainWindow > QWidget, QDialog > QWidget, QStackedWidget, QScrollArea > QWidget > QWidget { background: $canvas; }
    QLabel { background: transparent; }
    QFrame#panel, QFrame#outputPanel, QFrame#heroCard, QFrame#sourcePanel, QFrame#reviewPanel, QFrame#queueRoom { background: $surface; border: 1px solid $border; border-radius: 8px; }
    QFrame#previewFrame { background: $canvas; border: 1px solid $border; border-radius: 5px; }
    QLabel#previewImage { background: $canvas; color: $muted; padding: 3px; }
    QLabel#reviewVerdict { background: $raised; border-left: 3px solid $border; border-radius: 4px; color: $muted; padding: 8px 9px; }
    QLabel#reviewVerdict[state="safe"] { border-left-color: $success; color: $text; }
    QLabel#reviewVerdict[state="unsafe"] { border-left-color: $warning; color: $warning; }
    QFrame#navigation, QFrame#jobDock { background: $surface; border: 1px solid $border; border-radius: 8px; }
    QFrame#workflowStrip { background: $surface; border: none; border-radius: 8px; }
    QFrame#sourceRow { background: $surface; border: 1px solid $border; border-left: 3px solid $accent; border-radius: 6px; }
    QFrame#pageHeader { background: transparent; border: none; }
    #brandTitle { font-size: 21px; font-weight: 700; }
    #pageTitle { font-size: 23px; font-weight: 700; }
    #sectionTitle { font-size: 16px; font-weight: 650; }
    #heroNumber { font-size: 26px; font-weight: 650; }
    #sourceName { font-weight: 650; }
    #mutedText, #statusMuted { color: $muted; }
    #emptyState { color: $muted; padding: 28px; font-size: 14px; }
    #eyebrow, #workflowStep, #outputHeading { color: $muted; font-size: 11px; font-weight: 650; }
    #statusChip { color: $muted; border: 1px solid $border; border-radius: 5px; padding: 6px 10px; font-size: 11px; }
    #releaseBadge { background: $selection; color: $accent; border: 1px solid $border; border-radius: 5px; padding: 6px 10px; font-size: 11px; font-weight: 700; }
    #dropHint { color: $muted; padding: 8px 12px; }
    #pathPreview { color: $accent; background: $canvas; border-left: 2px solid $accent; padding: 10px; }
    #progressDetail { color: $muted; font-size: 11px; }
    QPushButton, QToolButton { background: $raised; color: $text; border: 1px solid $border; border-radius: 5px; padding: 8px 12px; min-height: 20px; }
    QPushButton:hover, QToolButton:hover { background: $hover; border-color: $muted; }
    QPushButton:pressed, QToolButton:pressed { background: $selection; }
    QPushButton#primaryButton { background: $accent; color: $ink; border-color: $accent; font-weight: 650; }
    QPushButton#primaryButton:hover { background: $text; border-color: $text; }
    QPushButton#primaryButton:pressed { background: $muted; }
    QPushButton#navButton { background: transparent; border-color: transparent; color: $muted; }
    QPushButton#navButton:hover { background: $raised; color: $text; }
    QPushButton#navButton:checked { background: $selection; color: $text; border-bottom: 2px solid $accent; }
    QPushButton#cancelButton:enabled { color: $danger; border-color: $danger; background: transparent; }
    QPushButton#cancelButton:enabled:hover { background: $raised; }
    QPushButton:disabled, QPushButton#primaryButton:disabled, QPushButton#secondaryButton:disabled, QToolButton:disabled { background: $surface; color: $disabled; border-color: $border; }
    QPushButton:focus, QToolButton:focus, QPushButton#primaryButton:focus, QPushButton#navButton:focus, QPushButton#cancelButton:focus { border: 2px solid $text; padding: 7px 11px; }
    QPushButton#iconButton { padding: 4px 8px; min-width: 24px; max-width: 30px; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: $canvas; color: $text; border: 1px solid $border; border-radius: 5px; padding: 6px 8px; min-height: 22px; selection-background-color: $selection; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: $disabled; background: $surface; }
    QComboBox QAbstractItemView { background: $raised; color: $text; selection-background-color: $selection; border: 1px solid $border; padding: 4px; }
    QListWidget, QTableWidget, QTextEdit { background: $canvas; border: 1px solid $border; border-radius: 5px; selection-background-color: $selection; selection-color: $text; }
    QListWidget::item { padding: 7px 10px; }
    QListWidget::item:selected, QTableWidget::item:selected { background: $selection; color: $text; }
    QListWidget::item:hover:!selected, QTableWidget::item:hover:!selected { background: $raised; }
    QTableWidget::item { padding: 8px 6px; border-bottom: 1px solid $surface; }
    QHeaderView::section { background: $raised; color: $muted; border: none; border-bottom: 1px solid $border; padding: 9px 6px; font-weight: 600; }
    QTableWidget:focus, QListWidget:focus, QTextEdit:focus { border-color: $accent; }
    QScrollArea, QScrollArea#pageScroll { background: transparent; border: none; }
    QCheckBox, QRadioButton { spacing: 8px; }
    QCheckBox:focus, QRadioButton:focus { color: $accent; }
    QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
    QCheckBox::indicator:unchecked { background: $canvas; border: 1px solid $muted; border-radius: 3px; }
    QCheckBox::indicator:checked { background: $accent; border: 2px solid $ink; border-radius: 3px; }
    QRadioButton::indicator:unchecked { background: $canvas; border: 1px solid $muted; border-radius: 8px; }
    QRadioButton::indicator:checked { background: $accent; border: 4px solid $selection; border-radius: 8px; }
    QProgressBar { background: $canvas; color: $text; border: 1px solid $border; border-radius: 4px; min-height: 20px; text-align: center; }
    QProgressBar::chunk { background: $selection; border-radius: 3px; }
    QFrame#analysisInsight { background: $surface; border: 1px solid $border; border-left: 3px solid $accent; border-radius: 5px; }
    QFrame#analysisInsight[state="unsafe"], QFrame#analysisInsight[state="cancelled"] { border-left-color: $warning; }
    QFrame#analysisInsight[state="error"] { border-left-color: $danger; }
    #insightTitle { font-weight: 650; } #insightText { color: $muted; }
    #statusGood { color: $success; } #statusWarn { color: $warning; }
    #logView { font-family: "Noto Sans Mono", "DejaVu Sans Mono", monospace; font-size: 12px; padding: 12px; }
    QTabWidget::pane { background: $surface; border: 1px solid $border; }
    QTabBar::tab { background: $surface; color: $muted; padding: 9px 14px; }
    QTabBar::tab:selected { background: $selection; color: $text; }
    QGroupBox { border: 1px solid $border; border-radius: 6px; margin-top: 14px; padding-top: 14px; }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; }
    QScrollBar:vertical { background: $canvas; width: 12px; margin: 0; }
    QScrollBar:horizontal { background: $canvas; height: 12px; margin: 0; }
    QScrollBar::handle:vertical { background: $thumb; min-height: 28px; border-radius: 4px; }
    QScrollBar::handle:horizontal { background: $thumb; min-width: 28px; border-radius: 4px; }
    QScrollBar::handle:hover { background: $muted; }
    QScrollBar::handle:pressed { background: $accent; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: $canvas; }
    QSplitter::handle { background: $canvas; }
    QSplitter::handle:hover { background: $border; }
    QMenuBar, QStatusBar { background: $canvas; color: $muted; border: none; }
    QMenuBar::item { padding: 6px 12px; background: transparent; }
    QMenu { background: $raised; border: 1px solid $border; padding: 5px; }
    QMenu::item { padding: 7px 24px; }
    QMenuBar::item:selected, QMenu::item:selected { background: $selection; }
    QToolTip { color: $text; background: $raised; border: 1px solid $border; padding: 6px; }
    ''').substitute(colors)
