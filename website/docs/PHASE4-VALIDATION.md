# Phase 4 local visual and integration review

## Source and safety

- Base: backend HEAD `5275e4f` on `integration/zwa-checkout-poc`; 45 commits ahead of `origin/main` at inspection time. The original checkout had untracked `.cursor/` and `design-assets/`; neither was reset or overwritten.
- Worktree: `ZWA-Brand-Integration`, branch `integration/phase4-brand-website`. A separate existing detached `ZWA-UI-review` worktree was left untouched.
- Website import: only `website/` from local remote-tracking ref `origin/fix/m3-remediation` (`0c4293b`). No full-branch merge, push, PR, or deployment.
- Original brand package is ignored in the integration branch. Selected production copies only are committed under `website/public/`.

## Baseline UI review

The team site had a usable Next.js route structure, responsive navigation/footer, and technical diagrams. It ran a predominantly navy page flow and repeated similar dark technical sections. The hero and supporting copy overclaimed eligibility and settlement proof behavior, and the demo was a simulated walkthrough. Phase 4 retains the route architecture and navigation pattern, replaces the visual story with the official black-to-light brand progression, and uses real local API responses.

## Page structure

- Home: cinematic official poster hero; light asset story; three concepts with separate maturity labels; rooted-lineage proof sequence; recorded checkout evidence; developer/research links; restrained closing lockup.
- `/protocol`: provenance, eligibility, settlement and their current evidence boundaries.
- `/demo`: real rooted-lineage scenario/proof evaluation and a separate read-only recorded checkout panel.
- `/developers`: actual verifier and checkout route contract and status semantics.
- `/research`, `/about`, `/privacy`, `/terms`: concise supporting pages with corrected claims and data-flow language.

## Brand and motion

Exact colors and easing come from `tokens/design-tokens.json` and `tokens/tokens.css`; the eight-page official guide was visually reviewed. Inter Variable is bundled locally and used for body and display optical sizing; the outlined official wordmark is used for logos. Lottie Web is dynamically imported only when an animation enters view, pauses when it exits, and is skipped under reduced motion. Static supplied SVGs remain available as visual fallbacks.

The source package does not contain any encoded hero or promotional video listed in its README. The hero therefore displays its official poster. The film dialog is user-controlled and reports that the film preview is unavailable instead of playing fabricated media. See `ASSET-INVENTORY.md` for the exact missing paths.

## Functional validation

- `npm ci`: pass, clean lockfile install.
- `npm run lint` (`tsc --noEmit`): pass.
- `npm run build`: pass, 12 static pages and four dynamic API routes.
- `npm audit --omit=dev --audit-level=moderate`: zero vulnerabilities after a narrow PostCSS override to 8.5.29. Next remains at 15.5.27.
- `python3 -m unittest checkout.test_checkout`: eight passed with loopback permission. An initial sandbox-only run could not bind loopback; the rerun passed.
- `lineage/scripts/check.sh`: pass, including fixture SHA-256 pins, frozen source checks, formatting, clippy with warnings denied, unit/adversarial/golden/pinned tests, docs tests, and unchanged Cargo lock.
- Live site proxy against the existing verifier binary: `valid-rooted-n3` → VERIFIED; `pending-a4` → PENDING_FINALITY; `checkpoint-ckn` → CHECKPOINT_TRUST_REQUIRED; `reorg` → INVALIDATED_BY_REORG; `tampered-proof` → REJECTED. Direct canonical v3 file proof → VERIFIED with eligibility NOT_EVALUATED and settlement NOT_EXECUTED.
- All four checkout GET endpoints returned 200. Recorded session/receipt remained nonactionable with finality UNKNOWN, purchased-note lineage UNSUPPORTED, and eligibility NOT_EVALUATED. An unlisted checkout route returned 404.
- Browser checks: 1440, 1024, 390, and 320 pixel homepage widths had no horizontal overflow. The 320 pixel menu opened and closed with Escape. The film dialog opened and showed the missing-film state. The verifier UI displayed the actual VERIFIED result and checkout evidence, and showed explicit unavailable states after both backends were stopped. Reduced-motion behavior is implemented in CSS and Lottie initialization but could not be browser-emulated in the available in-app browser.

## Screenshots

- `screenshots/home-1440.jpg`, `home-1024.jpg`, `home-390.jpg`, `home-320.jpg`: final homepage hero at requested widths. `home-full-320.jpg` shows the complete narrow layout.
- `screenshots/demo-1440.jpg`: real verifier result.
- `screenshots/checkout-1440.jpg`: recorded checkout response.
- `screenshots/closing-1440.jpg`: completed official logo reveal.

## Known limitations and next risks

1. The encoded 12-second hero loop, 28-second film, and 42-second extended film are absent from the supplied package. Video playback and optimization could not be validated. The source code exists, but rendering it on this machine would require additional media tooling and original Inter Display availability and might not reproduce the official exports byte-for-byte.
2. The exact standalone Inter Display font file is not included in the supplied package. The site uses local Inter Variable with optical sizing as the display fallback. The official outlined wordmark SVG remains exact.
3. The local APIs must be started separately. The site does not infer success when either one is unavailable. The website has not been deployed or exposed beyond loopback.
4. Reduced-motion preferences and keyboard modal behavior have code paths and partial browser checks, but automated reduced-motion emulation and full screen-reader validation remain to be done before publication.

Owner visual review is required before further feature work or any remote change.
