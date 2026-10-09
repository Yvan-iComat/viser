/** Returns true if the element is a text-entry control (INPUT, TEXTAREA,
 * SELECT, or contentEditable). Used to suppress global keyboard handlers --
 * camera movement keys and modifier-key cursor updates -- while the user is
 * typing into a form field. */
export function isFormElement(target: EventTarget | Element | null): boolean {
  const el = target as HTMLElement | null;
  if (el === null) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  return el.isContentEditable;
}

/** Returns true if the event target is a form control, or sits inside a widget
 * that handles its own keyboard shortcuts (marked `data-captures-keys`, e.g.
 * the node graph, where arrow keys move the selected nodes). Global keyboard
 * handlers should ignore such events. */
export function capturesKeyboard(
  target: EventTarget | Element | null,
): boolean {
  if (isFormElement(target)) return true;
  return (
    target instanceof Element && target.closest("[data-captures-keys]") !== null
  );
}
