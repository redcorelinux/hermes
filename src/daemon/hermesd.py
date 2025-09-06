#!/usr/bin/env python3

import dbus
import dbus.service
import dbus.mainloop.glib
import sisyphus
import io
import sys
import signal
import subprocess
import logging
import socket
from gi.repository import GLib

SERVICE_NAME = 'org.hermesd.MessageService'
OBJECT_PATH = '/org/hermesd/MessageObject'
INTERFACE = 'org.hermesd.MessageInterface'

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)


def check_internet(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def get_update_status():
    if not check_internet():
        logging.info("Internet check failed")
        return "no_internet"
    else:
        try:
            subprocess.check_call(['emerge', '--sync'])
        except Exception as e:
            logging.error(f"'emerge --sync' failed: {e}")
            return "blocked_sync"

    buffer = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buffer

    try:
        sisyphus.sysupgrade.start(
            ask=False, ebuild=True, gfx_ui=False, pretend=True)
    except SystemExit:
        pass
    except Exception as e:
        sys.stdout = old_stdout
        logging.error(f"Exception in sysupgrade: {e}")
        return "sisyphus_exception"

    sys.stdout = old_stdout

    output = buffer.getvalue()
    cleaned_output = output.replace('\n', ' ').strip()
    logging.info(f"Upgrade check output: {cleaned_output}")

    if "Please apply the above changes to your portage configuration files" in cleaned_output:
        return "blocked_upgrade"
    elif "The system is up to date" in cleaned_output:
        return "heartbeat"
    else:
        return "upgrade_available"


class MessageEmitter(dbus.service.Object):
    def __init__(self, bus, object_path):
        super().__init__(bus, object_path)

    @dbus.service.signal(dbus_interface=INTERFACE, signature='s')
    def MessageSent(self, message):
        logging.info(f"Signal emitted: {message}")

    @dbus.service.method(dbus_interface=INTERFACE, in_signature='', out_signature='s')
    def GetStatus(self):
        status = get_update_status()
        logging.info(f"GetStatus called; returning: {status}")
        return status


def send_message(emitter, msg):
    emitter.MessageSent(msg)


def main():
    logging.info("Daemon starting")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName(SERVICE_NAME, bus)
    emitter = MessageEmitter(bus, OBJECT_PATH)

    loop = GLib.MainLoop()

    def send_periodic():
        status = get_update_status()
        logging.info(f"Periodic send message: {status}")
        send_message(emitter, status)
        GLib.timeout_add_seconds(86400, send_periodic)  # every 24h
        return False

    def sigterm_handler(signum, frame):
        logging.info("SIGTERM received, quitting main loop")
        loop.quit()

    signal.signal(signal.SIGTERM, sigterm_handler)

    send_periodic()
    loop.run()
    logging.info("Daemon exited cleanly")


if __name__ == '__main__':
    main()
