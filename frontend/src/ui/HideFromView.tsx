/* HideFromView — session-only "hide this row from view". Purely a view
   filter held in store `ui.hiddenIds`: nothing is written to a source, the
   plan, or storage, and a reload brings every row back. The strip above the
   plan counts what is hidden and shows it all again in one click. */
import { useApp, useAppState } from "./context";

function EyeOffIcon() {
  return <svg class="trow__icon" viewBox="0 0 16 16" width="18" height="18" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true" focusable="false">
    <path d="M1.5 8s2.3-4.5 6.5-4.5S14.5 8 14.5 8s-2.3 4.5-6.5 4.5S1.5 8 1.5 8z" />
    <path d="M2.5 13.5l11-11" />
  </svg>;
}

export function HideFromViewButton({ id, name }: { id: string; name: string }) {
  const { controller } = useApp();
  return <button type="button" class="trow__btn trow__btn--icon trow__hide"
    aria-label={`Hide ${name} from view`}
    title="Hide from view — this session only"
    onClick={() => controller.hideFromView(id)}>
    <EyeOffIcon />
  </button>;
}

export function HiddenViewStrip() {
  const { controller } = useApp();
  const s = useAppState();
  const n = s.ui.hiddenIds.length;
  if (n === 0) return null;
  return <div class="queue__hidden-strip" role="status" data-hidden-strip>
    <span>{n} hidden from view · this session only · not counted in capacity</span>
    <button type="button" class="btn btn--sm" onClick={() => controller.showAllHidden()}>
      Show all
    </button>
  </div>;
}
