/* CapacitiesSettingsDrawer — retained facade over the consolidated
   SettingsShell. The Capacities policy body lives in
   CapacitiesSettingsPanel; existing suites keep mounting this name and now
   exercise the shared shell (the real host path). */

import { SettingsShell } from "./SettingsShell";

export function CapacitiesSettingsDrawer() {
  return <SettingsShell />;
}
