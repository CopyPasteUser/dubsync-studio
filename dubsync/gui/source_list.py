"""Native drag-and-drop source list, independent of the main window."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QListWidget


class SourceDropList(QListWidget):
    """Accessible source list that accepts local folders/videos via drag-and-drop."""
    pathsDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setToolTip("Ziehe Medienordner oder einzelne Videodateien hier hinein. NAS-Mounts werden unterstützt.")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        values = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if values:
            self.pathsDropped.emit(values)
            event.acceptProposedAction()
            return
        event.ignore()
