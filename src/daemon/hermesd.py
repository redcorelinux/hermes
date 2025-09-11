#!/usr/bin/env python3

import dbus
import dbus.service
import dbus.mainloop.glib
import sisyphus.checkenv
import sisyphus.depsolve
import sisyphus.getfs
import sisyphus.syncenv
import sisyphus.syncdb
import os
import sys
import signal
import pickle
import logging
from gi.repository import GLib

SERVICE_NAME = 'org.hermesd.MessageService'
OBJECT_PATH = '/org/hermesd/MessageObject'
INTERFACE = 'org.hermesd.MessageInterface'

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)


def get_update_status():
    is_online = sisyphus.checkenv.connectivity()
    is_sane = sisyphus.checkenv.sanity()

    if is_online != int(1):
        logging.info("Connectivity check failed")
        return "no_internet"
    else:
        if is_sane == int(1):
            try:
                sisyphus.syncenv.g_repo()
                sisyphus.syncenv.r_repo()
                sisyphus.syncenv.p_cfg_repo()
                sisyphus.syncdb.rmt_tbl()
            except Exception:
                return "blocked_sync"
        else:
            return "blocked_sync"

    try:
        sisyphus.depsolve.start.__wrapped__()
    except Exception:
        logging.error("Upgrade check failed!")
        return "check_failed"

    try:
        with open(os.path.join(sisyphus.getfs.p_mtd_dir, "sisyphus_worlddeps.pickle"), "rb") as f:
            bin_list, src_list, is_missing, is_vague, need_cfg = pickle.load(f)
    except Exception:
        logging.error("Upgrade check failed!")
        return "check_failed"

    if need_cfg != int(0):
        logging.error("Portage configuration failure!")
        return "blocked_upgrade"
    else:
        if len(bin_list) == 0 and len(src_list) == 0:
            logging.info("System up to date!")
            return "heartbeat"
        else:
            logging.info("System upgrade available!")
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
    logging.info(f"Emitting DBus signal: {msg}")
    emitter.MessageSent(msg)


def main():
    logging.info("Daemon starting")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
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
    try:
        loop.run()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, quitting main loop")
        loop.quit()
    logging.info("Daemon exited cleanly")


if __name__ == '__main__':
    main()
