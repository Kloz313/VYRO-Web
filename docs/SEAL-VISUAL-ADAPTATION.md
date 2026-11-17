# Seal visual identity adaptation

## Source examined

`Seal-Original-UI-Style-Extraction.zip` was inspected in full. It contains:

- `README.txt`
- `DESIGN-GUIDE-AR.md`
- `design-tokens.json`
- `palette-preview.svg`

## Facts confirmed by the supplied extraction

- Seal requests the Light interface style.
- The interface uses SwiftUI with `NeumorphicKit` / `Neumorphic`.
- Extracted button symbols include `SoftButtonStyle`, `SoftDynamicButtonStyle`, `SoftDynamicButton`, and `SoftButtonPressedEffect`.
- Extracted shadow symbols include `SoftInnerShadowViewModifier` and `SoftOuterShadowViewModifier`.
- A continuous-corner `RoundedRectangle` is present.
- The confirmed numeric palette is `#D1D9E6`, `#303135`, `#E8E8E8`, `#3E3F46`, `#232323`, and `#FFFFFF`.

## Web implementation mapping

The supplied extraction explicitly says that it cannot reliably assign each extracted color to a specific original UI role. Therefore, the following mapping is an implementation adaptation for VYRO Web, not a claim about Seal's exact original role assignments:

- `#D1D9E6`: main page and raised-surface base.
- `#E8E8E8`: subtle inset/support surface.
- `#303135` and `#232323`: primary text and high-contrast controls.
- `#3E3F46`: secondary text and the darker component of soft shadows.
- `#FFFFFF`: light component of soft shadows and button text.

The web stylesheet recreates the confirmed Neumorphic language with paired soft outer shadows, inset control shadows, rounded continuous-feeling corners, and a pressed interaction state. Numeric spread/radius values were not copied from the extraction because its guide marks those observations as unresolved.

## Layout and language work

- Rebalanced the hero, URL input, result card, media metadata, settings sections, advanced settings, and download-progress area.
- Established consistent spacing and responsive single-column fields on narrow screens.
- Rewrote Arabic labels and helper text for consistent professional terminology.
- Kept the workflow element IDs and request behavior; the visible theme switch was removed to keep the confirmed Light presentation.
- The design is a source-informed web adaptation. Pixel-identical reproduction cannot be claimed because the supplied archive contains no original screenshots or Swift source files.

## Validation

- Existing automated backend suite: 28 tests passed.
- Frontend JavaScript syntax check passed with `node --check`.
- Static HTML parsing passed.
- No live browser/device visual comparison against Seal screenshots was possible from the supplied archive because it contains no UI screenshots.
