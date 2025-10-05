#!/usr/bin/env python3

import os
import sys
import signal
import pickle
import logging

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

import sisyphus.checkenv
import sisyphus.depsolve
import sisyphus.getfs
import sisyphus.revdepsolve
import sisyphus.syncenv
import sisyphus.syncdb


class Config:
    SERVICE_NAME = 'org.hermesd.MessageService'
    OBJECT_PATH = '/org/hermesd/MessageObject'
    INTERFACE = 'org.hermesd.MessageInterface'
    HEARTBEAT_INTERVAL = 2700    # 45 minutes
    STATUS_INTERVAL = 21600      # 6 hours


def setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    logfile = os.environ.get("HERMESD_LOGFILE")
    if logfile:
        handlers.append(logging.FileHandler(logfile))
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        handlers=handlers
    )


class UpdateChecker:
    @staticmethod
    def get_status():
        is_online = sisyphus.checkenv.connectivity()
        is_sane = sisyphus.checkenv.sanity()

        if is_online != int(1):
            logging.error("Connectivity check failed")
            return "no_internet"
        else:
            if is_sane == int(0):
                logging.error("Portage tree && overlay sync failed!")
                return "blocked_sync"
            else:
                try:
                    sisyphus.syncenv.g_repo()
                    sisyphus.syncenv.r_repo()
                    sisyphus.syncenv.p_cfg_repo()
                    sisyphus.syncdb.rmt_tbl()
                except Exception:
                    logging.error("Portage tree && overlay sync failed!")
                    return "blocked_sync"
                try:
                    sisyphus.depsolve.start.__wrapped__()
                except Exception:
                    logging.error("Upgrade check failed!")
                    return "upgrade_check_failed"
                try:
                    with open(os.path.join(sisyphus.getfs.p_mtd_dir, "sisyphus_worlddeps.pickle"), "rb") as f:
                        bin_list, src_list, is_missing, is_vague, need_cfg = pickle.load(
                            f)
                except Exception:
                    logging.error("Upgrade check failed!")
                    return "upgrade_check_failed"

                if need_cfg != int(0):
                    logging.error("Portage configuration failure!")
                    return "blocked_upgrade"
                else:
                    if len(bin_list) == 0 and len(src_list) == 0:
                        try:
                            sisyphus.revdepsolve.start.__wrapped__(
                                depclean=True)
                        except Exception:
                            logging.error("Orphan check failed!")
                            return "orphan_check_failed"
                        try:
                            with open(os.path.join(sisyphus.getfs.p_mtd_dir, "sisyphus_pkgrevdeps.pickle"), "rb") as f:
                                is_installed, is_needed, is_vague, rm_list = pickle.load(
                                    f)
                        except Exception:
                            logging.error("Orphan check failed!")
                            return "orphan_check_failed"

                        if len(rm_list) == 0:
                            logging.info("System up to date!")
                            return "up_to_date"
                        else:
                            logging.info("Orphaned packages detected!")
                            return "orphans_detected"
                    else:
                        logging.info("System upgrade detected!")
                        return "upgrade_detected"


class MessageEmitter(dbus.service.Object):
    def __init__(self, bus, object_path):
        super().__init__(bus, object_path)

    @dbus.service.signal(dbus_interface=Config.INTERFACE, signature='s')
    def MessageSent(self, message):
        logging.info(f"Signal emitted: {message}")

    @dbus.service.signal(dbus_interface=Config.INTERFACE, signature='')
    def Heartbeat(self):
        logging.info("Heartbeat signal emitted")

    @dbus.service.method(dbus_interface=Config.INTERFACE, in_signature='', out_signature='s')
    def GetStatus(self):
        status = UpdateChecker.get_status()
        logging.info(f"GetStatus called; returning: {status}")
        return status


class HermesDaemon:
    def __init__(self):
        setup_logging()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.name = dbus.service.BusName(Config.SERVICE_NAME, self.bus)
        self.emitter = MessageEmitter(self.bus, Config.OBJECT_PATH)
        self.loop = GLib.MainLoop()
        self._setup_signals()

    def _setup_signals(self):
        signal.signal(signal.SIGTERM, self._sigterm_handler)
        signal.signal(signal.SIGINT, self._sigterm_handler)

    def _sigterm_handler(self, signum, frame):
        logging.info(f"Signal {signum} received, quitting main loop")
        self.loop.quit()

    def _send_periodic(self):
        status = UpdateChecker.get_status()
        logging.info(f"Periodic send message: {status}")
        self.emitter.MessageSent(status)
        GLib.timeout_add_seconds(Config.STATUS_INTERVAL, self._send_periodic)
        return False

    def _send_heartbeat(self):
        self.emitter.Heartbeat()
        GLib.timeout_add_seconds(
            Config.HEARTBEAT_INTERVAL, self._send_heartbeat)
        return False

    def run(self):
        logging.info("Daemon starting")
        self._send_periodic()
        self._send_heartbeat()
        try:
            self.loop.run()
        except Exception as e:
            logging.info(f"Exiting: {e}")
        logging.info("Daemon exited cleanly")


def main():
    daemon = HermesDaemon()
    daemon.run()


if __name__ == '__main__':
    main()
