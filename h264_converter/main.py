import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gui import MainWindow


def main():
    # Enable High DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("H264 to MP4 Converter")
    app.setOrganizationName("Antigravity")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
