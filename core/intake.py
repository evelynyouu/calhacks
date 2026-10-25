"""The intake questions, how each answer is read, and the risk score.  OWNER: Lance.

Each question says what input the chat shows for it:
  text   open-ended, typed            money   a dollar amount, typed
  slider a number in a range          scale   1 to 10
  choice up to 5 options              chips   pick many, plus "Other" (typed)
A choice can also offer a typed "Something else" box (Question.other); the goal question does.

The risk questions ask what the customer is comfortable with, not what they'd "do" in a test:
there are no wrong answers. The two preference answers (what matters most, the $1,000 range) map to
0..1 and their average is the risk score. The time horizon and the biggest drop they can live with
are limits, not preferences: core/builder.py uses them as ceilings on the mix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from core.portfolio import BOND_MAX, THEME_LABELS, level_for_score, worst_return_for

QuestionKind = Literal["text", "money", "slider", "scale", "choice", "chips"]


@dataclass
class Question:
    key: str
    kind: QuestionKind
    prompt: str
    help: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)   # (value, label)
    min: float = 0
    max: float = 100
    step: float = 1
    default: float | None = None
    unit: str = ""                       # "%" for sliders
    ends: tuple[str, str] = ("", "")     # labels under a scale's 1 and 10
    risk: bool = False                   # counts toward the risk score
    other: str = ""                      # choice: label of a typed "Something else" box (empty = none)


INTEREST_OPTIONS = sorted(THEME_LABELS.items(), key=lambda kv: kv[1])
# The steadiest mix's real worst year (2022, when bonds fell too): no answer below this can be met.
STEADIEST_WORST = abs(worst_return_for(1, [], [], bond_share=BOND_MAX))   # the same check the builder uses
MAX_MONEY = 10_000_000

# The goal is a choice card, or "Something else" typed (stored as "other" plus answers["goal_text"]).
GOAL_OPTIONS = [("home", "Buying a home"), ("retirement", "Retirement"), ("travel", "Travel"),
                ("safety", "A safety net"), ("wealth", "Growing my wealth")]
GOAL_LABELS = dict(GOAL_OPTIONS)
OTHER_GOAL = "other"
# Typed goals that clearly mean exactly one card ("a condo", "retire at 55") are stored as that card,
# with the typed words kept for display. Two matches or none -> "other".
GOAL_WORDS = {
    "home": r"house|home|condo|apartment|down ?payment|mortgage",
    "retirement": r"retire\w*|retirement|pension|401k",
    "travel": r"travel\w*|trips?|vacations?|backpacking",
    "safety": r"emergenc\w*|emergency fund|safety net|rainy day|cushion",
    "wealth": r"grow\w* (?:my )?(?:money|wealth)|wealth|financial freedom",
}
_GOAL_RES = {k: re.compile(r"\b(?:" + w + r")\b") for k, w in GOAL_WORDS.items()}
# The horizon each goal suggests when the customer hasn't said (or isn't sure). It's only a highlighted
# card on the horizon question; the customer picks. "Growing my wealth" (and "other") suggest none: that
# goal doesn't imply a date, and a guessed date would cap the mix for no reason the customer gave.
GOAL_HORIZON = {"safety": "lt3", "home": "3to7", "travel": "3to7", "retirement": "15plus"}
GOAL_HORIZON_WHY = {
    "safety": "a safety net should be there on short notice",
    "home": "a down payment is usually needed within a few years",
    "travel": "trips are usually a few years out at most",
    "retirement": "retirement money usually stays invested for decades",
}

QUESTIONS: list[Question] = [
    Question("goal", "choice", "What are you investing for?",
             help="Pick the closest one, or type your own. It shapes how your fund is explained.",
             options=GOAL_OPTIONS, other="Something else"),
    Question("starting_amount", "money", "How much do you want to put in to start?",
             help="Type an amount in dollars. $0 is fine; your paychecks will build it."),
    Question("paycheck_pct", "slider", "What percent of each paycheck should go into your fund?",
             help="It's invested the moment your paycheck lands. You can change it any time.",
             min=0, max=100, step=1, default=10, unit="%"),
    Question("risk_priority", "choice", "Which matters most to you?", risk=True, options=[
        ("protect", "Protecting what I put in"),
        ("steady", "Steady growth with small dips"),
        ("growth", "Strong growth, even with bigger dips"),
        ("max", "The most growth possible, big swings are fine"),
    ]),
    Question("risk_range", "choice", "Picture $1,000 invested for a year. Which range would you pick?",
             help="Wider ranges can end higher, and can also end lower.", risk=True, options=[
        ("narrow", "Ends between $960 and $1,060"),
        ("medium", "Ends between $900 and $1,150"),
        ("wide", "Ends between $820 and $1,250"),
        ("widest", "Ends between $700 and $1,400"),
    ]),
    Question("max_drop", "slider", "How big a drop in one year could you live with?",
             help=f"As a percent of your fund. We build the riskiest mix whose real worst year (2008 or 2022) stayed "
                  f"within this. Even the steadiest mix lost {STEADIEST_WORST:g}% in 2022, so a smaller number gets "
                  f"the steadiest mix.",
             min=0, max=50, step=5, default=20, unit="%", risk=True),
    Question("horizon", "choice", "When will you need most of this money?",
             help="Money you need soon has less time to recover from a drop, so it sets a limit on the mix.", options=[
        ("lt3", "In under 3 years"),
        ("3to7", "In 3 to 7 years"),
        ("7to15", "In 7 to 15 years"),
        ("15plus", "In 15 years or more"),
        ("unsure", "Not sure yet"),
    ]),
    Question("interests", "chips", "Which industries or themes are you excited about?",
             help="Pick as many as you like, or none to keep it broad. Use Other for anything not listed.",
             options=INTEREST_OPTIONS),
]
BY_KEY = {q.key: q for q in QUESTIONS}

_CHOICE_SCORES = {"risk_priority": ["protect", "steady", "growth", "max"],
                  "risk_range": ["narrow", "medium", "wide", "widest"]}

# Words people type for "Other" -> the theme that covers them
INTEREST_WORDS = {
    "tech": ["tech", "technology", "computers?", "apple", "microsoft", "big tech"],
    "ai_chips": ["ai", "artificial intelligence", "chips?", "semiconductors?", "nvidia", "gpus?"],
    "software": ["software", "cloud", "saas", "apps?"],
    "cyber": ["cyber", "cybersecurity", "security", "hacking"],
    "robotics": ["robots?", "robotics", "automation", "drones?"],
    "healthcare": ["health", "healthcare", "medical", "medicine", "hospitals?", "pharma", "doctors?"],
    "biotech": ["biotech", "genetics", "gene", "drugs?", "vaccines?"],
    "clean_energy": ["clean", "solar", "wind", "renewables?", "climate", "green", "evs?", "electric vehicles?",
                     "sustainab\\w*", "environment\\w*"],
    "oil_gas": ["oil", "gas", "fossil fuels?", "petroleum"],
    "financials": ["banks?", "banking", "finance", "fintech", "insurance", "payments?"],
    "real_estate": ["real estate", "housing", "property", "properties", "reits?", "homes?"],
    "consumer": ["retail", "shopping", "cars?", "fashion", "restaurants?", "travel", "hotels?", "e-?commerce"],
    "staples": ["food", "groceries", "grocery", "household", "beverages?", "drinks?", "coffee"],
    "industrials": ["industrial", "manufacturing", "construction", "airlines?", "railroads?", "trucking",
                    "infrastructure", "transportation"],
    "communications": ["media", "social media", "streaming", "entertainment", "telecom", "music", "movies?",
                       "netflix", "internet"],
    "utilities": ["utilities", "power", "electricity", "water"],
    "space_defense": ["space", "defense", "defence", "aerospace", "military", "rockets?"],
    "gaming": ["gaming", "games?", "video games?", "esports", "e-sports"],
    "emerging": ["emerging", "india", "brazil", "mexico", "developing", "asia"],
    "dividends": ["dividends?", "income", "passive income"],
    "small_caps": ["small caps?", "small companies", "startups?"],
    "gold": ["gold", "precious metals?", "silver"],
}
_INTEREST_RES = {t: re.compile(r"\b(" + "|".join(w) + r")\b") for t, w in INTEREST_WORDS.items()}


class AnswerError(ValueError):
    """The answer can't be used; the message says why, in plain words."""


def parse(q: Question, value: str = "", values: list[str] | None = None, other: str = "") -> tuple[object, str]:
    """Form input -> (stored answer, what the chat shows as the user's message)."""
    value = (value or "").strip()
    if q.kind == "text":
        if not value:
            raise AnswerError("Type a few words so I know what you're aiming for.")
        return value[:300], value[:300]
    if q.kind == "money":
        amount = parse_money(value)
        return amount, f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"
    if q.kind in ("slider", "scale"):
        try:
            n = float(value)
        except ValueError:
            raise AnswerError("Pick a number with the slider or type one in.") from None
        if not q.min <= n <= q.max:
            raise AnswerError(f"Pick a number from {q.min:g} to {q.max:g}.")
        n = round(round(n / q.step) * q.step, 4)
        n = int(n) if n == int(n) else n
        return n, f"{n}{q.unit}" if q.kind == "slider" else f"{n} out of {q.max:g}"
    if q.kind == "choice":
        labels = dict(q.options)
        if value in labels:
            return value, labels[value]
        typed = (other or "").strip()[:300]
        if q.other and typed:
            return OTHER_GOAL, typed
        raise AnswerError("Pick one of the options, or type your own." if q.other else "Pick one of the options.")
    if q.kind == "chips":
        labels = dict(q.options)
        picked = [v for v in dict.fromkeys(values or []) if v in labels]
        matched, unmatched = match_interests(other)
        picked += [t for t in matched if t not in picked]
        shown = [labels[t] for t in picked] + [f"“{u}”" for u in unmatched]
        return {"themes": picked, "unmatched": unmatched}, ", ".join(shown) or "None, keep it broad"
    raise AnswerError(f"Unknown question type {q.kind}")


def record(answers: dict, q: Question, value: str = "", values: list[str] | None = None, other: str = "") -> str:
    """Parse one intake answer and write it into `answers`; returns what the chat shows as the user's message.
    The goal also keeps typed words in answers["goal_text"] (and drops stale ones when a card is picked)."""
    stored, shown = parse(q, value, values, other)
    if q.key == "goal":
        answers.pop("goal_text", None)
        if stored == OTHER_GOAL:
            stored = goal_from_text(shown)
            answers["goal_text"] = shown
    answers[q.key] = stored
    return shown


def goal_from_text(text: str) -> str:
    """Typed goal -> the card it clearly means, else "other"."""
    t = (text or "").lower()
    hits = [k for k, rx in _GOAL_RES.items() if rx.search(t)]
    return hits[0] if len(hits) == 1 else OTHER_GOAL


def goal_key(answers: dict) -> str | None:
    """The structured goal ("home" ... "wealth", or "other"); None if not answered. An older free-text goal
    (a string that isn't a key, e.g. "Retire early") is read through goal_from_text."""
    g = str(answers.get("goal") or "").strip()
    if not g:
        return None
    return g if g in GOAL_LABELS or g == OTHER_GOAL else goal_from_text(g)


def goal_text(answers: dict) -> str:
    """The customer's own words for the goal, if they typed any; "" otherwise."""
    typed = str(answers.get("goal_text") or "").strip()
    if typed:
        return typed
    g = str(answers.get("goal") or "").strip()
    return "" if g in GOAL_LABELS or g == OTHER_GOAL else g


def goal_label(answers: dict) -> str:
    """How the goal reads on screen: their own words if typed, else the card ("Buying a home"); "" if none."""
    return goal_text(answers) or GOAL_LABELS.get(str(answers.get("goal") or ""), "")


def suggested_horizon(answers: dict) -> tuple[str, str] | None:
    """(horizon key, the line shown above the cards) that the goal suggests while the horizon is unanswered
    or "unsure"; else None. A typed goal that already says when ("a house in 2035") suggests nothing."""
    if answers.get("horizon") not in (None, "unsure"):
        return None
    g = goal_key(answers)
    if g not in GOAL_HORIZON:
        return None
    from core.followups import timeline   # late import: core.followups imports this module
    typed = goal_text(answers)
    if timeline(typed) is not None:
        return None
    goal = f"“{typed[:60]}”" if typed else GOAL_LABELS[g][0].lower() + GOAL_LABELS[g][1:]
    return GOAL_HORIZON[g], f"Suggested because your goal is {goal}: {GOAL_HORIZON_WHY[g]}. Pick whichever fits you."


def parse_money(text: str) -> float:
    t = text.lower().replace(",", "").replace("$", "").replace("usd", "").strip()
    mult = 1
    if t.endswith("k"):
        t, mult = t[:-1], 1_000
    try:
        amount = round(float(t) * mult, 2)
    except ValueError:
        raise AnswerError("Type a dollar amount, like 500 or $1,200.") from None
    if not 0 <= amount <= MAX_MONEY:
        raise AnswerError(f"Type an amount from $0 to ${MAX_MONEY:,}.")
    return amount


def match_interests(text: str) -> tuple[list[str], list[str]]:
    """Free text ("solar, pet care") -> (themes we have a fund for, phrases we don't)."""
    themes, unmatched = [], []
    for phrase in re.split(r",|;|/|\band\b|&|\n", (text or "").lower()):
        phrase = phrase.strip(" .!")
        if not phrase:
            continue
        hits = [t for t, rx in _INTEREST_RES.items() if rx.search(phrase)]
        if hits:
            themes += [t for t in hits if t not in themes]
        else:
            unmatched.append(phrase)
    return themes, unmatched


def risk_parts(answers: dict) -> dict[str, float]:
    """Each risk answer on a 0 (most careful) .. 1 (most adventurous) scale."""
    parts = {key: order.index(answers[key]) / (len(order) - 1)
             for key, order in _CHOICE_SCORES.items() if answers.get(key) in order}
    if "max_drop" in answers:
        q = BY_KEY["max_drop"]
        parts["max_drop"] = (answers["max_drop"] - q.min) / (q.max - q.min)
    return parts


def risk_score(answers: dict) -> int:
    """0 (most careful) .. 100 (most adventurous): the average of the two preference answers."""
    prefs = [v for k, v in risk_parts(answers).items() if k in _CHOICE_SCORES]
    return round(100 * sum(prefs) / len(prefs)) if prefs else 50


def risk_level(score: int) -> int:
    """Score -> 1 Conservative .. 4 Aggressive, in equal bands of 25."""
    return level_for_score(score)


def answer_label(key: str, answers: dict) -> str:
    """How an answer reads on screen ("Steady growth with small dips", "15%")."""
    q, v = BY_KEY[key], answers.get(key)
    if key == "goal":
        return goal_label(answers)
    if q.kind == "choice":
        return dict(q.options).get(v, "")
    if q.kind == "slider":
        return f"{v:g}{q.unit}" if v is not None else ""
    return str(v or "")


def interests(answers: dict) -> list[str]:
    return list(answers.get("interests", {}).get("themes", []))


def stocks(answers: dict) -> list[str]:
    """Single companies the customer added (from the Markets tab or chat; not an intake question)."""
    return list(answers.get("stocks", []))
