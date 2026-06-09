"""Decision-analysis offload: the serializable job round-trips through JSON
(the queue contract) and run_decision_analysis_job persists a row — whether it
runs inline or on the out-of-band worker."""
import json

import pytest

from poker.decision_analyzer import run_decision_analysis_job

pytestmark = pytest.mark.integration  # builds a real analyzer + DB-backed repos


def _sample_job():
    """Mirror the job controllers._analyze_decision builds (opponent_infos as
    flat dicts; trace fields as strings)."""
    return {
        "analyze_kwargs": {
            "game_id": "test",
            "player_name": "Hero",
            "hand_number": 1,
            "phase": "FLOP",
            "player_hand": ["Ah", "Kh"],
            "community_cards": ["Qh", "Jh", "2d"],
            "pot_total": 600,
            "cost_to_call": 500,
            "player_stack": 100,
            "num_opponents": 1,
            "action_taken": "call",
            "player_bet": 0,
            "all_players_bets": [(0, False), (500, False)],
            "opponent_infos": [
                {
                    "name": "Villain",
                    "position": "button",
                    "hands_observed": 10,
                    "vpip": 0.3,
                    "pfr": 0.2,
                    "aggression": 1.5,
                    "preflop_action": "open_raise",
                    "postflop_aggression_this_hand": "bet",
                }
            ],
        },
        "bounded_options": None,
        "intervention_trace_json": None,
        "strategy_pipeline_snapshot_json": None,
        "player_bet": 0,
        "big_blind": 100,
        "auto_label_extra": None,
    }


def test_job_round_trips_through_json():
    """The job must survive a JSON encode/decode — that's the queue contract."""
    restored = json.loads(json.dumps(_sample_job()))
    assert restored["analyze_kwargs"]["opponent_infos"][0]["name"] == "Villain"


def test_run_job_persists_a_row(repos):
    # Simulate the full queue round-trip (tuples -> lists, opponent_infos as dicts).
    job = json.loads(json.dumps(_sample_job()))
    analysis_repo = repos["decision_analysis_repo"]
    decision_id = run_decision_analysis_job(job, analysis_repo, repos["capture_label_repo"])
    assert decision_id is not None
    row = analysis_repo.get_decision_analysis(decision_id)
    assert row is not None
    assert row["player_name"] == "Hero"
    assert row["action_taken"] == "call"


def test_run_job_without_capture_label_repo(repos):
    """capture_label_repo is optional — analysis still persists."""
    job = json.loads(json.dumps(_sample_job()))
    decision_id = run_decision_analysis_job(job, repos["decision_analysis_repo"], None)
    assert decision_id is not None
