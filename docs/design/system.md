# FirstFund design system

Calm and trustworthy, like a good bank statement. The numbers are the hero; the chrome stays out of the way.

Tokens live in two places that must agree: the Tailwind config in `app/templates/base.html` (utility classes) and
the CSS variables at the top of `app/static/app.css` (components). `nav.css` and `tape.css` use the same values.

## Type

One family: **Public Sans** (US Web Design System, OFL), vendored in `app/static/vendor/fonts/` (400, 500, 600) so
it works offline. No display serif; the logo is Public Sans 600.

| Token | Size / line height | Use |
|---|---|---|
| `text-xs` | 12 / 16 | Labels above figures, captions, table headers, footnotes |
| `text-sm` | 14 / 20 | Body copy in cards, table rows, buttons, inputs |
| `text-base` | 16 / 24 | Page body, chat messages, a row's key figure |
| `text-lg` | 20 / 28 | Card and page headings, secondary hero figures |
| `text-xl` | 28 / 36 | The one hero number per page (fund value), landing headline |

Tailwind's other sizes are removed from the config, so `text-2xl` and up don't exist.

Weights: 400 body, 500 labels / buttons / links, 600 headings and key figures. Nothing heavier.

**Numbers:** `body` sets `font-variant-numeric: tabular-nums`, so every digit lines up. Right-align numbers in
tables (`.num`). Dollars: `$1,234.56` for balances, values and gains; whole dollars only where the amount is whole
by definition (paycheck, invest amount, starting amount). Percents: one decimal for weights and returns (`+0.8%`),
two for day moves in quote lists. Gains carry a sign (`+$27.06`), losses a minus sign.

## Spacing

4px base. Use only these steps: **4, 8, 12, 16, 24, 32, 48** (`1, 2, 3, 4, 6, 8, 12` in Tailwind).

- Inside a card: 24px padding (16px under 640px); 16px between groups; 8px between a label and its content; 4px
  between a figure and its caption.
- Between cards and page sections: 24px (`gap-6`, `space-y-6`).
- Page top/bottom: 24px under the header, 48px before the footer.

## Color

| Token | Value | Use | Contrast on white |
|---|---|---|---|
| `ink` | `#0b0f0d` | Primary text, active states, selected controls | 19.1 |
| `ink-2` | `#3c403e` | Secondary text | 10.7 |
| `muted` | `#5c625f` | Labels, captions, footnotes | 6.2 (5.7 on paper) |
| `line` | `#e3e6e4` | Dividers, card borders | (non-text) |
| `line-strong` | `#8b918e` | Input and button borders | 3.3 (meets 3:1 for UI) |
| `paper` | `#f4f5f4` | Quiet surfaces: notices, hovers, user chat bubbles | |
| `moss-600` (gain) | `#0b7a3e` | **Only** gains and the primary button | 5.4 (4.9 on paper) |
| `moss-700` | `#075c2e` | Primary button hover | 8.1 |
| `clay-500` (loss) | `#b42318` | **Only** losses and errors | 6.6 (6.0 on paper) |

Not used for: links (ink, underlined), active navigation (white text on a lighter row), badges, chart series that
aren't gains/losses (neutral ramp), decorative icons. Every text color above passes WCAG AA (4.5:1) on white and on
`paper`. The old `sun` token (a gray named after a yellow) is gone; `moss-500` now equals `moss-600` because the
brighter green failed AA (3.4:1).

## Shape

- Cards: 8px radius (`rounded-lg`, `.card`), 1px `line` border, no shadow.
- Buttons, inputs, chips, choice rows: 6px (`rounded-md`).
- `rounded-full` only for: status badges (`.badge`: tier, demo/live), avatars, progress tracks, chart swatches.
- One level of card. Inside a card, separate groups with whitespace and `divide-line`, not nested bordered boxes.
  Callouts inside a card use `.notice` (paper fill, ink left rule).

## Motion

No gradients, bounce, blur, shadows-on-hover or lift-on-hover. Transitions are 120–150ms on color/border only
(chat messages fade in 150ms). `prefers-reduced-motion` turns transitions off and stops the ticker scroll
(`tape.css`).

## Components (`app/static/app.css`)

| Role | Class |
|---|---|
| Primary action (one per view) | `.btn` (green) |
| Secondary action | `.btn-ghost` (outlined) |
| Quiet action | `.btn-quiet` (text, paper on hover) |
| Inline link | `.link` (ink, underline) |
| Input | Tailwind border + `line-strong` (set globally); `.composer-box`, `.num-box` for composite inputs |
| Choice / toggle | `.choice-btn` (rows), `.scale-btn`, `.chip`, `.prompt-chip` |
| Section heading in a card | `.eyebrow` (14/600, sentence case; no uppercase) |
| Figure group | `<dl>` of `.stat` (label 12 muted, value 16/600), no boxes |
| Status | `.badge` (pill) |
| Table | `.tbl`, numbers in `.num` |
| Callout | `.notice` |
| Empty state | `.empty` (bold line + one muted sentence) |
| Disclosure | `partials/disclosure.html` (12px muted, always visible) and the footer |

Icons: 20×20 viewBox, `stroke="currentColor"`, stroke width 1.6, round caps and joins, no fills. No emoji or
text glyphs (✓ ☆ →) as icons.

Focus: every focusable element gets a 2px ink outline with 2px offset (`:focus-visible`, in `app.css`; the dark
sidebar uses white).

## Copy

Sentence case for headings, buttons and labels. Say what a number is and its period ("Total gain, since you
started", "Today"). No hype ("powerful", "smart", "effortless"), no arrows in buttons. The chat stays friendly;
the chrome stays plain. Labels for simulated or demo data are never hidden or shrunk below 12px.
