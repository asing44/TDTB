/* TagExclusionSettingsDrawer — retained facade over the consolidated
   SettingsShell. The tag exclusion policy body lives in
   TagExclusionSettingsPanel; existing suites keep mounting this name and now
   exercise the shared shell (the real host path). */

import { SettingsShell } from "./SettingsShell";

export function TagExclusionSettingsDrawer() {
  return <SettingsShell />;
}
