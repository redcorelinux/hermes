#!/usr/bin/env python3

import sys
import os
import time
import signal
import subprocess

from collections import OrderedDict

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

IGNORE_FILE = os.path.expanduser("~/.hermes_upgrade_ignore")
AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "hermes.desktop")

SERVICE_NAME = 'org.hermesd.MessageService'
OBJECT_PATH = '/org/hermesd/MessageObject'
INTERFACE = 'org.hermesd.MessageInterface'


class HermesDBusHandler(QtCore.QObject):
    messageReceived = QtCore.pyqtSignal(str)
    heartbeatReceived = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.session_bus = QDBusConnection.systemBus()
        self.session_bus.connect(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            "MessageSent",
            self.handle_message
        )
        self.session_bus.connect(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            "Heartbeat",
            self.handle_heartbeat
        )

    @QtCore.pyqtSlot(str)
    def handle_message(self, message):
        self.messageReceived.emit(message)

    @QtCore.pyqtSlot()
    def handle_heartbeat(self):
        self.heartbeatReceived.emit()

    def get_status(self):
        iface = QDBusInterface(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            self.session_bus
        )
        reply = iface.call("GetStatus")
        if reply.type() == QDBusMessage.MessageType.ReplyMessage:
            return reply.arguments()[0]
        return None


class HistoryDialog(QtWidgets.QDialog):
    def __init__(self, notifications, gui_instance=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notification History")
        self.resize(720, 405)

        self.notifications = notifications
        self.parent_gui = gui_instance

        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Title", "Message"])
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 150)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)

        self.refresh_table()

        layout.addWidget(self.table)

        button_box = QtWidgets.QHBoxLayout()
        clear_btn = QtWidgets.QPushButton("Clear Notification History")
        clear_btn.clicked.connect(self.clear_history)
        button_box.addStretch()
        button_box.addWidget(clear_btn)
        layout.addLayout(button_box)

    def refresh_table(self):
        self.table.setRowCount(len(self.notifications))
        for row, note in enumerate(self.notifications):
            self.table.setItem(
                row, 0, QtWidgets.QTableWidgetItem(note['timestamp']))
            self.table.setItem(
                row, 1, QtWidgets.QTableWidgetItem(note['title']))
            self.table.setItem(
                row, 2, QtWidgets.QTableWidgetItem(note['message']))

    def clear_history(self):
        if self.parent_gui:
            self.parent_gui.notification_history.clear()
        self.notifications.clear()
        self.refresh_table()


class SysTrayGui(QtCore.QObject):
    def __init__(self):
        super().__init__()

        if not QtWidgets.QApplication.instance():
            self.app = QtWidgets.QApplication(sys.argv)
        else:
            self.app = QtWidgets.QApplication.instance()

        self.ignore_durations = OrderedDict([
            ("Ignore notifications for 1 day", 24*3600),
            ("Ignore notifications for 7 days", 7*24*3600),
            ("Ignore notifications for 15 days", 15*24*3600),
            ("Ignore notifications for 30 days", 30*24*3600),
            ("Allow notifications", 0)
        ])

        self.notification_history = []

        self.tray = QtWidgets.QSystemTrayIcon(
            QtGui.QIcon("/usr/share/pixmaps/hermes.png"))
        self.tray.setToolTip("Hermes: System Upgrade Notifications")
        self.tray.setVisible(True)

        self.menu = QtWidgets.QMenu()
        for label in self.ignore_durations:
            action = QtGui.QAction(label, self.menu)
            action.triggered.connect(
                lambda checked, l=label: self.set_ignore(l))
            self.menu.addAction(action)

        self.menu.addSeparator()

        history_action = QtGui.QAction("Show Notification History", self.menu)
        history_action.triggered.connect(self.show_notification_history)
        self.menu.addAction(history_action)

        self.menu.addSeparator()

        launch_action = QtGui.QAction("Launch Sisyphus GUI", self.menu)
        launch_action.triggered.connect(self.launch_main_app)
        self.menu.addAction(launch_action)

        self.menu.addSeparator()

        self.add_autostart_action = QtGui.QAction(
            "Enable Autostart", self.menu)
        self.add_autostart_action.triggered.connect(self.add_to_autostart)
        self.menu.addAction(self.add_autostart_action)

        self.remove_autostart_action = QtGui.QAction(
            "Disable Autostart", self.menu)
        self.remove_autostart_action.triggered.connect(
            self.remove_from_autostart)
        self.menu.addAction(self.remove_autostart_action)

        self.menu.addSeparator()

        quit_action = QtGui.QAction("Quit", self.menu)
        quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)

        self.heartbeat_timer = QtCore.QTimer()
        self.heartbeat_timer.setInterval(1 * 3600 * 1000)  # 1 hour
        self.heartbeat_timer.timeout.connect(self.missed_heartbeat)
        self.heartbeat_timer.start()

        self.signal_timer = QtCore.QTimer()
        self.signal_timer.timeout.connect(lambda: None)
        self.signal_timer.start(100)  # every 100 ms

    def record_notification(self, title, message, icon_type):
        self.notification_history.append({
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'title': title,
            'message': message,
            'icon': icon_type,
        })

    def show_notification(self, title, message, icon_type):
        self.tray.showMessage(title, message, icon_type)
        self.record_notification(title, message, icon_type)

    def set_ignore(self, label):
        duration = self.ignore_durations[label]
        if duration == 0:
            if os.path.exists(IGNORE_FILE):
                os.remove(IGNORE_FILE)
            self.show_notification("Ignore Cleared", "Notifications allowed.",
                                   QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        else:
            expiry = int(time.time()) + duration
            try:
                with open(IGNORE_FILE, "w") as f:
                    f.write(str(expiry))
                text = label[25:] if len(label) > 25 else label
                self.show_notification(
                    "Ignore Set", f"Ignoring notifications for {text}.", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
            except Exception:
                pass

    def is_ignored(self):
        if not os.path.exists(IGNORE_FILE):
            return False
        try:
            expiry = int(open(IGNORE_FILE).read().strip())
            return time.time() < expiry
        except Exception:
            return False

    def handle_message(self, message):
        self.heartbeat_timer.start()

        if message == "no_internet":
            self.show_notification("No Internet", "Cannot check for updates.",
                                   QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "blocked_sync":
            self.show_notification("Sync Failed", "Could not sync portage tree and overlays.",
                                   QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "upgrade_check_failed":
            self.show_notification("Check Failed", "Could not check for system upgrade.",
                                   QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "orphan_check_failed":
            self.show_notification("Check Failed", "Could not check for orphaned packages.",
                                   QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "blocked_upgrade":
            if not self.is_ignored():
                self.show_notification("Blocked Upgrade", "Upgrade blocked by portage configuration.",
                                       QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        elif message == "orphans_detected":
            if not self.is_ignored():
                self.show_notification("Orphans Detected", "System up to date, orphaned packages found.",
                                       QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        elif message == "upgrade_detected":
            if not self.is_ignored():
                self.show_notification("System Upgrade", "System upgrade available.",
                                       QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        elif message == "up_to_date":
            if not self.is_ignored():
                self.show_notification("Up to date", "System is up to date.",
                                       QtWidgets.QSystemTrayIcon.MessageIcon.Information)

    def handle_heartbeat(self):
        self.heartbeat_timer.start()

    def missed_heartbeat(self):
        self.show_notification("Heartbeat Missed", "No heartbeat for over 1 hour.",
                               QtWidgets.QSystemTrayIcon.MessageIcon.Warning)

    def add_to_autostart(self):
        try:
            if not os.path.exists(AUTOSTART_DIR):
                os.makedirs(AUTOSTART_DIR)
            exec_path = sys.argv[0]
            desktop_entry = f"""[Desktop Entry]
Type=Application
Exec={exec_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=Hermes
Comment=Tray notifications for system upgrades
"""
            with open(AUTOSTART_FILE, 'w') as f:
                f.write(desktop_entry)
            self.show_notification(
                "Autostart Enabled", "", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        except Exception:
            self.show_notification(
                "Autostart Enable Failed", "", QtWidgets.QSystemTrayIcon.MessageIcon.Critical)

    def remove_from_autostart(self):
        try:
            if os.path.exists(AUTOSTART_FILE):
                os.remove(AUTOSTART_FILE)
                self.show_notification(
                    "Autostart Disabled", "", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
            else:
                self.show_notification(
                    "Autostart Not Found", "", QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        except Exception:
            self.show_notification(
                "Autostart Disable Failed", "", QtWidgets.QSystemTrayIcon.MessageIcon.Critical)

    def launch_main_app(self):
        try:
            subprocess.Popen(['sisyphus-gui-pkexec'])
        except Exception:
            pass

    def show_notification_history(self):
        dialog = HistoryDialog(
            self.notification_history, gui_instance=self, parent=None)
        dialog.exec()

    def quit_app(self):
        self.tray.hide()
        self.app.quit()

    def run(self):
        self.app.exec()


def signal_handler(sig, frame):
    sys.exit(0)


if __name__ == "__main__":
    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance()

    dbus_handler = HermesDBusHandler()
    gui = SysTrayGui()

    dbus_handler.messageReceived.connect(gui.handle_message)
    dbus_handler.heartbeatReceived.connect(gui.handle_heartbeat)

    # Query current status after 15 minutes delay
    def delayed_status_query():
        status = dbus_handler.get_status()
        if status:
            gui.handle_message(status)

    QtCore.QTimer.singleShot(15 * 60 * 1000, delayed_status_query)

    signal.signal(signal.SIGINT, lambda sig, frame: gui.quit_app())
    signal.signal(signal.SIGTERM, lambda sig, frame: gui.quit_app())

    gui.run()
