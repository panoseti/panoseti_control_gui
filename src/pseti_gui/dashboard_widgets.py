"""Presentation components for the observatory control dashboard."""
from html import escape
from importlib.metadata import version
from time import monotonic

from PyQt6.QtCore import QDateTime, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QFrame, QLabel, QSizePolicy, QWidget, QSplitter, QHBoxLayout, QGridLayout


class ImageDashboardSplitter(QSplitter):
    """Start with square images, then respect the user's draggable panel split."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fit_pending = False
        self._manually_adjusted = False
        self.splitterMoved.connect(self._remember_manual_split)

    def _remember_manual_split(self, position, index):
        self._manually_adjusted = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.handle(1).setCursor(Qt.CursorShape.SplitHCursor)
        self.handle(1).setToolTip('Drag to resize the camera and control panels.')
        self._schedule_fit()

    def _schedule_fit(self):
        if not self._manually_adjusted and not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._fit_images)

    def _fit_images(self):
        self._fit_pending = False
        if self._manually_adjusted:
            return
        panel = self.findChild(QFrame, 'camera_panel')
        content = self.findChild(QWidget, 'camera_content')
        if panel is None or content is None or not content.layout().count():
            return
        panel.layout().activate()
        content.layout().activate()
        grid = content.layout().itemAt(0).widget()
        desired = panel.width() + grid.height() - grid.width()
        # Use actual panel space: the styled handle can be wider than handleWidth().
        available = sum(self.sizes())
        right_min = self.widget(1).minimumSizeHint().width()
        left = min(max(260, desired), available - right_min)
        self.setSizes([left, available - left])


class CameraPlaceholder(QLabel):
    """Keep the configured module title visible before streaming starts."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setScaledContents(True)
        self.caption = QLabel(title, self)
        self.caption.setStyleSheet(
            'background: rgba(20, 30, 45, 190); color: white; '
            'border-radius: 4px; padding: 4px 8px; font-weight: 600;'
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.caption.setFixedWidth(min(self.caption.sizeHint().width(), max(1, self.width() - 16)))
        self.caption.adjustSize()
        self.caption.move(8, max(0, self.height() - self.caption.height() - 8))


class PowerSwitch(QAbstractButton):
    """Keyboard-accessible, checkable power command switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self):
        return QSize(58, 32)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = '#459b64' if self.isChecked() else '#8b96a8'
        if not self.isEnabled():
            color = '#b4bdca'
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(2, 3, 54, 26), 13, 13)
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(QRectF(32 if self.isChecked() else 6, 6, 20, 20))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#3468c9'), 2))
            painter.drawRoundedRect(QRectF(1, 1, 56, 30), 15, 15)


class StatusIndicator(QLabel):
    """Display-only status; callers supply observations through set_status()."""

    COLORS = {
        'unknown': '#8b96a8',
        'idle': '#8b96a8',
        'running': '#459b64',
        'warning': '#bc8224',
        'error': '#cc5555',
    }

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setContentsMargins(10, 0, 8, 0)
        self.set_status('unknown')

    def set_status(self, state: str, detail: str | None = None) -> None:
        if state not in self.COLORS:
            raise ValueError(f'Unsupported status: {state}')
        label = detail if detail is not None else state.title()
        self.setText(
            f'<span style="color:{self.COLORS[state]};">●</span>'
            f' &nbsp;{escape(self.title)}: {escape(label)}'
        )
        self.setAccessibleName(f'{self.title}: {label}')
        self.setToolTip('Status monitoring is not connected.' if state == 'unknown' else label)


STYLE = """
QMainWindow { background: #eef2f7; }
QWidget { color: #202f4f; font-size: 13px; }
QWidget#centralwidget, QWidget#control_panel, QWidget#command_groups {
    background: #eef2f7;
}
QFrame#camera_panel, QFrame#header_panel, QFrame#logs_panel {
    background: #f8fafd; border: 1px solid #d6dfeb; border-radius: 8px;
}
QLabel { background: transparent; border: none; }
QLabel#app_heading { font-size: 24px; font-weight: 700; color: #14234b; }
QLabel#camera_heading, QLabel#logs_heading {
    font-size: 18px; font-weight: 700; color: #14234b; padding: 6px 2px;
}
QLabel#clock_label { color: #758198; font-size: 12px; }
QLabel#clock_label { padding: 0 8px; }
QLabel#power_label { font-weight: 600; }
QLabel#power_state_label { color: #65748b; min-width: 66px; }
QFrame#initialization_panel { background: #edf7f2; border: 1px solid #d2e9dd; border-radius: 7px; }
QFrame#configuration_panel { background: #edf3ff; border: 1px solid #d2e0fa; border-radius: 7px; }
QFrame#daq_panel { background: #f2effb; border: 1px solid #ded7f2; border-radius: 7px; }
QFrame#visualization_panel { background: #fcf5ed; border: 1px solid #f0dfc9; border-radius: 7px; }
QFrame#transfer_panel { background: #edf7fa; border: 1px solid #d0e6ed; border-radius: 7px; }
QLabel#initialization_heading, QLabel#configuration_heading, QLabel#daq_heading,
QLabel#visualization_heading, QLabel#transfer_heading {
    font-size: 14px; font-weight: 700; padding: 6px 0 8px 0;
}
QLabel#initialization_heading { color: #347c58; }
QLabel#configuration_heading { color: #345ea6; }
QLabel#daq_heading { color: #7751ac; }
QLabel#visualization_heading { color: #95652e; }
QLabel#transfer_heading { color: #347b91; }
QPushButton {
    background: #ffffff; border: 1px solid #d3dce8; border-radius: 5px;
    padding: 6px 6px; font-weight: 600; min-height: 18px;
}
QPushButton:hover { background: #e8effa; border-color: #99b2da; }
QPushButton:pressed { background: #d6e3f6; }
QPushButton:focus, QComboBox:focus { border: 2px solid #3468c9; }
QPushButton[role="start"] { background: #d3eedb; border-color: #bcdfc8; color: #25543a; }
QPushButton[role="start"]:hover { background: #bce2c8; }
QPushButton[role="stop"] { background: #f5d4d1; border-color: #e9bdb9; color: #793c3b; }
QPushButton[role="stop"]:hover { background: #eebcb7; }
QPushButton[role="secondary"] { background: #d5e4fc; border-color: #b9cef2; color: #304d7c; }
QPushButton[role="secondary"]:hover { background: #bfd5f7; }
QPushButton:disabled { background: #e9edf2; color: #929dac; border-color: #dce2ea; }
QPushButton#clear_logs { min-width: 64px; padding: 5px 12px; }
QComboBox { background: white; border: 1px solid #d3dce8; border-radius: 4px; padding: 8px; min-width: 78px; }
QComboBox#visualization_mode { min-width: 96px; padding: 6px; }
QComboBox:disabled { color: #929dac; background: #e9edf2; }
QComboBox QAbstractItemView { background: white; color: #202f4f; selection-background-color: #d5e4fc; }
QTextEdit#console_output { background: #ffffff; color: #24334c; border: 1px solid #dce3ed; border-radius: 4px; padding: 8px; }
QSplitter::handle { background: #dce4ef; border-radius: 3px; margin: 4px 2px; }
QSplitter::handle:hover { background: #99b2da; }
QStatusBar { background: #eef2f7; border-top: 1px solid #d6dfeb; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: #65748b; font-size: 12px; }
"""


def configure_dashboard(window) -> None:
    window.setStyleSheet(STYLE)
    window.main_splitter.setSizes([795, 817])
    window.main_splitter.setStretchFactor(0, 1)
    window.main_splitter.setStretchFactor(1, 1)
    window.camera_panel.setMinimumWidth(260)
    window.visualization_mode.addItem('PH1024', 'ph1024')
    window.visualization_mode.addItem('MOVIE16', 'mov16')
    window.visualization_mode.addItem('MOVIE8', 'mov8')
    # Never wrap controls just to maintain a larger camera area -- each panel
    # keeps its content-driven width as a floor (setMinimumWidth, not
    # setFixedWidth) and gets a stretch factor proportional to that same
    # width, so on a wide window the row grows to fill it with every panel
    # widened by the same ratio rather than sitting left-packed with a big
    # gap on the right; on a narrow window the floor still applies and the
    # camera cells shrink instead of the controls wrapping.
    panels = [getattr(window, name + '_panel') for name in
              ('initialization', 'configuration', 'daq', 'visualization', 'transfer')]
    preferred_widths = {'initialization_panel': 116, 'visualization_panel': 176}
    computed_widths = []
    for panel in panels:
        panel.ensurePolished()
        for child in panel.findChildren(QWidget):
            child.ensurePolished()
        width = max(preferred_widths.get(panel.objectName(), 136), panel.minimumSizeHint().width())
        panel.setMinimumWidth(width)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        computed_widths.append(width)
    for index, width in enumerate(computed_widths):
        window.groups_layout.setStretch(index, width)
    window.groups_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    window.command_groups.setMinimumWidth(
        sum(computed_widths) + 4 * window.groups_layout.spacing()
    )
    window.command_groups.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    window.stop_grpc.setEnabled(False)
    window.start_interleave.setToolTip('Interleave command is not configured yet.')
    window.clear_logs.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    window.clear_logs.clicked.connect(window.console_output.clear)
    window.statusbar.setSizeGripEnabled(True)
    status_content = QWidget(window.statusbar)
    status_layout = QGridLayout(status_content)
    status_layout.setContentsMargins(0, 0, 0, 0)
    status_layout.setHorizontalSpacing(8)
    window.session_label = QLabel(status_content)
    window.clock_label = QLabel(status_content)
    window.clock_label.setObjectName('clock_label')
    window.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    indicators = QWidget(status_content)
    indicator_layout = QHBoxLayout(indicators)
    indicator_layout.setContentsMargins(0, 0, 0, 0)
    indicator_layout.setSpacing(0)
    status_layout.addWidget(window.session_label, 0, 0)
    status_layout.addWidget(window.clock_label, 0, 1)
    status_layout.addWidget(indicators, 0, 2, Qt.AlignmentFlag.AlignRight)
    status_layout.setColumnStretch(0, 1)
    status_layout.setColumnStretch(2, 1)
    window.statusbar.addPermanentWidget(status_content, 1)
    window.status_indicators = {}
    for key, title in [('cameras', 'Cameras'), ('daq', 'DAQ'),
                       ('visualization', 'Visualization'), ('transfer', 'Transfer')]:
        indicator = StatusIndicator(title, window)
        window.status_indicators[key] = indicator
        indicator_layout.addWidget(indicator)
    start = monotonic()

    def update_clock():
        window.clock_label.setText(QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss'))
        minutes = int(monotonic() - start) // 60
        window.session_label.setText(
            f'  PANOSETI Control v{version("pseti-gui")}    |    Uptime: {minutes // 60}h {minutes % 60}m'
        )

    window.clock_timer = QTimer(window)
    window.clock_timer.timeout.connect(update_clock)
    window.clock_timer.start(1000)
    update_clock()
    # Equal side columns keep the time centered despite different text lengths.
    side_width = max(window.session_label.sizeHint().width(), indicators.sizeHint().width())
    status_layout.setColumnMinimumWidth(0, side_width)
    status_layout.setColumnMinimumWidth(2, side_width)
