"""Intake follow-ups: one short extra question, only when an answer is contradictory, implausible or
doesn't apply.  OWNER: Lance.

Most people answer the 8 questions and never see one. When a rule fires, the chat asks ONE follow-up
that resolves it, then the intake carries on. At most MAX_FOLLOWUPS per intake. Every follow-up can be
waved off ("Keep my answer", or "Skip it" for the purely additive interest suggestions), and keeping
leaves the answers exactly as they were. Advise, don't judge.

Pure Python: rules read the answers (by question KEY, so they skip themselves if a key isn't in the
intake) and the bank profile; every number in a follow-up's text is computed here.

    fu = check(answers, "paycheck_pct", profile, asked=["unclear_goal"])   # -> FollowUp | None
    shown = apply(fu, answers, value="40")                                 # updates answers in place

How an answer changes `answers`:
  choice   the option's `effects` are written into answers (e.g. {"horizon": "lt3"})
  money    / slider: the typed number replaces answers[fu.target]
  chips    picked themes are added to answers["interests"]["themes"]; the typed phrase they stand in
           for leaves answers["interests"]["unmatched"]
  keep     nothing changes
Each non-keep answer also appends a plain sentence to answers["followup_notes"] (the fund-ready
message mentions it).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from core import intake
from core.intake import AnswerError, Question
from core.portfolio import THEME_LABELS
from core.state import SIM_START

# ---------- Thresholds (each one says why) ----------

# Two at most: the intake should stay quick; past that, extra questions feel like a quiz.
MAX_FOLLOWUPS = 2

# Risk answers are each mapped to 0..1. A gap this wide between two of them (e.g. "Protecting what I put
# in" = 0 vs swing comfort 9/10 = 0.89, or the widest $1,000 range = 1 vs a 5% max drop = 0.1) means they
# point at different funds; smaller gaps are normal mixed feelings and averaging handles them.
RISK_SPREAD = 0.6

# When the customer picks one of two clashing answers, the other moves this far toward it (0.5 = halfway,
# then the nearest allowed value; a tie goes toward the chosen answer). Halfway, not all the way: both were
# real answers. E.g. "Protecting what I put in" chosen over swing comfort 9/10 -> swing comfort 5/10.
RISK_PULL = 0.5

# Growth (3) and Aggressive (4) can fall a lot in any one year; that only matters if the money is needed soon.
HIGH_RISK_LEVEL = 3

# Money needed within this many years counts as near-term ("wedding in 2027", "house next year").
NEAR_YEARS = 3

# Nothing invested: offer this paycheck share or this starting amount (small, easy to change later).
SUGGESTED_PCT = 5
SUGGESTED_START = 100

# A goal shorter than this many letters ("x", "ok") or in VAGUE_GOALS gets a gentle choice of common goals.
MIN_GOAL_LETTERS = 3
VAGUE_GOALS = {"idk", "i dont know", "i don't know", "dunno", "not sure", "no idea", "unsure", "money",
               "more money", "make money", "cash", "nothing", "stuff", "things", "anything", "everything",
               "none", "na", "n/a", "whatever", "test", "asdf", "?"}

# Typed "Other" interests that matched no fund: suggest themes whose label or keywords look at least this
# similar (difflib ratio; "helthcare" -> Healthcare 0.95, "solr" -> Clean energy 0.89, "biotek" -> Biotech 0.77;
# "bitcoin" vs "biotech" 0.57 and "solr" vs "silver" 0.6 stay out). At most MAX_SUGGESTIONS, a quick pick.
SIMILARITY_MIN = 0.7
MAX_SUGGESTIONS = 3

KEEP = "keep"
# Same keys as the intake's horizon question (core/intake.py), so either one fills answers["horizon"] the same way
HORIZON_OPTIONS = [("lt3", "Under 3 years"), ("3to7", "3 to 7 years"), ("7to15", "7 to 15 years"),
                   ("15plus", "15 years or more"), ("unsure", "Not sure yet")]
HORIZON_LABELS = dict(HORIZON_OPTIONS)

# Goal cards that are usually spent soon (unless typed words give a later date)
NEAR_GOALS = {"safety", "travel"}
# Typed "Something else" goals that are usually spent soon (unless the text says otherwise)
_NEAR_GOAL = re.compile(r"\b(cars?|wedding|tuition|emergency fund|emergencies|safety net|rainy day|vacation|"
                        r"trip|honeymoon)\b")
_SOMEDAY = re.compile(r"\b(someday|some day|one day|eventually|long[- ]term|long run|in the future|later on|"
                      r"no rush|forever)\b")
_NEAR_PHRASE = re.compile(r"\b(this year|next year|next month|this summer|this winter|soon|asap|"
                          r"in a (?:few|couple of) months|in (?:a|one|two|1|2) years?|within (?:a|one|two|1|2) years?)\b")
_IN_N = re.compile(r"\b(?:in|within) (\d{1,2}) (years?|months?)\b")
_YEAR = re.compile(r"\b(20\d\d)\b")
_AGE = re.compile(r"\b(?:at|by|before|age|when i'?m|when i am) (\d{2})\b")


@dataclass
class FollowUp:
    key: str                                   # the rule's name
    after: str                                 # the question key it follows
    text: str                                  # plain default wording (Python; numbers computed here)
    kind: str                                  # choice | money | slider | chips (the intake's input kinds)
    options: list[tuple[str, str]] = field(default_factory=list)    # choice / chips: (value, label)
    effects: dict[str, dict] = field(default_factory=dict)          # choice: value -> answers updates
    notes: dict[str, str] = field(default_factory=dict)             # choice: value -> note for the fund message
    target: str = ""                           # money / slider: the answers key it sets
    note: str = ""                             # money / slider / chips: note template ({shown})
    min: float = 0
    max: float = 100
    step: float = 1
    default: float | None = None
    unit: str = ""
    help: str = ""
    keep_label: str = "Keep my answer"
    facts: list[str] = field(default_factory=list)                  # what Claude may reword from
    sources: dict[str, str] = field(default_factory=dict)           # chips: theme -> the typed phrase

    @property
    def question(self) -> Question:
        """The same Question shape the intake uses, so the composer renders the matching input."""
        return Question(self.target or self.key, self.kind, self.text, help=self.help, options=self.options,
                        min=self.min, max=self.max, step=self.step, default=self.default, unit=self.unit)


# ---------- Helpers ----------

def _money(v: float) -> str:
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"


def _questions() -> dict[str, Question]:
    return {q.key: q for q in intake.QUESTIONS}      # read each call, so a changed intake is respected


def _has_horizon_question() -> bool:
    return "horizon" in _questions()


def _risk_keys(answers: dict) -> list[str]:
    return [q.key for q in intake.QUESTIONS if q.risk and q.key in answers]


def _last_risk_key() -> str | None:
    keys = [q.key for q in intake.QUESTIONS if q.risk]
    return keys[-1] if keys else None


def normalize(q: Question, v) -> float:
    """A risk answer -> 0 (most careful) .. 1 (most adventurous)."""
    if q.kind == "choice":
        values = [o for o, _ in q.options]
        return values.index(v) / (len(values) - 1)
    return (float(v) - q.min) / (q.max - q.min)


def _snap(q: Question, x: float, toward: float):
    """0..1 position -> the nearest allowed answer; an exact tie goes to the `toward` side (the chosen one)."""
    up = toward > x
    n = len(q.options) - 1 if q.kind == "choice" else (q.max - q.min) / q.step
    raw = x * n
    k = math.floor(raw + 0.5) if up else math.ceil(raw - 0.5)
    k = int(max(0, min(n, k)))
    if q.kind == "choice":
        return q.options[k][0]
    v = round(q.min + k * q.step, 4)
    return int(v) if v == int(v) else v


def describe(q: Question, v) -> str:
    """A risk answer in the customer's own terms, for showing two side by side."""
    if q.kind == "choice":
        label = dict(q.options).get(v, str(v))
        return "$1,000 for a year " + label[0].lower() + label[1:] if label.startswith("Ends ") else label
    if q.key == "max_drop":
        return f"I could live with a {v:g}% drop in a year"
    if q.key == "swing_comfort":
        return f"Comfort with big swings: {v:g} out of {q.max:g}"
    return f"{q.prompt} {v:g}{q.unit}"


def _shown(q: Question, v) -> str:
    if q.kind == "choice":
        return dict(q.options).get(v, str(v))
    return f"{v:g}{q.unit}" if q.kind == "slider" else f"{v:g} out of {q.max:g}"


def timeline(goal: str) -> str | None:
    """'near' (money needed within NEAR_YEARS), 'far' (a later date, an age, or 'someday'), or None (not said)."""
    t = (goal or "").lower()
    this_year = SIM_START.year
    years = [int(y) - this_year for y in _YEAR.findall(t)]
    spans = [int(n) / (12 if unit.startswith("month") else 1) for n, unit in _IN_N.findall(t)]
    if _NEAR_PHRASE.search(t) or any(0 <= y < NEAR_YEARS for y in years) or any(s < NEAR_YEARS for s in spans):
        return "near"
    if years or spans or _AGE.search(t) or _SOMEDAY.search(t):
        return "far"
    return None


def _note(answers: dict, text: str) -> None:
    if text:
        answers.setdefault("followup_notes", []).append(text)


def _horizon(key: str, after: str, text: str, help: str, facts: list[str]) -> FollowUp:
    return FollowUp(key, after, text, "choice", options=list(HORIZON_OPTIONS),
                    effects={v: {"horizon": v} for v, _ in HORIZON_OPTIONS},
                    help=help, facts=facts)   # no note: the fund message reads answers["horizon"] itself


# ---------- Rules (each returns a FollowUp or None) ----------

def unclear_goal(answers, after, profile, asked):
    """A typed "Something else" goal too short or too vague to explain the fund against ("idk", "x"):
    offer the goal cards again, never block. A picked card can't be unclear, so only "other" is checked."""
    if after != "goal" or intake.goal_key(answers) != intake.OTHER_GOAL:
        return None
    typed = intake.goal_text(answers)
    raw = typed.strip().lower()
    t = re.sub(r"[^a-z' /?]", "", raw).strip(" ./")
    if len(re.sub(r"[^a-z]", "", raw)) >= MIN_GOAL_LETTERS and t not in VAGUE_GOALS:
        return None
    opts = [(v, intake.GOAL_LABELS[v]) for v in ("retirement", "home", "safety", "wealth")]
    return FollowUp("unclear_goal", after, "No problem if you're not sure yet. Is it close to one of these?",
                    "choice", options=opts, effects={v: {"goal": v, "goal_text": ""} for v, _ in opts},
                    help="Your fund works the same either way; this just helps me explain it.",
                    facts=[f"The customer typed {typed!r} as their goal."])


def start_over_balance(answers, after, profile, asked):
    """Starting amount larger than everything in their accounts: confirm or edit."""
    if after != "starting_amount" or profile is None or "starting_amount" not in answers:
        return None
    amount = float(answers["starting_amount"])
    total = profile.checking_balance + profile.cash_cushion + profile.invested_balance
    if amount <= total:
        return None
    where = "checking, savings and investments" if profile.invested_balance else "checking and savings"
    text = (f"{_money(amount)} is more than the {_money(total)} in your {where} right now. "
            f"Keep {_money(amount)}, or type a different amount?")
    return FollowUp("start_over_balance", after, text, "money", target="starting_amount",
                    note="You changed your starting amount to {shown}.",
                    help="The starting amount is practice money in this demo, so either works.",
                    facts=[f"Starting amount typed: {_money(amount)}.", f"Checking + savings"
                           + (" + investments" if profile.invested_balance else "") + f": {_money(total)}."])


def nothing_invested(answers, after, profile, asked):
    """$0 to start and 0% of each paycheck: the fund would stay empty."""
    if after != "paycheck_pct" or not {"starting_amount", "paycheck_pct"} <= answers.keys():
        return None
    if float(answers["starting_amount"]) > 0 or float(answers["paycheck_pct"]) > 0:
        return None
    pay = profile.paycheck_amount if profile else 0
    pct_label = f"Invest {SUGGESTED_PCT}% of each paycheck" + (
        f" ({_money(math.floor(pay * SUGGESTED_PCT / 100))})" if pay else "")
    opts = [("pct", pct_label), ("start", f"Start with {_money(SUGGESTED_START)}")]
    return FollowUp("nothing_invested", after,
                    "With $0 to start and 0% of each paycheck, nothing would be invested yet. Want to change one?",
                    "choice", options=opts,
                    effects={"pct": {"paycheck_pct": SUGGESTED_PCT}, "start": {"starting_amount": SUGGESTED_START}},
                    notes={"pct": f"You chose to invest {SUGGESTED_PCT}% of each paycheck, so your fund gets money every payday.",
                           "start": f"You chose a {_money(SUGGESTED_START)} starting amount, so your fund starts with money."},
                    help="Totally fine to keep both at zero and change it later.", 
                    facts=["Starting amount: $0.", "Paycheck share: 0%."])


def paycheck_over_leftover(answers, after, profile, asked):
    """The paycheck share is more than what's left of a paycheck after bills: confirm, with the dollars."""
    if after != "paycheck_pct" or profile is None or "paycheck_pct" not in answers:
        return None
    pay, income = profile.paycheck_amount, profile.monthly_income
    if pay <= 0 or income <= 0:
        return None
    pct = float(answers["paycheck_pct"])
    invest = math.floor(pay * pct / 100)                    # same rounding as fund.paycheck
    bills = round(profile.monthly_bills * pay / income)     # each paycheck's share of the monthly bills
    left = max(0, pay - bills)
    if invest <= left:
        return None
    fits = max(p for p in range(0, 101) if math.floor(pay * p / 100) <= left)
    q = intake.BY_KEY.get("paycheck_pct") or _questions().get("paycheck_pct")
    text = (f"{pct:g}% is {_money(invest)} of each {_money(pay)} paycheck. Your bills take about {_money(bills)} "
            f"of each paycheck, which leaves about {_money(left)}. Keep {pct:g}%, or pick a new percent?")
    return FollowUp("paycheck_over_leftover", after, text, "slider", target="paycheck_pct",
                    note="You changed your paycheck share to {shown}.", min=q.min if q else 0, max=q.max if q else 100,
                    step=q.step if q else 1, default=fits, unit="%",
                    help=f"Up to {fits}% fits in what's left after bills. It's your call.",
                    facts=[f"Paycheck share picked: {pct:g}% = {_money(invest)} per paycheck.",
                           f"Paycheck: {_money(pay)}.", f"Bills per paycheck: about {_money(bills)}.",
                           f"Left after bills: about {_money(left)}.", f"Largest share that fits: {fits}%."])


def contradictory_risk(answers, after, profile, asked):
    """Two risk answers far apart: show both, the customer picks; the other moves RISK_PULL toward it."""
    if after != _last_risk_key():
        return None
    qs = _questions()
    keys = _risk_keys(answers)
    if len(keys) < 2:
        return None
    pos = {k: normalize(qs[k], answers[k]) for k in keys}
    lo = min(keys, key=lambda k: pos[k])
    hi = max(keys, key=lambda k: pos[k])
    if pos[hi] - pos[lo] < RISK_SPREAD:
        return None
    a, b = describe(qs[lo], answers[lo]), describe(qs[hi], answers[hi])
    mid = pos[lo] + RISK_PULL * (pos[hi] - pos[lo])
    new_hi = _snap(qs[hi], mid, toward=pos[lo])      # picked the careful one: the bold answer comes down
    new_lo = _snap(qs[lo], pos[hi] - RISK_PULL * (pos[hi] - pos[lo]), toward=pos[hi])
    notes = {
        "careful": (f'Two of your risk answers pointed different ways. You chose \"{a}\", so your answer to '
                    f'\"{qs[hi].prompt}\" moved halfway toward it, from {_shown(qs[hi], answers[hi])} '
                    f'to {_shown(qs[hi], new_hi)}.'),
        "bold": (f'Two of your risk answers pointed different ways. You chose \"{b}\", so your answer to '
                 f'\"{qs[lo].prompt}\" moved halfway toward it, from {_shown(qs[lo], answers[lo])} '
                 f'to {_shown(qs[lo], new_lo)}.'),
    }
    return FollowUp("contradictory_risk", after,
                    f'Two of your answers point different ways: "{a}" and "{b}". Which sounds more like you?',
                    "choice", options=[("careful", a), ("bold", b)],
                    effects={"careful": {hi: new_hi}, "bold": {lo: new_lo}}, notes=notes,
                    help="No wrong answer. I'll move the other one halfway toward it.",
                    facts=[f"Answer 1: {a}.", f"Answer 2: {b}."])


def near_term_high_risk(answers, after, profile, asked):
    """A goal that's usually spent soon (a safety net, travel, or typed words like "wedding in 2027"), growth-
    leaning risk answers, and no timeline ("Not sure yet", or an intake without the horizon question):
    ask once when the money is needed. Runs after the horizon question, or the last risk question without one."""
    if after != ("horizon" if _has_horizon_question() else _last_risk_key()):
        return None
    if answers.get("horizon") not in (None, "unsure"):
        return None
    g, typed = intake.goal_key(answers), intake.goal_text(answers)
    when = timeline(typed)
    near_card = g in NEAR_GOALS and when != "far"
    if not (when == "near" or near_card or (g == intake.OTHER_GOAL and when is None and _NEAR_GOAL.search(typed.lower()))):
        return None
    level = intake.risk_level(intake.risk_score(answers))
    if level < HIGH_RISK_LEVEL:
        return None
    label = "growth" if level == 3 else "aggressive growth"
    goal = typed[:60] or intake.GOAL_LABELS.get(g, "")
    fu = _horizon("near_term_high_risk", after,
                  f'Your goal is \"{goal}\", and your answers lean toward {label}. '
                  'When do you expect to need this money?',
                  "Just so I can explain your fund for your timeline. Your answers stay as they are.",
                  [f"Goal: {goal}.", f"Risk answers point to level {level} of 4 ({label})."])
    if "horizon" in answers:   # they already said "Not sure yet": offer only actual timelines, plus Keep
        fu.options = [o for o in fu.options if o[0] != "unsure"]
    return fu


def unmatched_interests(answers, after, profile, asked):
    """Typed interests that matched no theme: resolve to themes/stocks automatically."""
    if after != "interests" or not isinstance(answers.get("interests"), dict):
        return None
    unmatched = answers["interests"].get("unmatched", [])
    if not unmatched:
        return None
    # Niche map takes priority: check it first so "sports teams" gets real stocks, not a broad theme
    from core.interest_map import lookup as _niche_lookup
    stock_hits: dict[str, list[dict]] = {}   # phrase -> [{"symbol": ..., "name": ...}, ...]
    theme_candidates = []
    for phrase in unmatched:
        niche = _niche_lookup(phrase)
        if niche:
            stock_hits[phrase] = [{"symbol": sym, "name": name} for sym, name in niche]
        else:
            theme_candidates.append(phrase)

    # Theme matching only for phrases the niche map didn't claim
    picked = set(answers["interests"].get("themes", []))
    suggestions = suggest_themes(theme_candidates, exclude=picked)
    matched_by_theme = set(suggestions.values())
    still_unmatched = [p for p in theme_candidates if p not in matched_by_theme]

    # LLM (with 5s timeout) then Nasdaq word-by-word as a last resort
    truly_unmatched = []
    for phrase in still_unmatched:
        llm = _llm_stock_lookup(phrase)
        if llm:
            stock_hits[phrase] = llm
        else:
            hits = _nasdaq_fallback(phrase)
            if hits:
                stock_hits[phrase] = hits
            else:
                truly_unmatched.append(phrase)

    if not suggestions and not stock_hits:
        return None

    # Build display text for the confirmation message
    added = [f"{THEME_LABELS[t]} for \"{p}\"" for t, p in suggestions.items()]
    for phrase, hits in stock_hits.items():
        if len(hits) == 1:
            added.append(f"{hits[0]['symbol']} ({hits[0]['name']}) for \"{phrase}\"")
        else:
            syms = ", ".join(f"{h['symbol']} ({h['name']})" for h in hits[:3])
            suffix = f" + {len(hits) - 3} more" if len(hits) > 3 else ""
            added.append(f"{syms}{suffix} for \"{phrase}\"")
    note = "Added " + "; ".join(added) + "."
    if truly_unmatched:
        note += " Couldn't match: " + ", ".join(f'"{p}"' for p in truly_unmatched) + "."

    from core import market as _mkt
    from core.portfolio import ETFS
    all_symbols = [h["symbol"] for hits in stock_hits.values() for h in hits
                   if h["symbol"] not in ETFS and _mkt.known(h["symbol"])]

    # Store what to apply in sources/effects so apply() does the mutation, not check()
    return FollowUp("unmatched_interests", after, note, "chips",
                    options=[], keep_label="Got it", facts=[],
                    sources=suggestions,
                    effects={"_auto": {"_themes": list(suggestions.keys()),
                                       "_stocks": all_symbols,
                                       "_unmatched_clear": list(matched_by_theme | set(stock_hits.keys()))}})


# Order matters within a step: the first rule that fires is asked.
# (goal_no_timeline was removed with the goal cards: it asked "when?" for a typed goal with no date, which the
# horizon question and the goal's suggested horizon now cover.)
RULES = [unclear_goal, start_over_balance, nothing_invested, paycheck_over_leftover, contradictory_risk,
         near_term_high_risk, unmatched_interests]


def _llm_stock_lookup(phrase: str) -> list[dict]:
    """Ask Claude for 2-3 publicly traded stocks for a niche interest phrase.
    Returns [] when no API key is set or on any error. Hard 5s timeout so it never hangs."""
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return []
    try:
        import json
        import anthropic
        client = anthropic.Anthropic(timeout=5.0)
        msg = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": (
                    f'Name 2-3 publicly traded US stocks most closely tied to the interest: "{phrase}". '
                    'Respond ONLY with a JSON array, e.g. [{"symbol":"NKE","name":"Nike"}]. '
                    'Real tickers only. No commentary.'
                )
            }]
        )
        return json.loads(msg.content[0].text)
    except Exception:
        return []


def _nasdaq_fallback(phrase: str) -> list[dict]:
    """Try Nasdaq autocomplete on the full phrase, then word-by-word as a backup."""
    try:
        from core.feed import search as _search
        results = _search(phrase)
        if results:
            return [results[0]]
        # Try each word separately, take first hit across all words
        for word in phrase.split():
            if len(word) < 3:
                continue
            r = _search(word)
            if r:
                return [r[0]]
    except Exception:
        pass
    return []


def suggest_themes(phrases: list[str], exclude=()) -> dict[str, str]:
    """Unmatched phrases -> {theme: phrase} for the closest themes by string similarity."""
    scored = []
    for phrase in phrases:
        p = phrase.lower().strip()
        tokens = [p] + p.split()
        for theme, words in intake.INTEREST_WORDS.items():
            if theme in exclude or theme not in THEME_LABELS:
                continue
            cands = [THEME_LABELS[theme].lower()] + [re.sub(r"\\w\*|\?|\\", "", w) for w in words]
            best = max(SequenceMatcher(None, tok, c).ratio() for tok in tokens for c in cands)
            if best >= SIMILARITY_MIN:
                scored.append((best, theme, phrase))
    out: dict[str, str] = {}
    for _, theme, phrase in sorted(scored, key=lambda s: -s[0]):
        if theme not in out and len(out) < MAX_SUGGESTIONS:
            out[theme] = phrase
    return out


def check(answers: dict, step_just_answered: str, profile=None, asked=()) -> FollowUp | None:
    """The follow-up to ask after `step_just_answered`, if any. `asked`: rule keys already asked this intake."""
    asked = list(asked)
    if len(asked) >= MAX_FOLLOWUPS:
        return None
    for rule in RULES:
        if rule.__name__ in asked:
            continue
        fu = rule(answers, step_just_answered, profile, asked)
        if fu:
            return fu
    return None


def apply(fu: FollowUp, answers: dict, value: str = "", values: list[str] | None = None,
          keep: bool = False) -> str:
    """Write a follow-up's answer into `answers`; return what the chat shows as the user's message.
    Raises AnswerError (plain words) if the input can't be used. keep=True changes nothing."""
    if keep:
        return fu.keep_label
    if fu.kind == "choice":
        labels = dict(fu.options)
        if value not in labels:
            raise AnswerError("Pick one of the options, or keep your answer.")
        answers.update(fu.effects.get(value, {}))
        _note(answers, fu.notes.get(value, ""))
        return labels[value]
    if fu.kind == "chips":
        # Auto-resolve path: no options to pick, just apply what check() decided
        auto = fu.effects.get("_auto")
        if auto is not None and not keep:
            interests = answers.setdefault("interests", {"themes": [], "unmatched": []})
            new_themes = list(dict.fromkeys(list(interests.get("themes", [])) + auto["_themes"]))
            interests["themes"] = new_themes
            interests["unmatched"] = [u for u in interests.get("unmatched", []) if u not in auto["_unmatched_clear"]]
            if auto["_stocks"]:
                answers["stocks"] = list(dict.fromkeys(answers.get("stocks", []) + auto["_stocks"]))
            return fu.keep_label
        # Manual chip-selection path (legacy)
        picked = [t for t in dict.fromkeys(values or []) if t in fu.sources]
        if not picked:
            return fu.keep_label
        interests = answers.setdefault("interests", {"themes": [], "unmatched": []})
        interests["themes"] = list(dict.fromkeys(list(interests.get("themes", [])) + picked))
        covered = {fu.sources[t] for t in picked}
        interests["unmatched"] = [u for u in interests.get("unmatched", []) if u not in covered]
        shown = ", ".join(THEME_LABELS[t] for t in picked)
        _note(answers, f"You added {shown} in place of " + ", ".join(f'"{c}"' for c in covered) + ".")
        return shown
    stored, shown = intake.parse(fu.question, value)       # money / slider: same parsing as the intake
    answers[fu.target] = stored
    _note(answers, fu.note.format(shown=shown))
    return shown
