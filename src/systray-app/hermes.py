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


class SysTrayListener(QtCore.QObject):
    def __init__(self):
        super().__init__()

        if not QtWidgets.QApplication.instance():
            self.app = QtWidgets.QApplication(sys.argv)
        else:
            self.app = QtWidgets.QApplication.instance()

        self.tray = QtWidgets.QSystemTrayIcon(
            QtGui.QIcon.fromTheme("utilities-system-monitor"))
        self.tray.setToolTip("Hermes: System Upgrade Notifications")
        self.tray.setVisible(True)

        self.ignore_durations = OrderedDict([
            ("Ignore notifications for 1 day", 24*3600),
            ("Ignore notifications for 7 days", 7*24*3600),
            ("Ignore notifications for 15 days", 15*24*3600),
            ("Ignore notifications for 30 days", 30*24*3600),
            ("Receive notifications", 0)
        ])

        self.menu = QtWidgets.QMenu()
        for label in self.ignore_durations:
            action = QtGui.QAction(label, self.menu)
            action.triggered.connect(
                lambda checked, l=label: self.set_ignore(l))
            self.menu.addAction(action)

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

        launch_action = QtGui.QAction("Launch Sisyphus GUI", self.menu)
        launch_action.triggered.connect(self.launch_main_app)
        self.menu.addAction(launch_action)

        quit_action = QtGui.QAction("Quit", self.menu)
        quit_action.triggered.connect(self.quit_app)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)

        self.session_bus = QDBusConnection.systemBus()
        connected = self.session_bus.connect(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            "MessageSent",
            self.on_message_received
        )

        QtCore.QTimer.singleShot(
            15 * 60 * 1000, self.query_current_status)  # 15 minutes delay

        self.heartbeat_timer = QtCore.QTimer()
        self.heartbeat_timer.setInterval(25 * 3600 * 1000)  # 25 hours
        self.heartbeat_timer.timeout.connect(self.missed_heartbeat)
        self.heartbeat_timer.start()

    def set_ignore(self, label):
        duration = self.ignore_durations[label]
        if duration == 0:
            if os.path.exists(IGNORE_FILE):
                os.remove(IGNORE_FILE)
            self.tray.showMessage("Ignore Cleared", "Receive upgrade notifications.",
                                  QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        else:
            expiry = int(time.time()) + duration
            try:
                with open(IGNORE_FILE, "w") as f:
                    f.write(str(expiry))
                text = label[25:] if len(label) > 25 else label
                self.tray.showMessage(
                    "Ignore Set", f"Ignoring upgrade notifications for {text}.", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
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

    def launch_main_app(self):
        try:
            subprocess.Popen(['sisyphus-gui-pkexec'])
        except Exception:
            pass

    @QtCore.pyqtSlot(str)
    def on_message_received(self, message):
        self.heartbeat_timer.start()
        if message == "no_internet":
            self.tray.showMessage(
                "No Internet Connection",
                "Unable to check for system upgrade because no internet connection is available.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning
            )
        elif message == "blocked_sync":
            self.tray.showMessage(
                "Sync Failure",
                "Unable to sync the portage tree and overlays to check for system upgrade.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning
            )
        elif message == "check_failed":
            self.tray.showMessage(
                "Check Failure",
                "Unable to check for system upgrade.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Warning
            )
        elif message == "blocked_upgrade":
            if not self.is_ignored():
                self.tray.showMessage(
                    "Blocked Upgrade",
                    "System upgrade is available but blocked due to portage configuration issues.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning
                )
        elif message == "heartbeat":
            pass
        elif message == "upgrade_available":
            if not self.is_ignored():
                self.tray.showMessage(
                    "System Upgrade",
                    "System upgrade is available to improve security, stability and performance.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information
                )

    def missed_heartbeat(self):
        self.tray.showMessage(
            "Heartbeat Missed",
            "No heartbeat message received in over 25 hours. The daemon may be offline.",
            QtWidgets.QSystemTrayIcon.MessageIcon.Warning
        )

    def query_current_status(self):
        iface = QDBusInterface(
            SERVICE_NAME,
            OBJECT_PATH,
            INTERFACE,
            self.session_bus
        )
        reply = iface.call("GetStatus")
        if reply.type() == QDBusMessage.MessageType.ReplyMessage:
            message = reply.arguments()[0]
            self.on_message_received(message)

    def quit_app(self):
        self.tray.hide()
        self.app.quit()

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
            self.tray.showMessage(
                "Autostart Enabled", "", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        except Exception:
            self.tray.showMessage(
                "Autostart Enable Failed", "", QtWidgets.QSystemTrayIcon.MessageIcon.Critical)

    def remove_from_autostart(self):
        try:
            if os.path.exists(AUTOSTART_FILE):
                os.remove(AUTOSTART_FILE)
                self.tray.showMessage(
                    "Autostart Disabled", "", QtWidgets.QSystemTrayIcon.MessageIcon.Information)
            else:
                self.tray.showMessage(
                    "Autostart Not Found", "", QtWidgets.QSystemTrayIcon.MessageIcon.Warning)
        except Exception:
            self.tray.showMessage(
                "Autostart Disable Failed", "", QtWidgets.QSystemTrayIcon.MessageIcon.Critical)

    def run(self):
        self.app.exec()


def signal_handler(sig, frame):
    listener.quit_app()
    sys.exit(0)


if __name__ == "__main__":
    listener = SysTrayListener()
    signal.signal(signal.SIGINT, signal_handler)
    try:
        listener.run()
    except KeyboardInterrupt:
        listener.quit_app()
        sys.exit(0)
