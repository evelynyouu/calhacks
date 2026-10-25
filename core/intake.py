"""The intake questions, how each answer is read, and the risk score.  OWNER: Lance.

Each question says what input the chat shows for it:
  text   open-ended, typed            money   a dollar amount, typed
  slider a number in a range          scale   1 to 10
  choice up to 5 options              chips   pick many, plus "Other" (typed)

The risk questions ask what the customer is comfortable with, not what they'd "do" in a test:
there are no wrong answers. The two preference answers (what matters most, the $1,000 range) map to
0..1 and their average is the risk score. The time horizon and the biggest drop they can live with
are limits, not preferences: core/builder.py uses them as ceilings on the mix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from core.portfolio import BOND_MAX, THEME_LABELS, build_portfolio, level_for_score, worst_year

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


INTEREST_OPTIONS = sorted(THEME_LABELS.items(), key=lambda kv: kv[1])
# The steadiest mix's real worst year (2022, when bonds fell too): no answer below this can be met.
STEADIEST_WORST = abs(worst_year(build_portfolio(1, [], bond_share=BOND_MAX).holdings)[1])
MAX_MONEY = 10_000_000

QUESTIONS: list[Question] = [
    Question("goal", "text", "What are you investing for?",
             help="A house, retirement, travel, a safety net, or just growing your money. Anything works."),
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
        if value not in labels:
            raise AnswerError("Pick one of the options.")
        return value, labels[value]
    if q.kind == "chips":
        labels = dict(q.options)
        picked = [v for v in dict.fromkeys(values or []) if v in labels]
        matched, unmatched = match_interests(other)
        picked += [t for t in matched if t not in picked]
        shown = [labels[t] for t in picked] + [f"“{u}”" for u in unmatched]
        return {"themes": picked, "unmatched": unmatched}, ", ".join(shown) or "None, keep it broad"
    raise AnswerError(f"Unknown question type {q.kind}")


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
