import asyncio
import logging
import os
import subprocess
import json
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WakeOnController")

SLEEP_HOOK_PATH = "/usr/lib/systemd/system-sleep/wake-on-controller.sh"
SUDOERS_PATH = "/etc/sudoers.d/wake-on-controller"
BT_ADAPTER = "hci0"

XBOX_BT_NAMES = ["Xbox Wireless Controller", "Xbox Controller", "Microsoft Xbox Controller"]

SETTINGS_PATH     = Path("/home/deck/.config/wake-on-controller/settings.json")
WAKE_DEVICES_PATH = Path("/home/deck/.config/wake-on-controller/wake-devices.txt")

# Embedded sudoers rules — written once on first load.
# On SteamOS the deck user already has NOPASSWD:ALL, so we can bootstrap this
# by running `sudo tee` on first load without any pre-existing custom rules.
SUDOERS_CONTENT = """\
# Wake on Controller — Decky plugin
# Allows the deck user to arm BT wake and manage the sleep hook without a password.
deck ALL=(root) NOPASSWD: /bin/sh -c echo * > /sys/class/bluetooth/hci0/device/power/*
deck ALL=(root) NOPASSWD: /usr/bin/tee /usr/lib/systemd/system-sleep/wake-on-controller.sh
deck ALL=(root) NOPASSWD: /bin/rm -f /usr/lib/systemd/system-sleep/wake-on-controller.sh
deck ALL=(root) NOPASSWD: /bin/chmod +x /usr/lib/systemd/system-sleep/wake-on-controller.sh
deck ALL=(root) NOPASSWD: /usr/bin/btmgmt wake-system *
deck ALL=(root) NOPASSWD: /usr/bin/btmgmt add-device *
deck ALL=(root) NOPASSWD: /usr/bin/btmgmt del-device *
"""


def _run(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _run_root(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command as root via the Decky plugin helper."""
    return subprocess.run(["sudo"] + cmd, capture_output=True, text=True, check=False)


def _sysfs_write(path: str, value: str) -> bool:
    result = _run_root(["tee", path])
    if result.returncode != 0:
        # tee needs input — use sh -c
        result = _run_root(["sh", "-c", f"echo '{value}' > {path}"])
    return result.returncode == 0


def _adapter_sysfs_path() -> str | None:
    """Resolve the sysfs path for the BT adapter (e.g. /sys/class/bluetooth/hci0/device)."""
    base = f"/sys/class/bluetooth/{BT_ADAPTER}/device"
    if os.path.exists(base):
        return base
    return None


class Plugin:

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def _main(self):
        logger.info("WakeOnController: loading")
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        await self._auto_setup(self)
        if await self.get_enabled(self):
            await self._apply_bt_wake(self, enable=True)

    async def _unload(self):
        logger.info("WakeOnController: unloading")

    # Decky calls these on system suspend/resume
    async def _on_suspend(self):
        """Re-arm BT wakeup right before the system sleeps."""
        logger.info("WakeOnController: suspend hook — re-arming BT wake")
        if await self.get_enabled(self):
            await self._apply_bt_wake(self, enable=True)
            await self._register_devices_for_wake(self)

    async def _on_resume(self):
        """After wake: reconnect controller and clear wake scan list."""
        logger.info("WakeOnController: resume hook — reconnecting controller")
        if await self.get_enabled(self):
            await self._unregister_devices_for_wake(self)
            await self._reconnect_controller(self)

    # ------------------------------------------------------------------ #
    #  Public API (callable from the frontend)
    # ------------------------------------------------------------------ #

    async def initialize(self) -> dict:
        """
        Called by the frontend when the panel opens.
        Idempotent — safe to call every time the UI mounts.
        """
        await self._auto_setup(self)
        return await self.get_status(self)

    async def get_enabled(self) -> bool:
        s = self._load_settings(self)
        return s.get("enabled", False)

    async def set_enabled(self, enabled: bool) -> dict:
        s = self._load_settings(self)
        s["enabled"] = enabled
        self._save_settings(self, s)
        ok = await self._apply_bt_wake(self, enable=enabled)
        if enabled and ok:
            await self._install_sleep_hook(self)
            await self._register_devices_for_wake(self)
        elif not enabled:
            await self._unregister_devices_for_wake(self)
            await self._remove_sleep_hook(self)
        return {"success": ok}

    async def get_status(self) -> dict:
        """Return a snapshot of everything the UI needs."""
        adapter_path = _adapter_sysfs_path()
        wakeup_val = "unknown"
        power_ctrl = "unknown"

        if adapter_path:
            try:
                wakeup_val = Path(f"{adapter_path}/power/wakeup").read_text().strip()
                power_ctrl = Path(f"{adapter_path}/power/control").read_text().strip()
            except Exception:
                pass

        controllers = await self._list_xbox_controllers(self)

        return {
            "enabled": await self.get_enabled(self),
            "adapter_found": adapter_path is not None,
            "wakeup_armed": wakeup_val == "enabled",
            "power_control": power_ctrl,
            "controllers": controllers,
            "sleep_hook_installed": os.path.exists(SLEEP_HOOK_PATH),
            "wake_devices_registered": WAKE_DEVICES_PATH.exists() and WAKE_DEVICES_PATH.stat().st_size > 0,
        }

    async def get_paired_controllers(self) -> list[dict]:
        return await self._list_xbox_controllers(self)

    async def refresh_wake_devices(self) -> dict:
        """
        Re-scan paired controllers and re-register them immediately.
        Useful after pairing a new controller without needing to toggle
        the plugin off and on or wait for the next suspend.
        """
        ok = await self._register_devices_for_wake(self)
        return {"success": ok, "status": await self.get_status(self)}

    async def test_reconnect(self) -> dict:
        ok = await self._reconnect_controller(self)
        return {"success": ok}

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _auto_setup(self) -> None:
        """
        One-time idempotent setup: install sudoers rules and the sleep hook.
        Runs on plugin load and every time the panel opens so nothing falls
        through the cracks (e.g. after a SteamOS update wipes /etc/sudoers.d/).
        """
        await self._ensure_sudoers(self)
        await self._ensure_sleep_hook(self)

    async def _ensure_sudoers(self) -> bool:
        """Write the sudoers file if it doesn't exist or is out of date."""
        existing = ""
        if os.path.exists(SUDOERS_PATH):
            try:
                existing = Path(SUDOERS_PATH).read_text()
            except Exception:
                pass

        if existing.strip() == SUDOERS_CONTENT.strip():
            return True  # already current

        # SteamOS deck user has NOPASSWD:ALL by default, so this sudo call
        # works without pre-existing custom rules.
        proc = subprocess.run(
            ["sudo", "tee", SUDOERS_PATH],
            input=SUDOERS_CONTENT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            logger.error(f"WakeOnController: failed to write sudoers: {proc.stderr}")
            return False

        # Validate the file so a syntax error doesn't lock sudo
        check = _run_root(["visudo", "-c", "-f", SUDOERS_PATH])
        if check.returncode != 0:
            logger.error(f"WakeOnController: sudoers validation failed, removing: {check.stderr}")
            _run_root(["rm", "-f", SUDOERS_PATH])
            return False

        _run_root(["chmod", "0440", SUDOERS_PATH])
        logger.info("WakeOnController: sudoers rules installed")
        return True

    async def _ensure_sleep_hook(self) -> bool:
        """Install the sleep hook if it's not already in place."""
        if os.path.exists(SLEEP_HOOK_PATH):
            return True
        return await self._install_sleep_hook(self)

    async def _register_devices_for_wake(self) -> bool:
        """
        Tell the BT adapter to actively scan for each paired Xbox controller
        during suspend and wake the system when it sees one.

        Two steps:
          1. btmgmt add-device -a 0x01 -A 0x02 <mac>
             Registers the MAC as an LE (BLE) auto-connect target.
             When the Xbox button is pressed the controller broadcasts a BLE
             advertisement; the adapter sees it and raises a wakeup interrupt.
          2. Save the MAC list to a file so the sleep hook bash script can
             re-register them on every suspend without needing Python running.
        """
        controllers = await self._list_xbox_controllers(self)
        if not controllers:
            logger.warning("WakeOnController: no Xbox controllers found to register for wake")
            return False

        macs = [c["mac"] for c in controllers]

        # Persist MACs for the sleep hook script
        WAKE_DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
        WAKE_DEVICES_PATH.write_text("\n".join(macs) + "\n")

        success = False
        for mac in macs:
            # 0x01 = LE Public address type  (BLE advertisement from Xbox button press)
            # 0x02 = auto-connect action     (kernel wakes system when device is seen)
            r = _run_root(["btmgmt", "add-device", "-a", "0x01", "-A", "0x02", mac])
            if r.returncode == 0:
                logger.info(f"WakeOnController: registered {mac} for BLE wake scan")
                success = True
            else:
                logger.warning(f"WakeOnController: btmgmt add-device failed for {mac}: {r.stderr.strip()}")

        return success

    async def _unregister_devices_for_wake(self) -> None:
        """
        Remove controllers from the BT auto-connect/wake list after the system
        has already woken up — keeps the adapter from scanning unnecessarily
        while the Deck is in active use.
        """
        if not WAKE_DEVICES_PATH.exists():
            return

        for mac in WAKE_DEVICES_PATH.read_text().splitlines():
            mac = mac.strip()
            if mac:
                _run_root(["btmgmt", "del-device", mac])

        WAKE_DEVICES_PATH.unlink(missing_ok=True)
        logger.info("WakeOnController: unregistered wake devices after resume")

    async def _apply_bt_wake(self, enable: bool) -> bool:
        adapter_path = _adapter_sysfs_path()
        if not adapter_path:
            logger.warning("WakeOnController: BT adapter sysfs path not found")
            return False

        value = "enabled" if enable else "disabled"

        # 1. Set wakeup source
        r1 = _run_root(["sh", "-c", f"echo {value} > {adapter_path}/power/wakeup"])

        # 2. Prevent adapter from auto-suspending when we want wake enabled
        power_ctrl = "on" if enable else "auto"
        r2 = _run_root(["sh", "-c", f"echo {power_ctrl} > {adapter_path}/power/control"])

        # 3. Tell btmgmt to allow wake-system (best-effort — not all kernels support it)
        _run_root(["btmgmt", "wake-system", "on" if enable else "off"])

        success = (r1.returncode == 0) and (r2.returncode == 0)
        logger.info(f"WakeOnController: _apply_bt_wake({enable}) -> {success}")
        return success

    async def _install_sleep_hook(self) -> bool:
        """
        Write a systemd-sleep hook script so BT wake is re-armed every time
        the deck suspends (in case power management resets it).
        """
        script = """#!/bin/bash
# Installed by the Wake on Controller Decky plugin
# Runs before every suspend to keep the BT adapter armed for wake.
#
# Why this exists: power management can reset the sysfs wakeup flags when
# the system suspends, so we re-apply them here at the last moment before
# the system actually goes to sleep. We also re-register each paired Xbox
# controller so the adapter actively scans for the BLE advertisement that
# the controller sends when the Xbox button is pressed.

SYSFS="/sys/class/bluetooth/hci0/device"
WAKE_DEVICES="/home/deck/.config/wake-on-controller/wake-devices.txt"

case "$1" in
  pre)
    # 1. Keep adapter powered and mark it as a wakeup source
    if [ -d "$SYSFS" ]; then
      echo enabled > "$SYSFS/power/wakeup"
      echo on      > "$SYSFS/power/control"
    fi

    # 2. Allow the adapter to wake the system
    btmgmt wake-system on

    # 3. Register each paired controller for active BLE wake scanning
    #    -a 0x01 = LE Public address (the type Xbox controllers advertise on)
    #    -A 0x02 = auto-connect action (triggers wakeup when advertisement seen)
    if [ -f "$WAKE_DEVICES" ]; then
      while IFS= read -r mac; do
        [ -z "$mac" ] && continue
        btmgmt add-device -a 0x01 -A 0x02 "$mac"
      done < "$WAKE_DEVICES"
    fi
    ;;
esac
"""
        result = _run_root(["sh", "-c", f"cat > {SLEEP_HOOK_PATH} << 'HOOK_EOF'\n{script}\nHOOK_EOF"])
        if result.returncode != 0:
            # Fallback: write via tee
            proc = subprocess.run(
                ["sudo", "tee", SLEEP_HOOK_PATH],
                input=script,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                logger.error(f"WakeOnController: failed to write sleep hook: {proc.stderr}")
                return False

        _run_root(["chmod", "+x", SLEEP_HOOK_PATH])
        logger.info("WakeOnController: sleep hook installed")
        return True

    async def _remove_sleep_hook(self) -> bool:
        if os.path.exists(SLEEP_HOOK_PATH):
            result = _run_root(["rm", "-f", SLEEP_HOOK_PATH])
            return result.returncode == 0
        return True

    async def _list_xbox_controllers(self) -> list[dict]:
        """Return paired BT devices that look like Xbox controllers."""
        try:
            result = _run(["bluetoothctl", "devices"])
            controllers = []
            for line in result.stdout.splitlines():
                # Format: "Device AA:BB:CC:DD:EE:FF <Name>"
                parts = line.strip().split(" ", 2)
                if len(parts) < 3:
                    continue
                mac = parts[1]
                name = parts[2]
                if any(n.lower() in name.lower() for n in XBOX_BT_NAMES):
                    # Check if currently connected
                    info = _run(["bluetoothctl", "info", mac], check=False)
                    connected = "Connected: yes" in info.stdout
                    controllers.append({"mac": mac, "name": name, "connected": connected})
            return controllers
        except Exception as e:
            logger.error(f"WakeOnController: error listing controllers: {e}")
            return []

    async def _reconnect_controller(self) -> bool:
        """After wake, tell bluetoothctl to connect known Xbox controllers."""
        controllers = await self._list_xbox_controllers(self)
        if not controllers:
            return False
        success = False
        for ctrl in controllers:
            if not ctrl["connected"]:
                result = _run(["bluetoothctl", "connect", ctrl["mac"]], check=False)
                if result.returncode == 0:
                    success = True
                    logger.info(f"WakeOnController: reconnected {ctrl['name']}")
        return success

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return {}

    def _save_settings(self, settings: dict):
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
