# Project identity and artwork

The display name is **WhatsApp MCP**. `whatsapp-mcp` remains the technical plugin/repository identifier used by the private preview; that identifier should be reviewed before public distribution. WhatsApp is named descriptively to identify the supported application, not to imply endorsement.

The icon and README cover are original SVG artwork created for this project: stacked message cards, an audio waveform and a forest/mint palette. There is no telephone-logo shape, imported WhatsApp app icon or Meta logo. Both SVG sources and PNG exports are included under the MIT license.

Assets are in `plugins/whatsapp-mcp/assets/`:

- `icon.svg` / `icon.png`: 512 × 512; used for the Codex card/composer logo.
- `banner.svg` / `banner.png`: 1600 × 680; used in the README.

The SVG sources can be edited directly. PNGs were rendered with CairoSVG; the runtime plugin does not depend on CairoSVG, ImageMagick or any graphics library. To regenerate, install CairoSVG in a separate development environment and render each source with `cairosvg.svg2png`. No font files or proprietary application assets are bundled.

The decision to use original artwork follows the [Meta WhatsApp brand guidelines](https://www.meta.com/pt-br/brand/resources/whatsapp/whatsapp-brand/), checked on 2026-09-04. They restrict using the WhatsApp logo or other brand resources as the identity of another product and prohibit implying partnership or endorsement. This note records the design decision, not legal clearance for a future public release or ownership of third-party marks.
