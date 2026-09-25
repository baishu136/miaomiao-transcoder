# -*- coding: utf-8 -*-
"""Headless smoke tests for the independent Qt interface."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import wave

from PyQt6.QtCore import QEvent, QEventLoop, QPoint, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

import qt_main


def main():
    app = QApplication.instance() or QApplication([])
    window = qt_main.MainWindow()
    window.show()
    QTest.qWait(120)

    # Real animated widgets reach their final states.
    button = qt_main.MotionButton("测试", "check")
    button.show()
    QApplication.sendEvent(button, QEvent(QEvent.Type.Enter))
    QTest.qWait(190)
    assert button.hoverAmount > .9
    QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
    QTest.qWait(190)
    assert button.hoverAmount < .1

    toggle = qt_main.ToggleSwitch("动画开关")
    toggle.show()
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    QTest.qWait(220)
    assert toggle.isChecked() and toggle.offset > .9

    # Model-driven queue, result-page fade and full reset.
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for index, size in enumerate((101, 202)):
            path = os.path.join(folder, f"track-{index}.wav")
            with open(path, "wb") as handle:
                handle.write(bytes([index + 1]) * size)
            paths.append(path)
        window._add_files(paths)
        QTest.qWait(50)
        assert window.queue_model.rowCount() == 2
        assert not window.queue_view.empty_state.isVisible()
        window.set_inspector_page(1)
        QTest.qWait(220)
        assert window.inspector_stack.currentIndex() == 1
        window._clear_everything()
        QTest.qWait(80)
        assert window.queue_model.rowCount() == 0
        assert window.parallel_combo.currentText() == "2"
        assert window.inspector_stack.currentIndex() == 0

    # The Qt batch controller drives two real FFmpeg conversions concurrently.
    with tempfile.TemporaryDirectory() as folder:
        source_dir = os.path.join(folder, "source")
        output_dir = os.path.join(folder, "output")
        os.makedirs(source_dir)
        items = []
        for index in range(2):
            source = os.path.join(source_dir, f"tone-{index}.wav")
            with wave.open(source, "wb") as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(22050)
                audio.writeframes(b"\x00\x00" * 4410)
            items.append({"id": str(index), "item": str(index), "path": source,
                          "name": os.path.basename(source), "ext": ".wav"})
        controller = qt_main.BatchController()
        completed, finish_state = [], []
        controller.fileCompleted.connect(lambda _id, path, _name, _source: completed.append(path))
        loop = QEventLoop()
        controller.finished.connect(lambda ok, errors, cancelled:
                                    (finish_state.append((ok, errors, cancelled)), loop.quit()))
        controller.start(items, {"out_dir": output_dir, "format": "mp3", "parallel": 2,
                                 "bitrate": 0, "sample_rate": 0, "channels": 0,
                                 "copy_lrc": False})
        QTimer.singleShot(30000, loop.quit)
        loop.exec()
        assert finish_state == [(2, 0, False)], finish_state
        assert len(completed) == 2 and all(os.path.isfile(path) for path in completed)

    window.close(); button.close(); toggle.close()
    app.processEvents()
    print("QT_UI_SMOKE_OK")


if __name__ == "__main__":
    main()
