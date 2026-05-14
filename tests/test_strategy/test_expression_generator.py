"""Tests for ExpressionGenerator (Layer 3 LLM narration)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from poker.prompt_manager import PromptManager
from poker.strategy.expression_context import ExpressionContext
from poker.strategy.expression_generator import ExpressionGenerator


@pytest.fixture
def context():
    return ExpressionContext(
        action_taken='raise',
        raise_to=600,
        hand_cards=['As', 'Ah'],
        community_cards=[],
        phase='pre_flop',
        pot_size=300,
        opponent_count=2,
        personality_name='Test Character',
        play_style='analytical and patient',
        default_attitude='thoughtful',
        verbal_tics=["'I see your move.'", "'Hmm, interesting.'"],
        physical_tics=['*adjusts glasses*'],
        drama_level='notable',
        drama_tone='confident',
    )


@pytest.fixture
def prompt_manager():
    return PromptManager()


def test_generate_success_populates_all_fields(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(
        content=json.dumps({
            'dramatic_sequence': ['*leans forward*', "I'm in."],
            'inner_monologue': 'Pocket rockets, time to build the pot.',
            'hand_strategy': 'Apply pressure pre-flop with a premium hand.',
            'bluff_likelihood': 25,
        })
    )

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(context)

    assert result['dramatic_sequence'] == ['*leans forward*', "I'm in."]
    assert 'Pocket rockets' in result['inner_monologue']
    assert 'pressure' in result['hand_strategy']
    assert result['bluff_likelihood'] == 25
    mock_llm.complete.assert_called_once()


def test_generate_llm_failure_returns_empty(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.side_effect = RuntimeError('network down')

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(context)

    assert result == {
        'dramatic_sequence': [],
        'inner_monologue': '',
        'hand_strategy': '',
        'bluff_likelihood': 0,
    }


def test_generate_bad_json_returns_empty(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content='not valid json {{{{')

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(context)

    assert result['dramatic_sequence'] == []
    assert result['inner_monologue'] == ''


def test_generate_empty_content_returns_empty(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(content='')

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(context)

    assert result['dramatic_sequence'] == []


def test_generate_clamps_bluff_likelihood(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(
        content=json.dumps({
            'dramatic_sequence': [],
            'inner_monologue': '',
            'hand_strategy': '',
            'bluff_likelihood': 500,
        })
    )

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(context)

    assert result['bluff_likelihood'] == 100


def test_generate_handles_non_list_sequence(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(
        content=json.dumps({
            'dramatic_sequence': 'not a list',
            'inner_monologue': 'hi',
            'hand_strategy': '',
            'bluff_likelihood': 0,
        })
    )

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(context)

    assert result['dramatic_sequence'] == []
    assert result['inner_monologue'] == 'hi'


def test_generate_passes_player_name_to_llm(context, prompt_manager):
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(
        content=json.dumps({
            'dramatic_sequence': [],
            'inner_monologue': '',
            'hand_strategy': '',
            'bluff_likelihood': 0,
        })
    )

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    gen.generate(context, game_id='g123')

    kwargs = mock_llm.complete.call_args.kwargs
    assert kwargs['player_name'] == 'Test Character'
    assert kwargs['game_id'] == 'g123'
    assert kwargs['json_format'] is True


def test_generate_populates_capture_id_holder(context, prompt_manager):
    """When capture_id_holder is provided, the _on_captured callback writes the id back."""
    mock_llm = MagicMock()
    # Simulate the LLMClient invoking our enricher to populate _on_captured,
    # then firing the callback as capture_prompt() would post-insert.
    def fake_complete(**kwargs):
        enricher = kwargs.get('capture_enricher')
        if enricher is not None:
            capture_data = enricher({})
            on_captured = capture_data.get('_on_captured')
            if callable(on_captured):
                on_captured(424242)
        return SimpleNamespace(content=json.dumps({
            'dramatic_sequence': ['*nods*'],
            'inner_monologue': 'hm',
            'hand_strategy': 's',
            'bluff_likelihood': 0,
        }))
    mock_llm.complete.side_effect = fake_complete

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    holder = [None]
    gen.generate(context, capture_id_holder=holder)

    assert holder[0] == 424242


def test_generate_without_holder_omits_enricher(context, prompt_manager):
    """No holder => no capture_enricher passed (callers that don't care don't pay)."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(
        content=json.dumps({
            'dramatic_sequence': [],
            'inner_monologue': '',
            'hand_strategy': '',
            'bluff_likelihood': 0,
        })
    )

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    gen.generate(context)

    assert mock_llm.complete.call_args.kwargs.get('capture_enricher') is None


def _capture_messages(mock_llm):
    """Return the (system, user) message contents from the mock call."""
    call = mock_llm.complete.call_args
    messages = call.kwargs.get('messages') or call.args[0]
    by_role = {m['role']: m['content'] for m in messages}
    return by_role.get('system', ''), by_role.get('user', '')


def _capture_prompt(mock_llm) -> str:
    """Backward-compat: return system + user content joined.

    Tests that just look for a substring anywhere in the prompt keep
    working after the system/user split.
    """
    system, user = _capture_messages(mock_llm)
    return f"{system}\n\n{user}"


def _stub_response():
    return SimpleNamespace(content=json.dumps({
        'dramatic_sequence': [],
        'inner_monologue': '',
        'hand_strategy': '',
        'bluff_likelihood': 0,
    }))


def test_system_user_split(context, prompt_manager):
    """System message holds stable persona + format rules; user holds dynamic context."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = _stub_response()

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    gen.generate(context)

    system, user = _capture_messages(mock_llm)

    # System: persona, all 3 mode rule blocks, output spec
    assert 'Test Character' in system  # persona intro
    assert 'OUTPUT FORMAT' in system or 'dramatic_sequence' in system
    assert 'GESTURE-ONLY' in system or 'gesture' in system.lower()
    assert 'SPEAK' in system.upper()
    # Mode indicator is per-turn → user
    assert 'NARRATION MODE FOR THIS TURN' in user
    assert 'SPEAK' in user

    # Dynamic context lives in user, not system
    assert 'Action you took' in user
    assert 'Action you took' not in system


def test_optional_sections_omitted_when_fields_empty(context, prompt_manager):
    """hand_read and recent_actions sections are skipped for default ctx."""
    mock_llm = MagicMock()
    mock_llm.complete.return_value = _stub_response()

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    gen.generate(context)

    prompt = _capture_prompt(mock_llm)
    assert 'Your read on your hand' not in prompt
    assert 'Recent actions at the table' not in prompt
    # Format rules always present (now live in the system message as
    # mode descriptions, picked by the user-side mode_indicator)
    assert 'NARRATION MODES' in prompt
    assert 'MODE: SPEAK' in prompt


def test_optional_sections_render_when_fields_populated(prompt_manager):
    ctx = ExpressionContext(
        action_taken='raise',
        raise_to=600,
        hand_cards=['As', 'Ah'],
        community_cards=['Kd', '7c', '2s'],
        phase='flop',
        pot_size=900,
        opponent_count=2,
        personality_name='Test Character',
        play_style='aggressive',
        default_attitude='confident',
        verbal_tics=[],
        physical_tics=[],
        position='BTN',
        stack_bb=42.0,
        pot_bb=9.0,
        cost_to_call_bb=2.5,
        hand_name='Top Pair Top Kicker',
        recent_actions="Alice raised to 3 BB\nBob called",
    )

    mock_llm = MagicMock()
    mock_llm.complete.return_value = _stub_response()

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    gen.generate(ctx)

    prompt = _capture_prompt(mock_llm)
    # hand_read block present with the hand label
    assert 'Top Pair Top Kicker' in prompt
    # recent_actions block present, with opponents by name
    assert 'Alice raised to 3 BB' in prompt
    assert 'Bob called' in prompt
    # situation block carries new fields
    assert 'BTN' in prompt
    assert '42.0' in prompt        # stack_bb
    assert '9.0' in prompt         # pot_bb
    assert '2.5' in prompt         # cost_to_call_bb


def test_generate_with_empty_tics(prompt_manager):
    ctx = ExpressionContext(
        action_taken='check',
        raise_to=0,
        hand_cards=['7c', '2d'],
        community_cards=[],
        phase='pre_flop',
        pot_size=150,
        opponent_count=1,
        personality_name='Generic Bot',
        play_style='',
        default_attitude='neutral',
        verbal_tics=[],
        physical_tics=[],
    )

    mock_llm = MagicMock()
    mock_llm.complete.return_value = SimpleNamespace(
        content=json.dumps({
            'dramatic_sequence': ['Check.'],
            'inner_monologue': '',
            'hand_strategy': '',
            'bluff_likelihood': 0,
        })
    )

    gen = ExpressionGenerator(mock_llm, prompt_manager)
    result = gen.generate(ctx)

    assert result['dramatic_sequence'] == ['Check.']
