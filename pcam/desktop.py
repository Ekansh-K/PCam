"""Native PCam desktop controls. The camera receiver stays in its own process."""
import json
import os
from pathlib import Path
import pwd
import re
import sys
import time
from PyQt5.QtCore import (Qt, QTimer, QThread, pyqtSignal, QProcess, QPropertyAnimation,
                         QEasingCurve, QRectF, QSettings, QSize)
from PyQt5.QtGui import QFont, QFontDatabase, QIcon, QPainter, QColor, QPen, QPixmap, QImage
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QFrame, QListWidget, QListWidgetItem, QAbstractItemView,
                            QGraphicsOpacityEffect, QProgressBar, QStackedWidget)
from . import discovery
from .paths import ROOT, STATE
from .settings import write_size
from .picker import ResolutionPicker
from .pairing import qr_pixels


STYLE = '''
QWidget { color:#f0f0f2; font-size:12px; }
QWidget#window { background:#080809; border:1px solid #29292d; border-radius:18px; }
QLabel { background:transparent; border:0; }
QLabel#muted { color:#85858d; }
QLabel#eyebrow { color:#777780; font-size:10px; letter-spacing:2px; }
QLabel#section { font-size:14px; font-weight:500; }
QLabel#bigStatus { font-size:18px; }
QLabel#caption { color:#74747e; font-size:10px; }
QFrame#panel { background:#101012; border:1px solid #242428; border-radius:16px; }
QFrame#divider { background:#252529; max-height:1px; border:0; }
QPushButton { background:#17171b; border:1px solid #2a2a30; border-radius:10px; padding:12px 17px; color:#a4a4ad; }
QPushButton:hover { background:#222227; border-color:#414149; color:#fff; }
QPushButton:pressed { background:#303036; }
QPushButton:checked { background:#ededf0; color:#101013; border-color:#ededf0; }
QPushButton:disabled { color:#51515b; border-color:#252529; background:#17171b; }
QPushButton:checked:disabled { color:#dbdbdf; border-color:#515159; background:#28282e; }
QPushButton#primary { color:#101013; background:#ededf0; border:0; font-weight:600; padding:15px; }
QPushButton#primary:hover { background:#fff; }
QPushButton#primary:pressed { background:#cacacf; }
QPushButton#primary:disabled { color:#65656e; background:#28282e; }
QPushButton#icon { border:0; background:transparent; padding:5px; border-radius:6px; }
QPushButton#icon:hover { background:#27272d; color:#fff; }
QPushButton#close:hover { background:#4b262b; color:#fff; }
QPushButton#close { border:0; background:transparent; padding:5px; border-radius:6px; }
QListWidget { background:transparent; border:0; outline:0; padding:0; }
QListWidget::item { background:#161619; border:1px solid #2c2c31; border-radius:11px; padding:15px; margin:0 0 9px 0; }
QListWidget::item:hover { background:#202025; }
QListWidget::item:selected { background:#24242a; border-color:#777780; color:#fff; }
QProgressBar { background:#202025; border:0; border-radius:2px; max-height:3px; color:transparent; }
QProgressBar::chunk { background:#a3a3af; border-radius:2px; }
'''


class Job(QThread):
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    def __init__(self, action, parent): super().__init__(parent); self.action = action
    def run(self):
        try: self.result.emit(self.action())
        except Exception as error: self.failed.emit(str(error))


class StatusLight(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedSize(16, 16); self.phase = 0; self.active = False
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(70)
    def tick(self): self.phase = (self.phase + 1) % 40; self.update()
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        color = QColor('#a9d7bf' if self.active else '#696972')
        if self.active:
            halo = QColor(color); halo.setAlpha(25 + abs(self.phase - 20) * 2)
            painter.setPen(Qt.NoPen); painter.setBrush(halo); painter.drawEllipse(1, 1, 14, 14)
        painter.setBrush(color); painter.setPen(Qt.NoPen); painter.drawEllipse(5, 5, 6, 6)



def label(text, name='', wrap=False):
    value = QLabel(text); value.setTextFormat(Qt.PlainText); value.setObjectName(name); value.setWordWrap(wrap); return value


def control_icon(kind):
    icon = QIcon()
    for mode, state, color in [(QIcon.Normal, QIcon.Off, '#a4a4ad'),
                               (QIcon.Active, QIcon.Off, '#ffffff'),
                               (QIcon.Normal, QIcon.On, '#101013'),
                               (QIcon.Disabled, QIcon.Off, '#51515b'),
                               (QIcon.Disabled, QIcon.On, '#dbdbdf')]:
        canvas = QPixmap(48, 48); canvas.fill(Qt.transparent); canvas.setDevicePixelRatio(2)
        p = QPainter(canvas); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(color), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if kind == 'wifi':
            for rect in (QRectF(0, 6, 24, 20), QRectF(4, 10, 16, 16), QRectF(8, 14, 8, 10)):
                p.drawArc(rect, 40 * 16, 100 * 16)
            p.setBrush(QColor(color)); p.setPen(Qt.NoPen); p.drawEllipse(QRectF(10.8, 20, 2.4, 2.4))
        elif kind == 'direct':
            p.drawLine(3, 7, 21, 7); p.drawLine(17, 3, 21, 7); p.drawLine(17, 11, 21, 7)
            p.drawLine(3, 17, 21, 17); p.drawLine(3, 17, 7, 13); p.drawLine(3, 17, 7, 21)
        elif kind == 'minimize': p.drawLine(4, 12, 20, 12)
        elif kind == 'portrait': p.drawRoundedRect(QRectF(6,2,12,20),2,2)
        elif kind == 'landscape': p.drawRoundedRect(QRectF(2,6,20,12),2,2)
        elif kind == 'close': p.drawLine(5, 5, 19, 19); p.drawLine(5, 19, 19, 5)
        elif kind == 'scan':
            p.drawArc(QRectF(4, 4, 16, 16), 45 * 16, 285 * 16)
            p.drawLine(18, 3, 18, 8); p.drawLine(13, 8, 18, 8)
        p.end(); icon.addPixmap(canvas, mode, state)
    return icon


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('PCam'); self.setObjectName('window')
        self.setWindowIcon(QIcon(str(ROOT / 'assets/pcam.svg')))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground); self.resize(920, 620); self.setMinimumSize(860, 590)
        self.mode = 'direct'; self.devices = []; self.jobs = []; self.process = None
        self.connecting = False; self.stopping = False; self.closing = False; self.drag = None
        self.started_at = 0; self.output = ''; self.private_file = None
        self.camera_settings_file = None
        self.settings = QSettings('PCam', 'Desktop')
        self.build_ui()
        self.poll = QTimer(self); self.poll.timeout.connect(self.read_status); self.poll.start(700)
        QTimer.singleShot(250, lambda: self.set_mode(self.settings.value('mode', 'shared')))

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#080809')); painter.setPen(QPen(QColor('#29292d'), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 18, 18)

    def build_ui(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(28, 16, 28, 22); outer.setSpacing(0)
        top = QHBoxLayout(); top.setSpacing(10)
        logo = label('\U000f0100'); logo.setStyleSheet('font-size:25px;'); top.addWidget(logo)
        brand = label('PCam'); brand.setStyleSheet('font-size:16px; font-weight:600;'); top.addWidget(brand)
        top.addStretch()
        for kind, tip, action, name in [('minimize', 'Minimize', self.showMinimized, 'icon'), ('close', 'Close PCam', self.close, 'close')]:
            button = QPushButton(); button.setIcon(control_icon(kind)); button.setIconSize(QSize(15, 15))
            button.setObjectName(name); button.setToolTip(tip); button.setAccessibleName(tip); button.setFixedSize(42, 38)
            button.clicked.connect(action); top.addWidget(button)
        outer.addLayout(top); outer.addSpacing(24)
        modes = QHBoxLayout(); modes.setSpacing(10)
        self.direct_button = QPushButton('  Wi-Fi Direct'); self.direct_button.setCheckable(True)
        self.direct_button.setIcon(control_icon('direct')); self.direct_button.setIconSize(QSize(15, 15))
        self.direct_button.setObjectName('directMode'); self.direct_button.setChecked(True)
        self.lan_button = QPushButton('  Shared Wi-Fi'); self.lan_button.setCheckable(True); self.lan_button.setObjectName('sharedMode')
        self.lan_button.setIcon(control_icon('wifi')); self.lan_button.setIconSize(QSize(15, 15))
        self.direct_button.clicked.connect(lambda: self.set_mode('direct'))
        self.lan_button.clicked.connect(lambda: self.set_mode('shared'))
        modes.addWidget(self.direct_button); modes.addWidget(self.lan_button); modes.addStretch(); outer.addLayout(modes)
        outer.addSpacing(20)
        columns = QHBoxLayout(); columns.setSpacing(18)
        left = QFrame(); left.setObjectName('panel'); left.setMinimumWidth(300)
        ll = QVBoxLayout(left); ll.setContentsMargins(22, 21, 22, 20); ll.setSpacing(14)
        row = QHBoxLayout(); row.addWidget(label('Devices', 'section')); row.addStretch()
        self.scan_button = QPushButton(); self.scan_button.setIcon(control_icon('scan')); self.scan_button.setIconSize(QSize(15, 15))
        self.scan_button.setObjectName('icon'); self.scan_button.setToolTip('Scan for cameras'); self.scan_button.setAccessibleName('Scan for cameras')
        self.scan_button.setFixedSize(38, 38); self.scan_button.clicked.connect(self.scan); row.addWidget(self.scan_button); ll.addLayout(row)
        self.device_list = QListWidget(); self.device_list.setObjectName('cameras')
        self.device_list.setSelectionMode(QAbstractItemView.SingleSelection); self.device_list.currentRowChanged.connect(self.selection_changed)
        ll.addWidget(self.device_list, 1)
        self.empty = label('No devices found', 'muted', True)
        self.empty.setAlignment(Qt.AlignCenter); ll.addWidget(self.empty, 1)
        self.left_stack = QStackedWidget(); self.devices_panel = left; self.left_stack.addWidget(left)
        self.qr_panel = QFrame(); self.qr_panel.setObjectName('panel')
        ql = QVBoxLayout(self.qr_panel); ql.setContentsMargins(18,21,18,20)
        ql.addWidget(label('Pairing', 'section'))
        self.qr_label = label('Show QR to pair', 'muted'); self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setAccessibleName('Wi-Fi Direct pairing QR'); ql.addWidget(self.qr_label,1)
        self.left_stack.addWidget(self.qr_panel); columns.addWidget(self.left_stack, 4)
        right = QFrame(); right.setObjectName('panel'); rr = QVBoxLayout(right); rr.setContentsMargins(26, 21, 26, 23)
        status_row = QHBoxLayout(); self.light = StatusLight(); status_row.addWidget(self.light)
        self.status_label = label('NOT CONNECTED', 'eyebrow'); status_row.addWidget(self.status_label); status_row.addStretch()
        rr.addLayout(status_row); rr.addStretch()
        self.headline = label('', 'bigStatus'); self.headline.setAlignment(Qt.AlignCenter); rr.addWidget(self.headline)
        self.detail = label('', 'muted', True)
        self.detail.setAlignment(Qt.AlignCenter); rr.addWidget(self.detail)
        rr.addSpacing(12)
        rr.addSpacing(18); rr.addWidget(label('Output', 'muted')); rr.addSpacing(10)
        saved = self.settings.value('resolution','480x960')
        size = tuple(map(int,saved.split('x'))) if saved in ('480x960','720x1600','960x480','1600x720') else (480,960)
        self.resolution = ResolutionPicker(size, 90 if size[1] > size[0] else 0)
        for button, kind in ((self.resolution.portrait_button,'portrait'),(self.resolution.landscape_button,'landscape')):
            button.setIcon(control_icon(kind)); button.setIconSize(QSize(15,15))
        self.resolution.changed.connect(self.resolution_changed); rr.addWidget(self.resolution)
        rr.addStretch(); self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.setRange(0, 0); self.progress.hide(); rr.addWidget(self.progress)
        rr.addSpacing(14)
        self.connect_button = QPushButton('Connect camera   \uf061'); self.connect_button.setObjectName('primary')
        self.connect_button.setMinimumHeight(48); self.connect_button.clicked.connect(self.connect_or_stop); rr.addWidget(self.connect_button)
        columns.addWidget(right, 6); outer.addLayout(columns, 1); outer.addSpacing(18)
        bottom = QHBoxLayout(); self.format_label = label('', 'caption'); bottom.addWidget(self.format_label)
        bottom.addStretch(); self.rate_label = label('', 'caption'); bottom.addWidget(self.rate_label); outer.addLayout(bottom)
        self.setStyleSheet(STYLE)
        self.connect_button.setEnabled(False)

    def animate_panel(self):
        effect = QGraphicsOpacityEffect(self.device_list); self.device_list.setGraphicsEffect(effect)
        self.fade = QPropertyAnimation(effect, b'opacity', self); self.fade.setDuration(230)
        self.fade.setStartValue(.25); self.fade.setEndValue(1); self.fade.setEasingCurve(QEasingCurve.OutCubic); self.fade.start()

    def job(self, action, success):
        task = Job(action, self); self.jobs.append(task)
        task.result.connect(success); task.failed.connect(self.show_error)
        task.finished.connect(lambda: self.jobs.remove(task)); task.finished.connect(task.deleteLater); task.start()

    def set_mode(self, mode):
        if self.process or self.connecting: return
        mode = mode if mode in ('direct', 'shared') else 'shared'
        self.settings.setValue('mode', mode)
        self.mode = mode; self.direct_button.setChecked(mode == 'direct'); self.lan_button.setChecked(mode == 'shared')
        self.left_stack.setCurrentWidget(self.qr_panel if mode == 'direct' else self.devices_panel)
        self.left_stack.show()
        self.headline.clear(); self.detail.clear()
        self.status_label.setText('NOT CONNECTED')
        self.connect_button.setText('Show pairing QR' if mode == 'direct' else 'Connect camera   \uf061')
        if mode == 'direct':
            self.qr_label.clear(); self.qr_label.setText('Show QR to pair'); self.connect_button.setEnabled(True)
        else: self.scan()

    def scan(self):
        if self.mode == 'direct': return
        if self.process or self.connecting or any(job.isRunning() for job in self.jobs): return
        self.direct_button.setEnabled(False); self.lan_button.setEnabled(False)
        self.scan_button.setEnabled(False); self.device_list.setEnabled(False); self.connect_button.setEnabled(False)
        self.empty.setText('Scanning…')
        mode = self.mode
        self.job(discovery.scan, lambda items: self.show_devices(items, mode))

    def show_devices(self, items, mode):
        self.scan_button.setEnabled(True)
        self.device_list.setEnabled(True)
        self.direct_button.setEnabled(True); self.lan_button.setEnabled(True)
        if mode != self.mode: return
        self.devices = items; self.device_list.clear()
        for item in items:
            row = QListWidgetItem('\U000f011c   ' + item['name'] + (' · In use' if item['busy'] else ''))
            row.setToolTip(item.get('phone', '')); self.device_list.addItem(row)
        self.empty.setVisible(not items); self.device_list.setVisible(bool(items))
        self.empty.setText('Open PCam on your phone')
        if items: self.device_list.setCurrentRow(0)
        self.animate_panel()

    def selection_changed(self):
        if self.mode == 'direct':
            self.connect_button.setEnabled(not self.connecting); return
        index = self.device_list.currentRow()
        selected = self.devices[index] if 0 <= index < len(self.devices) else None
        self.headline.setText(selected['name'] if selected else '')
        self.connect_button.setEnabled(bool(selected and not selected.get('busy')) and not self.connecting)

    def connect_or_stop(self):
        if self.process: self.stop(); return
        if self.mode == 'direct':
            self.connecting = True; self.set_busy(True); self.status_label.setText('PREPARING DIRECT LINK')
            self.start_backend(['pcam.direct','--desktop','--qr']); return
        index = self.device_list.currentRow()
        if not 0 <= index < len(self.devices): return
        self.connecting = True; self.set_busy(True); self.connect_button.setEnabled(False)
        selected = self.devices[index]
        self.status_label.setText('WAITING FOR APPROVAL'); self.detail.setText('Tap Connect on your phone.')
        self.job(lambda: discovery.pair(selected['phone'], selected['control_port']), self.paired)

    def paired(self, session):
        directory = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / f'pcam-{os.getuid()}'
        directory.mkdir(mode=0o700, exist_ok=True)
        self.private_file = directory / 'shared-session.json'
        fd = os.open(self.private_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle: json.dump(session, handle)
        self.start_backend(['pcam.session', '--session-file', str(self.private_file)])

    def start_backend(self, arguments):
        directory = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / f'pcam-{os.getuid()}'
        directory.mkdir(mode=0o700, exist_ok=True)
        self.camera_settings_file = directory / f'camera-settings-{os.getpid()}.json'
        write_size(self.camera_settings_file, self.resolution.output_size(), self.resolution.rotation())
        arguments += ['--settings-file', str(self.camera_settings_file)]
        self.output = ''; self.started_at = time.time(); self.stopping = False
        self.process = QProcess(self); self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.backend_output)
        self.process.finished.connect(self.backend_finished)
        self.process.errorOccurred.connect(self.backend_error)
        self.process.setWorkingDirectory(str(ROOT))
        user = pwd.getpwuid(os.getuid()).pw_name
        command = [str(ROOT / 'pcam-backend'), *arguments, '--user', user]
        if arguments[0] == 'pcam.session' and os.access('/dev/video10', os.R_OK | os.W_OK):
            self.process.start(command[0], command[1:])
        else:
            self.process.start('pkexec', command)
        self.connect_button.setText('Disconnect'); self.connect_button.setEnabled(True)

    def resolution_changed(self):
        size = self.resolution.output_size()
        self.settings.setValue('resolution', f'{size[0]}x{size[1]}')
        if self.camera_settings_file: write_size(self.camera_settings_file, size, self.resolution.rotation())

    def set_busy(self, busy):
        self.direct_button.setEnabled(not busy); self.lan_button.setEnabled(not busy)
        self.scan_button.setEnabled(not busy); self.device_list.setEnabled(not busy)
        self.progress.setVisible(busy)

    def backend_output(self):
        if not self.process: return
        text = bytes(self.process.readAllStandardOutput()).decode(errors='replace'); self.output += text
        if 'Connecting the phone' in text: self.status_label.setText('PAIRING DIRECT LINK')
        elif 'scan the pairing QR' in text: self.status_label.setText('SCAN QR ON YOUR PHONE')
        elif 'Starting' in text or 'Starting' in self.output: self.status_label.setText('STARTING CAMERA')

    def backend_error(self, error):
        if error == QProcess.FailedToStart:
            self.output += '\nPCam: Could not start the camera helper. Check that PolicyKit is installed.\n'
            self.backend_finished(1, QProcess.NormalExit)

    def backend_finished(self, code, status):
        self.backend_output()
        requested = self.stopping
        self.process.deleteLater(); self.process = None; self.connecting = False; self.stopping = False
        self.light.active = False; self.set_busy(False)
        self.connect_button.setText('Show pairing QR' if self.mode == 'direct' else 'Connect camera   \uf061'); self.selection_changed()
        if self.mode == 'direct':
            self.qr_label.clear(); self.qr_label.setText('Show QR to pair'); self.left_stack.show()
        if self.private_file: self.private_file.unlink(missing_ok=True); self.private_file = None
        if self.camera_settings_file: self.camera_settings_file.unlink(missing_ok=True); self.camera_settings_file = None
        if not requested and code:
            lines = [line.strip() for line in self.output.splitlines() if line.strip()]
            message = next((line[6:] for line in reversed(lines) if line.startswith('PCam: ')), '')
            self.show_error(message or 'Connection cancelled or authorization was not granted. Try connecting again.')
        else:
            self.status_label.setText('NOT CONNECTED'); self.detail.clear(); self.rate_label.clear(); self.format_label.clear()
        if self.closing: QTimer.singleShot(0, self.close)

    def read_status(self):
        if not self.process or self.stopping: return
        if self.mode == 'direct' and not self.light.active:
            qr_path = STATE / 'direct/qr.json'
            try:
                if qr_path.stat().st_mtime >= self.started_at and not self.qr_label.pixmap():
                    pixels = qr_pixels(json.loads(qr_path.read_text()))
                    height,width = pixels.shape
                    picture = QImage(pixels.data,width,height,pixels.strides[0],QImage.Format_Grayscale8).copy()
                    self.qr_label.setPixmap(QPixmap.fromImage(picture).scaled(300,300,Qt.KeepAspectRatio,Qt.FastTransformation))
                    self.connect_button.setText('Cancel pairing')
            except (OSError, ValueError): pass
        path = STATE / 'live-color.log'
        try:
            if path.stat().st_mtime < self.started_at: return
            if time.time() - path.stat().st_mtime > 6:
                if self.light.active:
                    self.light.active = False
                    self.status_label.setText('WAITING FOR VIDEO'); self.detail.setText('Check your phone connection.')
                    self.rate_label.clear(); self.progress.show()
                return
            with path.open('rb') as handle:
                handle.seek(max(0, path.stat().st_size - 8192)); text = handle.read().decode(errors='replace')
        except OSError: return
        dimensions = re.findall(r'(?:at |Selected )(\d+)×(\d+)', text)
        if dimensions:
            width,height = map(int,dimensions[-1]); self.format_label.setText(f'{width} × {height} · ' + ('portrait' if height > width else 'landscape'))
        fps = re.findall(r'(\d+) FPS, (\d+) recovery events', text)
        if fps and int(fps[-1][0]) > 0:
            self.connecting = False; self.light.active = True; self.progress.hide()
            self.status_label.setText('CONNECTED'); self.detail.clear()
            if self.mode == 'direct':
                self.qr_label.clear(); self.left_stack.hide(); self.connect_button.setText('Disconnect')
                try: self.headline.setText(json.loads((STATE / 'direct/session.json').read_text()).get('name','Phone'))
                except (OSError, ValueError): pass
            self.rate_label.setText(f'{fps[-1][0]} FPS')

    def show_error(self, message):
        if not self.process:
            self.connecting = False; self.set_busy(False); self.scan_button.setEnabled(True)
            self.connect_button.setText('Show pairing QR' if self.mode == 'direct' else 'Connect camera   \uf061'); self.selection_changed()
        self.status_label.setText('CONNECTION FAILED'); self.detail.setText(message[:260])

    def stop(self):
        if not self.process or self.stopping: return
        self.stopping = True; self.status_label.setText('DISCONNECTING'); self.connect_button.setEnabled(False)
        self.process.write(b'stop\n'); self.process.closeWriteChannel(); self.progress.show()

    def closeEvent(self, event):
        if self.process:
            self.closing = True; self.stop(); event.ignore(); return
        if any(job.isRunning() for job in self.jobs):
            event.ignore(); self.closing = True; QTimer.singleShot(400, self.close); return
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() < 65: self.drag = event.globalPos() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, event):
        if self.drag is not None and event.buttons() & Qt.LeftButton: self.move(event.globalPos() - self.drag)
    def mouseReleaseEvent(self, event): self.drag = None


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling); QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv); app.setApplicationName('PCam'); app.setDesktopFileName('pcam')
    font = ROOT / 'assets/PCam-NerdFont.ttf'
    if font.exists():
        families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))
        if families:
            face = QFont(families[0], 10); face.setStyleStrategy(QFont.PreferAntialias); app.setFont(face)
    else: app.setFont(QFont('JetBrains Mono', 10))
    name = f'pcam-desktop-{os.getuid()}'
    existing = QLocalSocket(); existing.connectToServer(name)
    if existing.waitForConnected(150): existing.write(b'show'); existing.waitForBytesWritten(150); return 0
    QLocalServer.removeServer(name); server = QLocalServer(); server.listen(name)
    window = Window()
    def show_existing():
        connection = server.nextPendingConnection()
        if connection: connection.close(); connection.deleteLater()
        window.showNormal(); window.raise_(); window.activateWindow()
    server.newConnection.connect(show_existing)
    window.show()
    return app.exec_()


if __name__ == '__main__': sys.exit(main())
