import type { ReactNode } from "react";

interface ModalProps {
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
}

export function Modal({ title, description, children, onClose }: ModalProps) {
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
      onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}
    >
      <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <header className="modal-header">
          <div><h2 id="modal-title">{title}</h2>{description ? <p>{description}</p> : null}</div>
          <button className="modal-close" type="button" onClick={onClose} aria-label="关闭弹框">×</button>
        </header>
        {children}
      </section>
    </div>
  );
}
