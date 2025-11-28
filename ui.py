import logging
from PySide6.QtCore import (
    Slot,
    Signal,
    QThread,
    Qt,
    QCoreApplication,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QObject,
    QModelIndex,
    QPersistentModelIndex,
    QSize,
    QEvent,
)

from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QAbstractItemView,
    QApplication,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QTextEdit,
    QTableWidget,
    QTabWidget,
)

from recipeUi import RecipeWindow

_logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.recipe_window = RecipeWindow(self)
        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(self.recipe_window)
        self.setCentralWidget(self.recipe_window)
        # self.setMinimumSize(QSize(1000, 600))
        self.resize(QSize(1000, 600))

    def closeEvent(self, event: QEvent) -> None:
        _logger.debug("MainWindow closeEvent called.")
        # self.recipe_window.close()
        for child in self.findChildren(QWidget):
            child.closeEvent(event)
        return super().closeEvent(event)
