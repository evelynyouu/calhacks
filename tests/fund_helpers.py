"""Give a demo persona a built fund without clicking through the chat."""
from app import state
from core import fund, onboarding
from core.intake import QUESTIONS

ANSWERS = {"goal": "A house", "starting_amount": 3000, "paycheck_pct": 10, "risk_priority": "steady",
           "risk_range": "medium", "max_drop": 15, "swing_comfort": 5,
           "interests": {"themes": ["tech"], "unmatched": []}}


def make_fund(cid: str = "sam", **answers) -> state.Session:
    s = state.get(cid)
    s.linked = s.linked or onboarding.link(cid)   # every customer links a bank before the intake
    s.fund.answers = {**ANSWERS, **answers}
    fund.start(s.fund)
    s.step = len(QUESTIONS)
    s.say("Your fund is ready.")
    return s
