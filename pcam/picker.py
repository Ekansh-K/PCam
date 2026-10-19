"""Two output presets and orientation, with no dropdown or custom dimensions."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QButtonGroup
from .settings import PRESETS, validate_size


class ResolutionPicker(QWidget):
    changed = pyqtSignal()

    def __init__(self, size=(480,960), rotation=90, parent=None):
        super().__init__(parent)
        self.landscape = rotation == 0
        self.preset = 1 if sorted(size) == sorted(PRESETS[1]) else 0
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(9)
        orientations = QHBoxLayout(); orientations.setSpacing(8)
        self.orientation_group = QButtonGroup(self)
        self.portrait_button = QPushButton('  Portrait'); self.landscape_button = QPushButton('  Landscape')
        for button, landscape in ((self.portrait_button,False),(self.landscape_button,True)):
            button.setCheckable(True); self.orientation_group.addButton(button); orientations.addWidget(button)
            button.clicked.connect(lambda unused=False, value=landscape: self.set_orientation(value))
        outer.addLayout(orientations)
        presets = QHBoxLayout(); presets.setSpacing(8); self.preset_group = QButtonGroup(self); self.buttons = []
        for index in range(2):
            button = QPushButton(); button.setCheckable(True); self.preset_group.addButton(button); self.buttons.append(button)
            button.clicked.connect(lambda unused=False, value=index: self.set_preset(value)); presets.addWidget(button)
        outer.addLayout(presets); self.refresh()
        self.setToolTip('Reopen your video app camera after changing output size. Hold the phone horizontally for landscape.')

    def refresh(self):
        self.portrait_button.setChecked(not self.landscape); self.landscape_button.setChecked(self.landscape)
        for index, button in enumerate(self.buttons):
            width,height = PRESETS[index]
            if self.landscape: width,height = height,width
            button.setText(f'{width} × {height}'); button.setChecked(index == self.preset)

    def output_size(self):
        width,height = PRESETS[self.preset]
        return (height,width) if self.landscape else (width,height)

    def rotation(self): return 0 if self.landscape else 90

    def set_orientation(self, landscape):
        self.landscape = landscape; self.refresh(); self.changed.emit()

    def set_preset(self, index):
        self.preset = index; self.refresh(); self.changed.emit()

    def set_output(self, size):
        validate_size(size)
        self.landscape = size[0] > size[1]; self.preset = 1 if sorted(size) == sorted(PRESETS[1]) else 0
        self.refresh(); self.changed.emit()
