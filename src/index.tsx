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
import { FaGamepad } from "react-icons/fa";

// ── Backend callables ─────────────────────────────────────────────────────────

const initialize    = callable<[], StatusResult>("initialize");
const getStatus     = callable<[], StatusResult>("get_status");
const setEnabled    = callable<[boolean], { success: boolean }>("set_enabled");
const testReconnect = callable<[], { success: boolean }>("test_reconnect");

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
}

// ── Main panel component ──────────────────────────────────────────────────────

const WakeOnControllerPanel: FC = () => {
  const [status, setStatus] = useState<StatusResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

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
            description="Wake the Steam Deck by pressing the Xbox button on your controller"
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
            label="BT Power Mode"
            description={`Adapter power control: ${status.power_control}`}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Controllers">
        {status.controllers.length === 0 ? (
          <PanelSectionRow>
            <Field
              label="No Xbox controllers paired"
              description="Pair your Xbox controller in Steam's BT settings first, then come back here"
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
  icon: <FaGamepad />,
  onDismount: () => {},
}));
