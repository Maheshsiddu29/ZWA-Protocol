"use client";

import { useEffect, useRef, useState } from "react";

export function FilmDialog() {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const [checking, setChecking] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    fetch("/video/zwa-promo.mp4", { method: "HEAD" }).then(response => {
      if (active) setFailed(!response.ok);
    }).catch(() => { if (active) setFailed(true); }).finally(() => { if (active) setChecking(false); });
    setChecking(true);
    return () => { active = false; };
  }, [open]);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    const close = () => setOpen(false);
    node.addEventListener("close", close);
    return () => node.removeEventListener("close", close);
  }, []);
  return <>
    <button className="text-link text-link-light" type="button" onClick={() => setOpen(true)}>Watch the Film <span aria-hidden="true">↗</span></button>
    <dialog ref={dialog} className="film-dialog" aria-label="ZWA promotional film">
      <div className="film-dialog-head"><span>ZWA / The film</span><button type="button" onClick={() => setOpen(false)} aria-label="Close film">Close ×</button></div>
      {checking && <p className="film-caption">Checking film asset…</p>}
      {open && !failed && !checking && <video controls preload="none" poster="/video/zwa-poster.webp" onError={() => setFailed(true)}><source src="/video/zwa-promo.mp4" type="video/mp4" />Your browser cannot play this film.</video>}
      {failed && <div className="film-unavailable"><img src="/video/zwa-poster.webp" alt="ZWA promotional film end card" /><p>The film file is absent from the supplied asset package. The official storyboard and motion assets remain available on this page.</p></div>}
      <p className="film-caption">28-second promotional film · user-controlled playback</p>
    </dialog>
  </>;
}
