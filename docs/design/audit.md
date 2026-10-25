# Design pass: audit and what changed

Screenshots: `before/` and `after/`, `<page>-1280.png` and `<page>-500.png`, persona Sam (built fund, one paycheck),
Maria for the intake (`dashboard`), Jordan for the bank link. Fixture bank, simulated prices. System:
[system.md](system.md).

| What read as generated | Count / where | Change |
|---|---|---|
| `rounded-full` on everything | 36 | Kept only for avatars, progress tracks, swatches and real status (`.badge`: tier, Demo, Nessie, Suggested). Chips, prompt chips, Switch customer and the backtest period picker are 6px controls now. |
| Gradients | 2 (chat avatars) + gradient fill on price chart | Solid ink avatar; flat chart fills. |
| `animate-bounce` | 3 (typing dots) | "FirstFund is writing a reply…" in plain text, `role="status"`. |
| `backdrop-blur`, lift-on-hover, shadows | 4 uses / landing cards / card and button shadows | Removed. Cards are a 1px light border, no shadow. |
| Fraunces + Inter | Every page | Public Sans only, vendored (works offline); no Google Fonts request. Logo is Public Sans 600, monochrome. |
| Type: sm/xs with jumps to 2xl–5xl | 129 xs, 198 sm, 30 at 2xl+ | 5 steps (12/14/16/20/28) enforced in the Tailwind config; one 28px figure per page (fund value, price, report total). Page and card titles are 20px or the 14px section heading. |
| Ad hoc spacing (0.5/1/1.5/2/2.5/5) | 27× `mt-0.5`, 9× `mt-1.5`, etc. | Mapped to 4/8/12/16/24/32/48; cards 24px padding (16 on phones), 24px between sections everywhere. |
| ✓ / ☆ glyphs | 8 | Stroke SVG checks (report plan ladder, account security, agentic rules), "Add to watchlist" plain. Arrows in buttons/links (→ ←, 22) removed; external links get a stroke icon + "opens in a new tab" for screen readers; data arrows ("Checking → Investing") kept. |
| Everything in its own bordered card, cards in cards | Autopilot stats, Simulate box, report money/finances/recommendations, how-built slices, money accounts | One level of card. Figure groups are a row of label/value on a divider; nested boxes became dividers or a left rule (sectors). Callouts use `.notice`. |
| Uppercase tracked eyebrows | 56 eyebrows + 15 `uppercase` | Sentence-case 14px semibold headings (`<h2 class="eyebrow">`), trade sides "Buy"/"Sell". |
| Green on everything | links, active nav, tier pill, Nessie badges, reasons, pie, chart lines, progress bars, inflows, selected chips | Green only for gains and the primary button. Links are ink + underline; active nav is a lighter row with a white bar; pie and non-gain chart series use a neutral ramp. |
| Red for non-losses | "All prices are simulated" note, off-target weights, delete button, owed card balance | Notice / dotted underline / neutral button / ink. Red kept for losses and form errors (see below). |
| Low-contrast gray text | `text-ink/40`–`/55` (111 uses, 2.7–4.3:1) | Two tokens: `ink-2` (10.7:1) and `muted` (6.2:1). Bright green `#0e9f4f` (3.4:1) retired. |
| Misnamed `sun` token | config only | Removed (unused); neutrals are `paper`, `line`, `muted`, `ink-2`. |
| Gray stat tiles with centered numbers | Autopilot, report, markets, backtest, how-built | Left-aligned label/value rows; tables use `.tbl` with right-aligned `.num` columns; tabular figures on `body`. |
| Mixed dollar formats on one page | `$3,232` next to `$3,232.06` (autopilot, account, backtest) | 2 decimals for balances and values; whole dollars only where the amount is whole by rule (paycheck invest, monthly flow bars). |
| Generic hero copy and centered-card grid on the landing page | 5 equal cards + "Start →" | One primary action ("Start as a new customer") and a plain list of demo customers. |
| Hype / vague copy | "AI rundown", "Powered by Capital One Nessie", "Spending → Stocks", "Alpha vs…", "Invest of each paycheck", "Nessie-powered" | "Today's rundown" + "Written by Claude" badge, "Source: Capital One Nessie (demo bank)", "Where you spend", "Auto-trade minus buy & hold", "Share of each paycheck to invest", "Uses your bank data". |
| Mismatched icons | stroke 1.6 in nav, 2 in search, text glyphs elsewhere | 20×20, stroke 1.6, round caps everywhere new; search icon matched. |
| Focus rings | only some controls | Global `:focus-visible` 2px ink outline (white in the sidebar); chips and choice rows get it through the peer input. |
| Motion | 200–220ms slides, unguarded fade-in | 120–150ms; `prefers-reduced-motion` disables transitions, the chat fade-in and the ticker scroll. |

## Kept on purpose

- **Red for form errors** (start page). The brief says red only for losses; errors are the one exception, since
  they need to read as errors to everyone. Easy to change in `start.html` if you disagree.
- **Several green "Save" buttons on Account**: each section is its own form, so each has one primary action.
- **Money flow bars in whole dollars**: `tests/test_money.py` asserts `$4,100`-style amounts there.
- **Copy the tests pin**: "Question 1 of 8", "From FirstFund", "Your mix", "Check my targets", "A suggested change is
  waiting", "Read the full version", "Moved to investing</span>", "Show all N positions".
- **Robinhood wording** ("Agentic trading active", "Live via Robinhood"): restyled only. Whether it's honest is
  already an open item in CLAUDE.md and isn't a design call.
