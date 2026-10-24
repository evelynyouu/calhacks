"""The current Nessie ids also live in Nessie (a "FirstFund registry" account), so a server whose disk
was wiped (Render's free plan) or that holds an old copy of data/nessie_ids.json finds the persona's
current accounts instead of falling back to demo data."""
import json
import shutil

import requests

from core import nessie, profile, seed
from core.profile import get_profile
from tests.test_live import fake  # noqa: F401


def restart(ids_file_contents=None):
    """A server restart: in-memory state gone, local files as given (None = left as they are)."""
    if ids_file_contents is not None:
        nessie.save_ids(ids_file_contents)
    nessie._registry.update(account=None, pulled=False)
    nessie._SHADOW.clear()
    nessie.clear_cache()
    shutil.rmtree(profile.CACHE_DIR, ignore_errors=True)


def registry_records(fake):
    accounts = [a["_id"] for a in fake.accounts.values() if a["nickname"] == nessie.REGISTRY_NICKNAME]
    return [json.loads(d["description"][len(nessie.REGISTRY_TAG):])
            for d in fake.txns["deposits"].values() if d["account_id"] in accounts]


def test_seed_publishes_the_ids_to_nessie(fake):
    records = registry_records(fake)
    assert records, "seeding should write the ids to the registry account"
    newest = max(records, key=lambda r: r["_seq"])
    assert newest == nessie.load_ids()


def test_restart_with_an_old_ids_file_finds_the_current_accounts(fake):
    before = get_profile("sam")
    old_file = nessie.load_ids()
    nessie.reset("sam")                       # live reset: Sam moves to new accounts
    seed.wait_for_background()                # the old accounts are deleted
    current = nessie.load_ids()["sam"]
    assert current != old_file["sam"]

    restart(old_file)                         # Render redeployed: the committed (old) file is back
    p = get_profile("sam")
    assert nessie.offline_reason() is None, nessie.offline_reason()
    assert p == before
    assert nessie.load_ids()["sam"] == current


def test_another_machine_resets_while_this_one_runs(fake):
    get_profile("sam")
    mine = nessie.load_ids()
    nessie.reset("sam")                       # stands in for a reset on another server
    seed.wait_for_background()
    nessie.save_ids(mine)                     # ...this server's file still has the old ids
    nessie.clear_cache()
    get_profile("sam")
    assert nessie.offline_reason() is None, nessie.offline_reason()


def test_a_newer_local_file_beats_an_older_registry_copy(fake):
    ids = nessie.load_ids()
    ids["_seq"] = max(r["_seq"] for r in registry_records(fake)) + 1
    ids["note"] = "local is newer"
    restart(ids)
    get_profile("sam")
    assert nessie.load_ids().get("note") == "local is newer"


def test_registry_down_does_not_break_reset(fake, monkeypatch):
    real = nessie._send

    def registry_down(method, path, body=None, params=None):
        if path == "/accounts" or (method == "POST" and path.endswith("/deposits")
                                   and body["description"].startswith(nessie.REGISTRY_TAG)):
            raise requests.ConnectionError("registry unreachable")
        return real(method, path, body, params)

    monkeypatch.setattr(nessie, "_send", registry_down)
    monkeypatch.setattr(nessie.time, "sleep", lambda s: None)   # skip the retry backoff
    restart()
    nessie.reset("sam")
    seed.wait_for_background()
    assert nessie.offline_reason() is None
    assert get_profile("sam").checking_balance == 575


def test_dry_run_publishes_nothing(fake):
    before = len(registry_records(fake))
    lines = []
    with seed.dry_run(lines.append):
        seed.seed(["sam"], reset="sam", save=False)
    assert len(registry_records(fake)) == before
    assert not any(nessie.REGISTRY_TAG in line for line in lines)


def test_seeder_with_an_old_file_does_not_undo_a_newer_reset(fake, monkeypatch):
    old_file = nessie.load_ids()
    nessie.reset("sam")                       # another machine resets Sam
    seed.wait_for_background()
    current = nessie.load_ids()["sam"]
    restart(old_file)
    monkeypatch.setenv("NESSIE_MODE", "fixture")   # e.g. `python -m scripts.seed_nessie --reset maria` without live in .env
    seed.seed(["maria"], reset="maria", spares=False)
    newest = max(registry_records(fake), key=lambda r: r["_seq"])
    assert newest["sam"] == current
    assert nessie.load_ids()["sam"] == current
