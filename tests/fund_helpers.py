"""Give a demo persona a built fund without clicking through the chat."""
from app import state
from core import fund, onboarding
from core.intake import QUESTIONS

ANSWERS = {"goal": "A house", "starting_amount": 3000, "paycheck_pct": 10, "risk_priority": "steady",
           "risk_range": "medium", "max_drop": 15, "horizon": "7to15",
           "interests": {"themes": ["tech"], "unmatched": []}}


# One answer set per demo persona, for golden tests and the explanation page. Alex's is the app's preset
# (app/routes/pages.DEMO_PRESETS); Maria, Jordan and Sam start at the intake in the demo.
PERSONA_ANSWERS = {
    "maria": {"goal": "An emergency fund, then a car", "starting_amount": 500, "paycheck_pct": 5,
              "risk_priority": "protect", "risk_range": "narrow", "max_drop": 10, "horizon": "3to7",
              "interests": {"themes": ["staples"], "unmatched": []}},
    "jordan": {"goal": "Our wedding in a few years", "starting_amount": 1000, "paycheck_pct": 8,
               "risk_priority": "steady", "risk_range": "medium", "max_drop": 20, "horizon": "3to7",
               "interests": {"themes": ["gaming", "communications"], "unmatched": []}},
    "sam": {"goal": "Buy a house someday", "starting_amount": 3000, "paycheck_pct": 10,
            "risk_priority": "growth", "risk_range": "medium", "max_drop": 25, "horizon": "7to15",
            "interests": {"themes": ["tech", "healthcare"], "unmatched": []}},
}


def make_fund(cid: str = "sam", **answers) -> state.Session:
    s = state.get(cid)
    s.linked = s.linked or onboarding.link(cid)   # every customer links a bank before the intake
    s.fund.answers = {**ANSWERS, **answers}
    fund.start(s.fund)
    s.step = len(QUESTIONS)
    s.say("Your fund is ready.")
    return s


def _alex():
    from app.routes.pages import DEMO_PRESETS
    return dict(DEMO_PRESETS["alex"]["answers"])


PERSONA_ANSWERS["alex"] = _alex()
