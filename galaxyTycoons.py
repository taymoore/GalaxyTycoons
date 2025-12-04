import logging
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from api.gameData import get_gamedata, save_gamedata
from api.exchange import Exchange
from ui import MainWindow

_logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    Exchange.load_cache()
    Exchange.update_listings()

    app = QApplication([])

    main_window = MainWindow()
    main_window.show()

    app.exec()

    save_gamedata()
    Exchange.close()
