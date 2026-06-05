import {
  definePlugin,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  ButtonItem,
  Field,
  staticClasses,
  Spinner,
} from "@decky/ui";
import { callable } from "@decky/api";
import { useEffect, useState, FC } from "react";

// Inline SVG — avoids react-icons which rollup leaves as an unbundled external
const GamepadIcon: FC = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 512" width="1em" height="1em" fill="currentColor">
    <path d="M480 96H160C71.6 96 0 167.6 0 256s71.6 160 160 160c44.8 0 85.3-18.4 114.6-48H365.4C394.7 397.6 435.2 416 480 416c88.4 0 160-71.6 160-160S568.4 96 480 96zM256 276c0 6.6-5.4 12-12 12h-52v52c0 6.6-5.4 12-12 12h-24c-6.6 0-12-5.4-12-12v-52H92c-6.6 0-12-5.4-12-12v-24c0-6.6 5.4-12 12-12h52v-52c0-6.6 5.4-12 12-12h24c6.6 0 12 5.4 12 12v52h52c6.6 0 12 5.4 12 12v24zm184 28c-17.7 0-32-14.3-32-32s14.3-32 32-32 32 14.3 32 32-14.3 32-32 32zm64-64c-17.7 0-32-14.3-32-32s14.3-32 32-32 32 14.3 32 32-14.3 32-32 32z"/>
  </svg>
);

// ── Backend callables ─────────────────────────────────────────────────────────

const initialize          = callable<[], StatusResult>("initialize");
const getStatus           = callable<[], StatusResult>("get_status");
const setEnabled          = callable<[boolean], { success: boolean }>("set_enabled");
const testReconnect       = callable<[], { success: boolean }>("test_reconnect");
const refreshWakeDevices  = callable<[], { success: boolean; status: StatusResult }>("refresh_wake_devices");

// ── Types ─────────────────────────────────────────────────────────────────────

interface Controller {
  mac: string;
  name: string;
  connected: boolean;
}

interface UsbController {
  js: string;
  name: string;
  wakeup_path: string;
  armed: boolean;
}

interface StatusResult {
  enabled: boolean;
  adapter_found: boolean;
  wakeup_armed: boolean;
  power_control: string;
  controllers: Controller[];
  usb_controllers: UsbController[];
  sleep_hook_installed: boolean;
  wake_devices_registered: boolean;
}

// ── Main panel component ──────────────────────────────────────────────────────

const WakeOnControllerPanel: FC = () => {
  const [status, setStatus] = useState<StatusResult | null>(null);
  const [dataLoading, setDataLoading] = useState(true); // true only while first fetch is in-flight
  const [toggling, setToggling] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    try {
      const s = await getStatus();
      setStatus(s);
    } catch (_) {}
    setDataLoading(false);
  };

  useEffect(() => {
    // Run initialize() in the background (sets up sudoers + sleep hook).
    // The panel renders immediately — data fills in when the call returns.
    initialize()
      .then((s) => { setStatus(s); setDataLoading(false); })
      .catch(() => refresh());

    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleToggle = async (enabled: boolean) => {
    setToggling(true);
    await setEnabled(enabled);
    await refresh();
    setToggling(false);
  };

  const handleReconnect = async () => {
    setReconnecting(true);
    await testReconnect();
    await refresh();
    setReconnecting(false);
  };

  const handleRefreshWakeDevices = async () => {
    setRefreshing(true);
    const result = await refreshWakeDevices();
    // Result already includes updated status — apply it directly
    if (result.status) setStatus(result.status);
    setRefreshing(false);
  };

  // Derive safe display values — show defaults while data is loading
  const enabled   = status?.enabled ?? false;
  const adapterOk = status?.adapter_found ?? false;
  const wakeArmed = status?.wakeup_armed ?? false;
  const hookOk    = status?.sleep_hook_installed ?? false;
  const devicesOk = status?.wake_devices_registered ?? false;
  const powerCtrl = status?.power_control ?? "—";
  const controllers    = status?.controllers ?? [];
  const usbControllers = status?.usb_controllers ?? [];

  return (
    <>
      {dataLoading && (
        <PanelSection>
          <PanelSectionRow>
            <Field label="" description="Loading status…">
              <Spinner />
            </Field>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Wake on Controller">
        <PanelSectionRow>
          <ToggleField
            label="Enable BT Wake"
            description="Wake the Steam Deck by pressing the home button on your Bluetooth controller"
            checked={enabled}
            disabled={toggling || !adapterOk || dataLoading}
            onChange={handleToggle}
          />
        </PanelSectionRow>

        {!adapterOk && !dataLoading && (
          <PanelSectionRow>
            <Field label="" description="⚠ Bluetooth adapter not found at /sys/class/bluetooth/hci0" />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Status">
        <PanelSectionRow>
          <Field
            label="BT Wake Armed"
            description={wakeArmed ? "✓ Wakeup source is active" : "✗ Not armed — toggle on above"}
          >
            <StatusDot active={wakeArmed} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="Sleep Hook"
            description={hookOk ? "✓ Re-arms on every suspend" : "✗ Not installed"}
          >
            <StatusDot active={hookOk} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="BLE Wake Scan"
            description={
              devicesOk
                ? "✓ Controller registered — adapter will scan for it during sleep"
                : "✗ No controller registered — pair one and enable wake above"
            }
          >
            <StatusDot active={devicesOk} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="BT Power Mode" description={`Adapter power control: ${powerCtrl}`} />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Controllers">
        {controllers.length === 0 ? (
          <PanelSectionRow>
            <Field
              label={dataLoading ? "Detecting controllers…" : "No controllers detected"}
              description={dataLoading ? "" : "Pair a BT controller in Steam's BT settings first. Works with Xbox, DualSense, Switch Pro, 8BitDo, and any BT gamepad."}
            />
          </PanelSectionRow>
        ) : (
          controllers.map((ctrl) => (
            <PanelSectionRow key={ctrl.mac}>
              <Field
                label={ctrl.name}
                description={`${ctrl.mac}  •  ${ctrl.connected ? "Connected" : "Not connected"}`}
              >
                <StatusDot active={ctrl.connected} />
              </Field>
            </PanelSectionRow>
          ))
        )}

        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={refreshing || !enabled}
            onClick={handleRefreshWakeDevices}
          >
            {refreshing ? "Refreshing..." : "Refresh Wake Devices"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={reconnecting || controllers.length === 0}
            onClick={handleReconnect}
          >
            {reconnecting ? "Reconnecting..." : "Reconnect Now"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="USB Controllers (experimental)">
        {usbControllers.length === 0 ? (
          <PanelSectionRow>
            <Field
              label="No USB controllers detected"
              description="Connect a controller via USB-C and enable wake above. Not all hardware supports USB HID wake."
            />
          </PanelSectionRow>
        ) : (
          usbControllers.map((ctrl) => (
            <PanelSectionRow key={ctrl.wakeup_path}>
              <Field
                label={ctrl.name}
                description={`${ctrl.js}  •  Wake ${ctrl.armed ? "armed ✓" : "not armed"}`}
              >
                <StatusDot active={ctrl.armed} />
              </Field>
            </PanelSectionRow>
          ))
        )}
      </PanelSection>

      <PanelSection title="How it works">
        <PanelSectionRow>
          <Field
            label=""
            description={
              "1. Pair your controller in Steam BT settings\n" +
              "2. Enable BT Wake above\n" +
              "3. Put the Deck to sleep\n" +
              "4. Press the home button — the Deck wakes and reconnects"
            }
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};

// ── Small status indicator dot ────────────────────────────────────────────────

const StatusDot: FC<{ active: boolean }> = ({ active }) => (
  <div
    style={{
      width: 10,
      height: 10,
      borderRadius: "50%",
      backgroundColor: active ? "#4caf50" : "#f44336",
      flexShrink: 0,
    }}
  />
);

// ── Plugin entry point ────────────────────────────────────────────────────────

export default definePlugin(() => ({
  name: "Wake on Controller",
  titleView: <div className={staticClasses.Title}>Wake on Controller</div>,
  content: <WakeOnControllerPanel />,
  icon: <GamepadIcon />,
  onDismount: () => {},
}));
