"use client";

import { useEffect, useRef } from "react";

const paths = {
  logo: "/animations/logo-lockup-reveal.json",
  fingerprint: "/animations/privacy-fingerprint.json",
  rings: "/animations/verification-rings.json",
} as const;

export function OfficialMotion({ kind, className = "" }: { kind: keyof typeof paths; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
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
    }, { rootMargin: "80px" });
    observer.observe(node);
    return () => { disposed = true; observer.disconnect(); animation?.destroy(); };
  }, [kind]);
  return <div ref={ref} className={className} aria-hidden="true" />;
}
