# Demo script (about 4 minutes)

Written against the `lance/fund-builder` design, checked by running the flow below end to end. Button names are the ones on the page.

## Before you start (10 minutes ahead)
- Open the Render URL once so the free tier is awake (a cold start takes 20-60 seconds), then open `/health`.
- Open **Alex** (`/p/alex`) and **Sam** (`/p/sam`) once so prices are loaded. On Render the first load after a sleep also fetches real prices, which takes a few seconds.
- If Sam or Alex was used before, press **Start over** on the persona's main page. State is in memory: a restart resets everything.
- Have a screen recording of this exact run as a fallback (venue wifi can kill Claude and the CDN).

## 1. Alex: the report (60 seconds)
Alex's fund is a year old, $52K, tier 4. Open **Alex → Report**.
- **Headline.** "Your fund vs your benchmark." Say: the benchmark is the same core funds at the fund's own stock/bond split, with the same deposits. We compare against *that* because a fund with bonds shouldn't be judged against the S&P 500; the S&P is shown too.
- **What moved your fund.** Dollars per holding; they add up to the market move.
- **Your money.** "Your own money did X%, the fund did Y%": the first counts when each deposit landed, the second removes timing.
- **Your finances, and where the fund fits.** Paycheck share of the budget, cash cushion in months, what a 30% market fall would do against the drop Alex said he could live with.
- Point at the line under the headline: **"All prices here are simulated"** or **"Prices are real Nasdaq closing prices"**. Say which is true when you present.

## 2. Sam: from intake to a fund (90 seconds)
Open **Sam**. Answer the questions in the chat. A quick set that gives a moderate fund with two interests:

| Question | Answer |
|---|---|
| What are you investing for? | Buy a house in 5 years |
| Starting amount | 3000 |
| Percent of each paycheck | 10 |
| Which matters most | Steady growth with small dips |
| $1,000 for a year | the middle range |
| Drop you could live with | 20 |
| Comfort with swings | 6 |
| Interests | Technology, Healthcare |

Result: moderate fund (about VTI 28%, VXUS 12%, BND 45%, XLK 7.5%, XLV 7.5%), fee 0.04% a year.

## 3. Make things happen (60 seconds)
1. **Paycheck arrives.** Sam's $2,050 paycheck at 10% puts $205 into the fund the moment it lands.
2. **Market drops 10%.** Watch the value fall; then open **Report**. The dip recommendation talks about *Sam's own stocks* (what share of the fund they are), not the S&P 500.
3. On a recommendation with trades, press **Line up these trades for my OK**, then **Confirm trades**. Nothing trades until you confirm; say so.
4. **Got a raise** shows the paycheck, and the investing amount, growing.

## 4. Ask the chat (30 seconds)
Ask "how am I doing?" or "why did the dip suggestion appear?". The answers come from the report's numbers; Claude only explains them. Say: Python computes every number, Claude never does.

## If something goes wrong
- **Chat is slow or offline:** it falls back to scripted answers from the same numbers. Keep going.
- **A page won't load:** the app is probably asleep. Wait 30 seconds and reload.
- **No internet at the venue:** Claude and live prices are off; prices are simulated and the page says so. Tailwind, htmx and Chart.js are bundled (only the fonts come from Google, and the page falls back to system fonts).

## Be straight about
- Trades go to a practice account at simulated or delayed prices. No real money moves.
- The 10-year range is built on assumptions about each fund (shown on the report), not a forecast.
- Fund income is estimated, and look-through company and sector shares are approximate.
- FirstFund is a guide, not a licensed advisor.
