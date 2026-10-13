# Official brand asset inventory and website mapping

Source package: `ZWA-Protocol/design-assets/` in the original checkout. It is untracked and preserved unchanged. The integration worktree contains selected copies only.

## Official sources used

- `brand/zwa-logo-dark.svg` → dark hero navigation lockup; `brand/zwa-logo-light.svg` → scrolled navigation, closing section, and footer. Both contain the outlined wordmark.
- `brand/zwa-logo-mono-white.svg` → retained production variant for imagery/blue backgrounds. `brand/favicon.svg` → browser favicon and app icon.
- `illustrations/asset-land.svg`, `asset-home.svg`, `asset-person.svg`, `asset-tokens-onchain.svg` → the light-stage problem story.
- `illustrations/privacy-fingerprint.svg` → privacy transition and reduced-motion fallback.
- `illustrations/verification-rings.svg`, `verification-rings-empty.svg`, `ring-provenance.svg`, `ring-eligibility.svg`, `ring-settlement.svg` → approach, demo placeholder, and concept states.
- `animations/logo-lockup-reveal.json` → closing lockup animation; `privacy-fingerprint.json` → privacy transition; `verification-rings.json` → approach. All are loaded only in view and skipped under reduced motion.
- `video/zwa-hero-poster.webp` → black hero fallback; `video/zwa-poster.webp` → film poster; `video/storyboard/01-land.webp` through `09-lockup.webp` → retained visual story references.
- `tokens/design-tokens.json`, `tokens/tokens.css` → website brand token copies. Palette and easing values were checked against the eight-page `source/brand-guide.pdf`.

## Sources inspected but not copied

The package also contains mono black/white marks, gradient and solid standalone marks, app icons, twelve stroke icons, token illustrations, nature drop, watching eye, alternate fingerprints, progress rings, `logo-reveal.json`, and `verification-rings-loop.json`. These remain in `design-assets/` because the site has no current use for them. The original artwork is never recolored or stretched.

## Missing promised video exports

The package README lists `video/zwa-hero-loop.webm`, `video/zwa-hero-loop.mp4`, `video/zwa-hero-loop-720p.mp4`, `video/zwa-promo.mp4` (28 seconds), and `video/zwa-promo-extended-42s.mp4`. None exists in the supplied directory or its subdirectories. The editable rendering source exists under `source/motion-source/`, but the final encoded exports are absent. The homepage uses the official hero poster instead of inventing motion. The film control reports unavailability when opened; it does not silently substitute a fabricated film.
