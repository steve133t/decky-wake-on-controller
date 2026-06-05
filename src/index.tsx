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

interface StatusResult {
  enabled: boolean;
  adapter_found: boolean;
  wakeup_armed: boolean;
  power_control: string;
  controllers: Controller[];
  sleep_hook_installed: boolean;
  wake_devices_registered: boolean;
}

// ── Main panel component ──────────────────────────────────────────────────────

const WakeOnControllerPanel: FC = () => {
  const [status, setStatus] = useState<StatusResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    const s = await getStatus();
    setStatus(s);
    setLoading(false);
  };

  useEffect(() => {
    // First open: run initialize() so sudoers + sleep hook are set up automatically,
    // then fall back to plain getStatus() for subsequent polls.
    initialize().then((s) => {
      setStatus(s);
      setLoading(false);
    });
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

  if (loading || !status) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <div style={{ display: "flex", justifyContent: "center", padding: "20px" }}>
            <Spinner />
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <>
      <PanelSection title="Wake on Controller">
        <PanelSectionRow>
          <ToggleField
            label="Enable BT Wake"
            description="Wake the Steam Deck by pressing the home button on your Bluetooth controller"
            checked={status.enabled}
            disabled={toggling || !status.adapter_found}
            onChange={handleToggle}
          />
        </PanelSectionRow>

        {!status.adapter_found && (
          <PanelSectionRow>
            <Field label="" description="⚠ Bluetooth adapter not found at /sys/class/bluetooth/hci0" />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Status">
        <PanelSectionRow>
          <Field
            label="BT Wake Armed"
            description={status.wakeup_armed ? "✓ Wakeup source is active" : "✗ Not armed — toggle on above"}
          >
            <StatusDot active={status.wakeup_armed} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="Sleep Hook"
            description={
              status.sleep_hook_installed
                ? "✓ Re-arms on every suspend"
                : "✗ Not installed"
            }
          >
            <StatusDot active={status.sleep_hook_installed} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="BLE Wake Scan"
            description={
              status.wake_devices_registered
                ? "✓ Controller registered — adapter will scan for it during sleep"
                : "✗ No controller registered — pair one and enable wake above"
            }
          >
            <StatusDot active={status.wake_devices_registered} />
          </Field>
        </PanelSectionRow>
        <PanelSectionRow>
          <Field
            label="BT Power Mode"
            description={`Adapter power control: ${status.power_control}`}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Controllers">
        {status.controllers.length === 0 ? (
          <PanelSectionRow>
            <Field
              label="No controllers detected"
              description="Pair a BT controller in Steam's BT settings first, then come back here. Works with Xbox, DualSense, Switch Pro, 8BitDo, and any other BT gamepad."
            />
          </PanelSectionRow>
        ) : (
          status.controllers.map((ctrl) => (
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
            disabled={refreshing || !status.enabled}
            onClick={handleRefreshWakeDevices}
          >
            {refreshing ? "Refreshing..." : "Refresh Wake Devices"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={reconnecting || status.controllers.length === 0}
            onClick={handleReconnect}
          >
            {reconnecting ? "Reconnecting..." : "Reconnect Now"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="How it works">
        <PanelSectionRow>
          <Field
            label=""
            description={
              "1. Pair your Xbox controller in Steam BT settings\n" +
              "2. Enable BT Wake above\n" +
              "3. Put the deck to sleep\n" +
              "4. Press the Xbox button — the deck wakes and reconnects"
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
