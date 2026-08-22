import { type ReactNode, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

const getTarget = () =>
  globalThis.document?.getElementById("topbar-context") ?? null;
const subscribe = (onStoreChange: () => void) => {
  const observer = new MutationObserver(onStoreChange);
  observer.observe(document.body, { childList: true, subtree: true });
  return () => observer.disconnect();
};

export function TopbarPortal({ children }: { children: ReactNode }) {
  const target = useSyncExternalStore(subscribe, getTarget, () => null);
  return target ? createPortal(children, target) : null;
}
