/* SetupDrawer — retained facade over the consolidated SettingsShell. The Day
   setup body lives in DaySetupPanel; existing suites keep mounting this name
   and now exercise the shared shell (the real host path). */

import { SettingsShell } from "./SettingsShell";

export function SetupDrawer() {
  return <SettingsShell />;
}
