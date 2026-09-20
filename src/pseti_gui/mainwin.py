from PyQt6.QtCore import QProcess, QProcessEnvironment, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel, QMainWindow
from PyQt6.QtGui import QPixmap, QTextCursor, QTextOption
from PyQt6.QtCore import QSocketNotifier

import json, os, sys
from pathlib import Path
import pyqtgraph as pg
import numpy as np
import socket

from pseti_gui.mainwin_ui import Ui_MainWindow
from pseti_gui.dashboard_widgets import CameraPlaceholder, configure_dashboard
from pseti_gui.data_config_win import DataConfigWin, DataConfigOp
from pseti_gui.window_config import load_window_config, DEFAULT_TITLE
from pseti_gui.grpc_config import load_grpc_config
from pseti_gui.square_grid import SquareGridContainer
from pseti_gui.terminal_launcher import open_terminal_with_command
from pseti_gui.ansi_html import AnsiToHtml
from pseti_gui.gui_log_file import write_console_log_line
from pseti_gui.status_check import check_power_status, check_transfer_daemon_status, grpc_process_check
import asyncio, signal
from multiprocessing import shared_memory, resource_tracker

from panoseti_grpc.telemetry.logger import get_logger

SOCK_PATH = "/tmp/panoseti_meta.sock"
FIGURE_DIR = Path(__file__).resolve().parent / "figure"


class StatusCheckThread(QThread):
    """Runs status_check.py's blocking checks off the Qt main thread.

    A short-lived, one-shot thread rather than a long-running loop: MainWin
    spins up a fresh instance on a QTimer tick (see _run_status_check()) and
    lets it finish, so a single slow/hung `pseti` invocation (or a large
    process table for grpc_process_check()) just delays that poll instead of
    ever touching the UI thread.
    """
    power_checked = pyqtSignal(int, int)
    transfer_checked = pyqtSignal(bool)
    grpc_process_checked = pyqtSignal(bool)

    def run(self):
        try:
            total, on_count = check_power_status()
        except Exception:
            pass
        else:
            self.power_checked.emit(total, on_count)
        try:
            running = check_transfer_daemon_status()
        except Exception:
            pass
        else:
            self.transfer_checked.emit(running)
        try:
            grpc_running = grpc_process_check()
        except Exception:
            pass
        else:
            self.grpc_process_checked.emit(grpc_running)
class MainWin(QMainWindow, Ui_MainWindow):
    def __init__(self):
        self.logger = get_logger('pseti_gui.mainwin', log_dir='/var/log/panoseti')
        self.logger.info('********************************************')
        self.logger.info('Main Window started.')
        self.logger.info('********************************************')
        super().__init__()
        self.setupUi(self)
        configure_dashboard(self)
        self._power_command = None
        self._power_previous_checked = False
        self._power_previous_label = "(-/-)"
        # Follow-up `pseti` argv to run once the power command itself
        # succeeds (redis daemons are started/stopped alongside power now
        # that the standalone Redis On/Off buttons are gone) -- None means
        # no follow-up is pending.
        self._power_second_command = None
        # Qt's CSS support for `word-break` is unreliable, so force wrapping
        # natively rather than relying on ansi_html.py's inline style alone --
        # otherwise a long unbroken run (e.g. a padded table row) can still
        # force a horizontal scrollbar instead of wrapping to the pane width.
        # WrapAtWordBoundaryOrAnywhere (not plain WrapAnywhere) so it wraps at
        # a space when one's available -- plain WrapAnywhere would happily
        # split a number like "8182" mid-digit just because that's where the
        # pane's edge happens to fall.
        self.console_output.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.actiondata_config.triggered.connect(self.open_data_config)
        # Both child processes' stdout/stderr are pipes, not a real terminal,
        # so their own Rich console can't query a terminal width and falls
        # back to a hardcoded 80 columns -- wrapping log lines far short of
        # this pane's actual width. Rich (via shutil.get_terminal_size())
        # honors the COLUMNS/LINES env vars before falling back, so set them
        # wide here instead of touching the panoseti/panoseti_grpc loggers.
        wide_console_env = QProcessEnvironment.systemEnvironment()
        wide_console_env.insert('COLUMNS', '200')
        wide_console_env.insert('LINES', '50')
        # Rich (used throughout panoseti/panoseti_grpc for CLI output) disables
        # its own ANSI color codes when it detects stdout isn't a real
        # terminal, which is always true for a QProcess pipe -- FORCE_COLOR
        # overrides that detection so console_output can show the same colors
        # a user would see running `pseti` directly in a terminal (see
        # append_log()/ansi_html.py, which decodes those codes back into
        # HTML for the QTextEdit).
        wide_console_env.insert('FORCE_COLOR', '1')
        self._console_html = AnsiToHtml()
        # Process for panoseti software
        self.ps_process = QProcess(self)
        self.ps_process.setProcessEnvironment(wide_console_env)
        self.ps_process.readyReadStandardOutput.connect(self.ps_stdout)
        self.ps_process.readyReadStandardError.connect(self.ps_stderr)
        self.ps_process.finished.connect(self.ps_finished)
        self.ps_process.errorOccurred.connect(self.ps_error)
        # Process for panoseti grpc
        self.grpc_process = QProcess(self)
        self.grpc_process.setProcessEnvironment(wide_console_env)
        self.grpc_process.readyReadStandardOutput.connect(self.grpc_stdout)
        self.grpc_process.readyReadStandardError.connect(self.grpc_stderr)
        self.grpc_process.finished.connect(self.grpc_finished)
        self.grpc_process.errorOccurred.connect(self.grpc_error)
        self.grpc_process_exit = True
        # set socket notifier
        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(SOCK_PATH)
        self.server.listen(1)
        self.server_notifier = QSocketNotifier(
            self.server.fileno(),
            QSocketNotifier.Type.Read
        )
        self.server_notifier.activated.connect(self._on_new_connection)
        self.conn = None
        self.conn_notifier = None
        # use shared memory to get image data
        self.shm = None
        self.shm_name = None
        self.img = None
        # window grid layout: dimensions + per-module_id (title, row, col),
        # loaded from PSETI_GUI_WINDOW_CONFIG or the packaged default -- see
        # pseti_gui/window_config.py.
        self.window_config = load_window_config()
        self.rows = self.window_config.rows
        self.cols = self.window_config.cols
        num_plots = self.rows * self.cols
        self.logger.info(
            f"Loaded window config from {self.window_config.path} "
            f"({self.rows}x{self.cols} grid, {len(self.window_config.slots)} module(s) mapped)"
        )
        # Populate the Designer layout with a configurable square image grid.
        for i in reversed(range(self.view_layout.count())):
            item = self.view_layout.takeAt(i)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.view_container = SquareGridContainer(self.rows, self.cols)
        self.view_layout.addWidget(self.view_container, 0, 0)
        # add static figure by default
        self.static_label = [None] * num_plots
        for r in range(self.rows):
            for c in range(self.cols):
                self.set_placeholder(r, c)
        self.plot_widgets = [None] * num_plots
        self.timers = [None] * num_plots
        self.imgs = [None] * num_plots
        self.qttexts = [None] * num_plots
        self.shutdown_event = None
        self.setup_signal_functions()
        self._unmapped_module_ids_warned = set()
        # Background power/transfer-daemon status polling (status_check.py):
        # a fresh StatusCheckThread per tick, skipped if the previous one is
        # still running so a slow `pseti` response can't pile up overlapping
        # subprocess calls.
        self._status_check_thread = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._run_status_check)
        self._status_timer.start()
        self._run_status_check()

    # ---------------------------------------------------------------------------
    # signal functions for socket
    # ---------------------------------------------------------------------------
    def _on_new_connection(self):
        self.server_notifier.setEnabled(False)
        self.conn, _ = self.server.accept()
        self.conn.setblocking(False)
        self.conn_notifier = QSocketNotifier(
            self.conn.fileno(),
            QSocketNotifier.Type.Read
        )
        self.conn_notifier.activated.connect(self._on_ready_read)

    def _on_ready_read(self):
        data = self.conn.recv(4096)
        if not data:
            self.conn_notifier.setEnabled(False)
            self.conn.close()
            return
        for line in data.split(b"\n"):
            if line:
                metadata = json.loads(line.decode())
        if 'shm' in metadata:
            # this is from send_shm_info
            self.shm_name = metadata['shm']
            self.logger.debug(f"shm_name is {self.shm_name}")
            self.shm = shared_memory.SharedMemory(name=self.shm_name, create=False)
            h, w = metadata['shape']
            mode = metadata['mode']
            dtype = self._get_dytpe_from_mode(mode)
            self.img = np.ndarray((h, w), dtype=dtype, buffer=self.shm.buf)
        else:
            # this is from send_images
            data = metadata
            image_array = self.img.copy()
            data['image_array'] = image_array
            self.plot_data(data)

    # ---------------------------------------------------------------------------
    # Sub Window creation
    # ---------------------------------------------------------------------------
    def open_data_config(self):
        if not hasattr(self, "data_config_win"):
            self.data_config_win = DataConfigWin()
            self.data_config_op = DataConfigOp(self.data_config_win)
        self.data_config_win.show()

    # ---------------------------------------------------------------------------
    # Set placeholder
    # ---------------------------------------------------------------------------
    def set_placeholder(self, r, c):
        i = r * self.cols + c
        pixmap = QPixmap(str(FIGURE_DIR / "placeholder.png"))
        title = next((slot.title for slot in self.window_config.slots.values()
                      if slot.row == r and slot.col == c), DEFAULT_TITLE)
        label = CameraPlaceholder(title)
        label.setPixmap(pixmap)
        # SquareGridContainer resizes this label's geometry to the current
        # square cell size; scaledContents stretches the pixmap to match.
        label.setScaledContents(True)
        self.static_label[i] = label
        self.view_container.add_widget(label, r, c)
    # ---------------------------------------------------------------------------
    # Low level APIs
    # ---------------------------------------------------------------------------
    def ps_stdout(self):
        text = self.ps_process.readAllStandardOutput().data().decode()
        self.append_log(text)

    def ps_stderr(self):
        text = self.ps_process.readAllStandardError().data().decode()
        self.append_log(text)

    def ps_finished(self, exitCode, exitStatus):
        success = exitStatus == QProcess.ExitStatus.NormalExit and exitCode == 0
        if success and self._power_second_command is not None:
            second_command = self._power_second_command
            self._power_second_command = None
            self.append_log('---------------------------------------------------------------------------')
            self.run_pseti(*second_command)
            return
        self._power_second_command = None
        self._finish_power_command(success)
        if success:
            self.append_log('---------------------------------------------------------------------------')
            return
        self.append_log("Command failed")
        self.append_log('^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^')

    def append_log(self, text):
        write_console_log_line(text)
        html = self._console_html.convert(text)
        if not html:
            return
        cursor = self.console_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.console_output.document().isEmpty():
            cursor.insertBlock()
        cursor.insertHtml(html)
        self.console_output.setTextCursor(cursor)
        self.console_output.ensureCursorVisible()
        self._trim_console_output()

    def _trim_console_output(self, max_blocks=1000):
        # QTextEdit has no setMaximumBlockCount() (that's QPlainTextEdit-only)
        # -- console_output switched to QTextEdit for append_log()'s colored
        # HTML, so this replicates the same "don't grow forever" behavior by
        # hand.
        doc = self.console_output.document()
        excess = doc.blockCount() - max_blocks
        if excess <= 0:
            return
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, excess)
        cursor.removeSelectedText()

    def start_grpc_clicked(self):
        if not self.grpc_process_exit:
            return
        mode = self.visualization_mode.currentData()
        try:
            grpc_config = load_grpc_config()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.append_log(f"Cannot load visualization configuration: {exc}")
            return
        self.logger.info(f'Start PANOSETI gRPC process ({mode}).')
        self.logger.info(
            f'Loaded grpc config from {grpc_config.path} '
            f'(host={grpc_config.host}, port={grpc_config.port})'
        )
        self.grpc_process_exit = False
        self.visualization_mode.setEnabled(False)
        self.start_grpc.setEnabled(False)
        self.stop_grpc.setEnabled(True)
        args = [
            '-u', '-m', 'pseti_gui.grpc_process',
            '--host', grpc_config.host,
            '--port', str(grpc_config.port),
            '-m', mode,
        ]
        self.init_all_plots_zero()
        self.grpc_process.start(sys.executable, args)

    def stop_grpc_clicked(self):
        self.logger.info('Stop PANOSETI gRPC process.')
        pid = self.grpc_process.processId()
        if pid:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            if not self.grpc_process.waitForFinished(3000):
                self.append_log('Visualization is still stopping; please wait.')
                return
        if not self.grpc_process_exit and self.grpc_process.state() == QProcess.ProcessState.NotRunning:
            self._release_visualization()

    def _release_visualization(self):
        """Restore controls and release the previous stream before another mode starts."""
        if self.conn_notifier is not None:
            self.conn_notifier.setEnabled(False)
            self.conn_notifier.deleteLater()
            self.conn_notifier = None
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.img = None
        if self.shm is not None:
            self.shm.close()
            try:
                self.shm.unlink()
            except FileNotFoundError:
                pass
            self.shm = None
        self.server_notifier.setEnabled(True)
        self.grpc_process_exit = True
        self.visualization_mode.setEnabled(True)
        self.start_grpc.setEnabled(True)
        self.stop_grpc.setEnabled(False)
        self.reset_all_plots()

    def grpc_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.append_log(f'Cannot start visualization: {self.grpc_process.errorString()}')
            self._release_visualization()

    def init_all_plots_zero(self):
        """Show every window as a zero-valued image with the default title,
        before any real per-module frame has arrived."""
        zero_data = np.zeros((32, 32))
        for r in range(self.rows):
            for c in range(self.cols):
                self.show_plot(r, c, {
                    'image_array': zero_data,
                    'frame_number': 0,
                    'name': DEFAULT_TITLE,
                })

    def reset_all_plots(self):
        """Revert every window back to the default placeholder image."""
        for r in range(self.rows):
            for c in range(self.cols):
                i = r * self.cols + c
                self.plot_widgets[i] = None
                self.imgs[i] = None
                self.qttexts[i] = None
                self.set_placeholder(r, c)

    def _get_dytpe_from_mode(self, mode):
        if mode == 'ph1024' or mode == 'ph256':
            dtype = np.int16
        elif mode == 'mov8':
            dtype = np.uint8
        elif mode == 'mov16':
            dtype = np.uint16
        else:
            self.logger.error(f"mode({mode}) is not supported.")
        return dtype

    def grpc_stdout(self):
        # we get an image every time when this function is called
        # text already ends in '\n' (grpc_process's own Rich handler
        # terminates every line) -- print() would otherwise add a second one.
        text =  self.grpc_process.readAllStandardOutput().data().decode()
        print(text, end='')

    def grpc_stderr(self):
        text =  self.grpc_process.readAllStandardError().data().decode()
        print(text, end='')
    
    def grpc_finished(self, exitCode, exitStatus):
        self._release_visualization()
        if exitStatus == QProcess.ExitStatus.NormalExit and exitCode == 0:
            self.logger.info('PANOSETI gRPC process exited gracefully.')
        else:
            self.logger.error('PANOSETI gPRC process exited failed.')

    # ---------------------------------------------------------------------------
    # plot figures
    # ---------------------------------------------------------------------------
    def show_plot(self, r, c, data):
        i = r * self.cols + c
        if self.static_label[i] is not None:
            self.static_label[i] = None
            # create obj
            plot_widget = pg.PlotWidget()
            self.plot_widgets[i] = plot_widget
            # replaces the placeholder QLabel occupying this cell
            self.view_container.add_widget(plot_widget, r, c)
            # create random data for default viewer
            rdata = np.random.rand(32, 32)
            h, w = rdata.shape
            # show 2D img
            img = pg.ImageItem(rdata)
            self.imgs[i] = img
            plot_widget.addItem(img)
            img.setRect(0,0,w,h)
            # remove axis
            plot_widget.hideAxis('bottom')
            plot_widget.hideAxis('left')
            # set color map
            cmap = pg.colormap.get('plasma')  # PyQtGraph >=0.13
            img.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))
            # set title
            plot_widget.setTitle("Simulation", color='w', size='12pt')
            text = pg.TextItem("Frame: 0", color='w', anchor=(0, 1))  # anchor=(0,1) 左下角
            text.setPos(0,0) 
            self.plot_widgets[i].addItem(text)
            self.qttexts[i] = text
        else:
            pass
        imgdata = data['image_array']
        h, w = imgdata.shape
        self.imgs[i].setRect(0,0,w,h)
        self.imgs[i].setImage(imgdata)
        self.qttexts[i].setText(f"Frame No: {data['frame_number']}")
        self.plot_widgets[i].setTitle(data['name'])

    # ---------------------------------------------------------------------------
    # Signal functions
    # ---------------------------------------------------------------------------
    def run_command(self, program, arguments):
        self.ps_process.start(program, arguments)
    
    def run_pseti(self, *pseti_args):
        # Resolve the standalone CLI on PATH, independently of this interpreter.
        if self.ps_process.state() != QProcess.ProcessState.NotRunning:
            self.append_log('A command is already running. Wait for it to finish.')
            return False
        cmdline = 'pseti ' + ' '.join(pseti_args)
        self.append_log('---------------------------------------------------------------------------')
        self.append_log(cmdline)
        self.append_log('---------------------------------------------------------------------------')
        self.run_command('pseti', list(pseti_args))
        return True

    def power_toggled(self, checked):
        if self.ps_process.state() != QProcess.ProcessState.NotRunning:
            self.power_switch.setChecked(not checked)
            self.append_log('A command is already running. Wait before changing power.')
            return
        self._power_previous_checked = not checked
        self._power_previous_label = self.power_state_label.text()
        self._power_command = checked
        self._power_second_command = ('cfg', 'redis-daemons') if checked else ('cfg', 'stop-redis-daemons')
        self.power_switch.setEnabled(False)
        self.power_state_label.setText('Pending…')
        self.run_pseti('power', 'on' if checked else 'off')

    def _finish_power_command(self, success):
        if self._power_command is None:
            return
        if success:
            self.power_state_label.setText('ON*' if self._power_command else 'OFF*')
            self.power_state_label.setToolTip('Last successful command; physical power status is not monitored.')
        else:
            self.power_switch.setChecked(self._power_previous_checked)
            self.power_state_label.setText(self._power_previous_label)
        self._power_command = None
        self.power_switch.setEnabled(True)

    def ps_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.append_log(f'Cannot start command: {self.ps_process.errorString()}')
            self._power_second_command = None
            self._finish_power_command(False)

    def _run_status_check(self):
        if self._status_check_thread is not None:
            return  # previous poll hasn't finished yet; skip this tick rather than overlap it
        thread = StatusCheckThread(self)
        thread.power_checked.connect(self._on_power_status_checked)
        thread.transfer_checked.connect(self._on_transfer_status_checked)
        thread.grpc_process_checked.connect(self._on_grpc_process_status_checked)
        thread.finished.connect(self._on_status_check_finished)
        self._status_check_thread = thread
        thread.start()

    def _on_status_check_finished(self):
        thread = self._status_check_thread
        self._status_check_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_power_status_checked(self, total, on_count):
        if self._power_command is not None:
            return  # a manual power toggle is mid-flight; don't stomp its "Pending…" label
        self.power_state_label.setText(f'({on_count}/{total})')
        self.power_state_label.setToolTip(f'{on_count} of {total} device(s) reporting power on.')
        # setChecked() doesn't emit clicked (only real user/keyboard activation
        # does), so this can't recursively trigger power_toggled() -- it just
        # makes the switch reflect reality, e.g. showing on at startup if every
        # device is already observed to be on rather than defaulting to off.
        self.power_switch.setChecked(total > 0 and on_count == total)

    def _on_transfer_status_checked(self, running):
        self.set_subsystem_status('transfer', 'running' if running else 'idle')

    def _on_grpc_process_status_checked(self, running):
        self.set_subsystem_status('visualization', 'running' if running else 'idle')

    def start_interleave_clicked(self):
        """Future integration point: call run_pseti() once the command is defined."""
        self.append_log('Start Interleave: command is not configured yet.')

    def set_subsystem_status(self, subsystem: str, state: str, detail: str | None = None):
        """UI-thread entry point for future status observers; no polling is installed."""
        self.status_indicators[subsystem].set_status(state, detail)

    def reboot_clicked(self):
        self.run_pseti('cfg', 'reboot')

    def marocconfig_clicked(self):
        self.run_pseti('cfg', 'maroc-config', '--non-interactive')

    def maskconfig_clicked(self):
        self.run_pseti('cfg', 'mask-config')

    def calbrateph_clicked(self):
        self.run_pseti('cfg', 'calibrate-ph')

    def showbaselines_clicked(self):
        self.run_pseti('cfg', 'show-ph-baselines')

    def getuid_clicked(self):
        self.run_pseti('uids')

    def validate_clicked(self):
        self.run_pseti('val')

    def health_clicked(self):
        self.run_pseti('health', '--skip-containers')

    def startdaq_clicked(self):
        self.run_pseti('start', '--yes')

    def stopdaq_clicked(self):
        self.run_pseti('stop', '--yes')

    def xfr_start_clicked(self):
        self.run_pseti('xfr', 'start')

    def xfr_stop_clicked(self):
        self.run_pseti('xfr', 'stop')

    def xfr_status_clicked(self):
        self.run_pseti('xfr', 'stat')

    def xfr_monitor_clicked(self):
        # `pseti stat --watch` redraws in place with ANSI cursor-repositioning
        # escape codes (Rich's Live view) -- that renders as garbage inside
        # console_output (a plain QPlainTextEdit), so it needs a real
        # terminal emulator window instead of going through run_pseti()/
        # ps_process like the other buttons in this group.
        self.logger.info('Opening transfer monitor terminal (pseti stat --watch).')
        try:
            open_terminal_with_command(['pseti', 'stat', '--watch'])
        except RuntimeError as e:
            self.logger.error(str(e))
            self.append_log(f"Error: {e}")

    def plot_data(self, data):
        mid = data['module_id']
        slot = self.window_config.slots.get(mid)
        if slot is None:
            if mid not in self._unmapped_module_ids_warned:
                self.logger.warning(
                    f"module_id {mid} is not in the window config "
                    f"({self.window_config.path}); dropping its frames."
                )
                self._unmapped_module_ids_warned.add(mid)
            return
        data['name'] = slot.title
        self.show_plot(slot.row, slot.col, data)

    def closeEvent(self, event):
        # call stop_grpc to stop grpc process
        if self.grpc_process_exit == False:
            self.stop_grpc_clicked()
        # delete uds
        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)
        self._status_timer.stop()
        if self._status_check_thread is not None:
            self._status_check_thread.wait(3000)
        event.accept()

    # ---------------------------------------------------------------------------
    # Setup signal function
    # ---------------------------------------------------------------------------
    def setup_signal_functions(self):
        self.power_switch.clicked.connect(self.power_toggled)
        self.start_interleave.clicked.connect(self.start_interleave_clicked)
        self.reboot.clicked.connect(self.reboot_clicked)
        self.start_grpc.clicked.connect(self.start_grpc_clicked)
        self.stop_grpc.clicked.connect(self.stop_grpc_clicked)
        self.maroc_config.clicked.connect(self.marocconfig_clicked)
        self.mask_config.clicked.connect(self.maskconfig_clicked)
        self.cal_ph.clicked.connect(self.calbrateph_clicked)
        self.show_baselines.clicked.connect(self.showbaselines_clicked)
        self.get_uid.clicked.connect(self.getuid_clicked)
        self.validate.clicked.connect(self.validate_clicked)
        self.health.clicked.connect(self.health_clicked)
        self.start_daq.clicked.connect(self.startdaq_clicked)
        self.stop_daq.clicked.connect(self.stopdaq_clicked)
        self.xfr_start.clicked.connect(self.xfr_start_clicked)
        self.xfr_stop.clicked.connect(self.xfr_stop_clicked)
        self.xfr_status.clicked.connect(self.xfr_status_clicked)
        self.xfr_monitor.clicked.connect(self.xfr_monitor_clicked)
