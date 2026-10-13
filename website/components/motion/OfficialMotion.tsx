"use client";

import { useEffect, useRef, useState } from "react";

const paths = {
  logo: "/animations/logo-lockup-reveal.json",
  fingerprint: "/animations/privacy-fingerprint.json",
  rings: "/animations/verification-rings.json",
} as const;
const fallbacks = {
  logo: "/brand/zwa-logo-light.svg",
  fingerprint: "/illustrations/privacy-fingerprint.svg",
  rings: "/illustrations/verification-rings.svg",
} as const;

export function OfficialMotion({ kind, className = "" }: { kind: keyof typeof paths; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let disposed = false;
    let animation: import("lottie-web").AnimationItem | undefined;
    const observer = new IntersectionObserver(async entries => {
      if (!entries[0]?.isIntersecting) { animation?.pause(); return; }
      if (animation) { animation.play(); return; }
      const lottie = (await import("lottie-web")).default;
      if (disposed) return;
      animation = lottie.loadAnimation({ container: node, renderer: "svg", loop: false, autoplay: true, path: paths[kind] });
      animation.addEventListener("DOMLoaded", () => setReady(true));
    }, { rootMargin: "80px" });
    observer.observe(node);
    return () => { disposed = true; observer.disconnect(); animation?.destroy(); };
  }, [kind]);
  return <div className={className} aria-hidden="true"><div ref={ref} className="motion-canvas" />{!ready && <img className="motion-fallback" src={fallbacks[kind]} alt="" />}</div>;
}
