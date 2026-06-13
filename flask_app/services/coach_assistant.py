"""LLM-powered conversational poker coaching.

Provides a CoachAssistant that wraps the core Assistant class
with a poker-coaching system prompt and stat formatting.
"""

import json
import logging
import os
from collections import defaultdict
from typing import Dict, List, Optional, TypedDict

from core.llm.assistant import Assistant
from core.llm.settings import get_assistant_model, get_assistant_provider
from core.llm.tracking import CallType
from poker.hand_narrator import abbreviate_position, format_action_phrase

from .skill_definitions import get_skill_by_id

logger = logging.getLogger(__name__)

COACH_SYSTEM_PROMPT = """\
You are a professional poker coach helping a player in real-time during a game.

Rules:
- I will provide pre-calculated statistics. Reference them directly - do not recalculate.
- NEVER describe the player's hand as something the cards don't support. If no community cards are shown, the hand ended pre-flop — do not reference a flop/turn/river or any board-made hand (set, flush, straight, two pair). Use only the hand facts given ("Hand:", "Your cards:", "Board:").
- CRITICAL: Only recommend actions from the "Available actions" list. If raise/bet is not listed, don't suggest it (e.g., when all opponents are all-in, you can only call or fold).
- Consider position when giving advice: early position requires tighter ranges, late position allows wider opening ranges.
- Note opponent stack sizes and all-in status — this affects what actions make sense.
- Be concise and actionable. For proactive tips: 1-2 sentences max. For questions: 2-3 short paragraphs.
- Explain the math simply (e.g., "You need 22% equity to call, and you have 45% — easy call")
- A "Recommended action" (computed from the math) may be provided. Default to it. If you advise something different, you MUST say why in one phrase (e.g. a teaching point or a read) — never silently contradict the equity/pot-odds.
- When an opponent archetype is given (e.g. "calling station", "maniac"), use it: say how to exploit it (value-bet thin vs a station; don't bluff a station; trap a maniac).
- An opponent line tagged "EXPLOIT [tendency]: <tell> → <play>" is a confirmed read the strong players at this table are already attacking. When it's relevant to THIS spot, teach it: name the tell in plain words and give the player the play. This is how the human learns to read and punish opponents — it's the most valuable thing you can pass on. Don't dump every read; surface the one that matters here.
- The "YOUR PLAY" section runs those SAME detectors on the PLAYER. A line tagged "LEAK [tendency]: <tell> → fix: <play>" is the player's OWN exploitable tendency — the table reads them this way. When it's relevant to the current decision, name it gently and give the fix ("you've been folding to c-bets a lot — start defending more in position"). Treat the player like any other player at the table: honest about their leaks, with the correction.
- Mention opponent tendencies when relevant
- Be encouraging but honest about mistakes
- Use poker terminology naturally but explain concepts for beginners when asked

RESPONSE FORMAT: Always respond with valid JSON in this exact format:
{
  "advice": "Your coaching message here (1-2 sentences for tips, 2-3 paragraphs for questions)",
  "action": "fold" | "check" | "call" | "raise" | null,
  "raise_to": <total chip amount if action is raise, omit otherwise>
}

- Set "action" to the action you recommend from Available actions, or null if you want the player to figure it out themselves.
- For raises, include "raise_to" with the specific total chip amount (not the raise increment).
- If no specific action recommendation, set "action" to null.
"""

LEARN_MODE_PROMPT = """\
You are in TEACHING mode. The player is learning a new poker concept.
- Explain the concept clearly and simply
- Use examples from the current hand to illustrate
- Be encouraging — they're building a new habit
- Keep explanations to 2-3 sentences max
"""

COMPETE_MODE_PROMPT = """\
You are in COMPETE mode. The player already understands the concept.
- Give brief, actionable reminders (1 sentence)
- Focus on execution, not explanation
- Trust that they know the theory
"""

REVIEW_MODE_PROMPT = """\
You are in REVIEW mode. Analyze what just happened.
- Reference the specific skill being practiced
- Note whether the action was correct for the concept
- Give one concrete takeaway
"""

PROACTIVE_TIP_PROMPT = """\
Given these stats, give the player a brief 1-2 sentence nudge that helps them THINK — \
point out the single most important factor (their position, the price they're getting, an opponent's tendency, their hand's relative strength) or pose a short guiding question. \
Do NOT tell them which action to take and do NOT name fold/check/call/raise — the whole point is that THEY decide. Set "action" to null. \
If a KNOWN LEAK (confirmed) is shown, this is the priority: gently remind them this is a spot where they tend to make this specific mistake (name the spot, and the hand if one is shown), and nudge them toward the solver line — raise-or-fold instead of limping, continuing instead of over-folding, or raising instead of just calling — without naming the action outright. Do not lecture, just the nudge. \
If a WATCHING item is shown instead, give an even softer heads-up — frame it as something you've noticed a couple of times, not a settled habit ("I've seen this from you here once or twice — worth a thought?"). \
Otherwise, if a SKILL FOCUS is listed, aim the nudge at that concept using the current hand. \
No preamble, no greeting.\
"""

HAND_REVIEW_PROMPT = """\
Review this completed hand from the player's perspective. Be concise (3-5 sentences).

Structure your review as:
1. One sentence summarizing what happened
2. One sentence on what the player did well OR the key mistake
3. One sentence of specific advice for similar situations

If SKILL EVALUATIONS FOR THIS HAND are provided above, reference them:
- Cover incorrect evaluations first (1-2 sentences each)
- Then mention correct applications briefly
- Keep each skill's review to 1-2 sentences

If the player provided an explanation, acknowledge their reasoning and compare it with the stats.

Be honest — if they played well, say so briefly. If they made an error, explain what the better play was and why (use pot odds/equity math if relevant). Don't sugarcoat, but don't be harsh either.\
"""


LEAK_FEEDBACK_PROMPT = """\
Given this preflop profile, give the player concise, prioritized feedback (3-5 sentences):
- Name their single biggest preflop leak first, and why it costs chips.
- Quantify it using ONLY the EXACT numbers shown in the profile — the percentages, the solver comparison, and the sample count ("seen N×"). Do not use any hand, position, percentage, or number that is not in the profile.
- Respect the CONFIRMED vs WATCHING split: state confirmed leaks plainly, but frame WATCHING items as small-sample tendencies you're keeping an eye on — not settled mistakes.
- Give one concrete fix that matches the leak — e.g. raise-or-fold instead of limping, open tighter from a position, or defend/3-bet more when facing raises.
- If the profile shows their play tracks the charts, acknowledge it briefly rather than inventing a leak.
Name ONLY hands and positions that appear in the profile above — do not invent or add examples of your own, do not invent stats, and do not reference postflop play. Set "action" to null.\
"""


_MODE_PROMPTS = {
    'learn': LEARN_MODE_PROMPT,
    'compete': COMPETE_MODE_PROMPT,
    'review': REVIEW_MODE_PROMPT,
}


class CoachResponse(TypedDict, total=False):
    """Structured coach response with advice and optional action recommendation."""

    advice: str
    action: Optional[str]  # 'fold', 'check', 'call', 'raise', or None
    raise_to: Optional[int]


def _normalize_action(action: Optional[str], available_actions: List[str]) -> Optional[str]:
    """Normalize LLM action output to canonical form and validate against available actions."""
    if not action:
        return None

    action_lower = action.lower().strip()

    # Map variations to canonical actions
    if action_lower in ('bet', 'raise', 'all-in', 'allin', 'all in', 'all_in'):
        normalized = 'raise'
    elif action_lower in ('check', 'pass'):
        normalized = 'check'
    elif action_lower in ('call', 'match'):
        normalized = 'call'
    elif action_lower in ('fold', 'muck'):
        normalized = 'fold'
    else:
        normalized = action_lower

    # Validate against available actions (bet/raise are interchangeable)
    if normalized == 'raise' and ('raise' in available_actions or 'bet' in available_actions):
        return 'raise'
    if normalized in available_actions:
        return normalized

    # Action not available - return None to fall back to GTO
    logger.warning(
        f"Coach suggested unavailable action '{action}' (normalized: '{normalized}'), ignoring"
    )
    return None


def _parse_coach_response(response: str, coaching_data: Dict) -> CoachResponse:
    """Parse JSON response from coach LLM, falling back gracefully on failure."""
    available_actions = coaching_data.get('available_actions', [])

    try:
        data = json.loads(response)
        advice = data.get('advice', response)
        raw_action = data.get('action')
        raise_to = data.get('raise_to')

        # Normalize and validate action
        action = _normalize_action(raw_action, available_actions)

        # Validate raise_to is a reasonable number (round floats to nearest int)
        if raise_to is not None:
            try:
                raise_to = int(round(float(raise_to)))
                if raise_to <= 0:
                    raise_to = None
            except (ValueError, TypeError):
                raise_to = None

        return CoachResponse(
            advice=advice,
            action=action,
            raise_to=raise_to if action == 'raise' else None,
        )
    except json.JSONDecodeError as e:
        logger.warning(f"Coach response JSON parse failed: {e}, raw: {response[:200]}")
        # Truncate and clean up raw response for display
        cleaned = response.strip()[:500] if response else "I couldn't format my response properly."
        return CoachResponse(
            advice=cleaned,
            action=None,
            raise_to=None,
        )


class CoachAssistant:
    """LLM-powered poker coaching assistant."""

    def __init__(
        self,
        game_id: str,
        owner_id: str,
        player_name: str = '',
        mode: str = '',
        skill_context: str = '',
    ):
        self.mode = mode
        system_prompt = COACH_SYSTEM_PROMPT
        if player_name:
            system_prompt += f"\nThe player's name is {player_name}. Use their name sparingly — at most once every few messages, never in proactive tips."
        if mode and mode in _MODE_PROMPTS:
            system_prompt += f"\n\n{_MODE_PROMPTS[mode]}"
        if skill_context:
            system_prompt += f"\n\n{skill_context}"
        # Coaching runs on the Assistant tier, not the Default tier. The Default
        # tier is a cheap, fast model (8B-class) tuned for in-game flavor/
        # commentary — fine for chatter, but it hallucinates hand facts and
        # gives incoherent strategy when asked to coach (observed: "play your
        # set of fives" on a hand that never saw a flop). Coaching needs a model
        # that can actually reason about the spot, so it uses the Assistant
        # endpoint (the same tier as experiment design/analysis).
        self._assistant = Assistant(
            system_prompt=system_prompt,
            provider=get_assistant_provider(),
            model=get_assistant_model(),
            call_type=CallType.COACHING,
            game_id=game_id,
            owner_id=owner_id,
        )

    def ask(self, question: str, coaching_data: Dict) -> CoachResponse:
        """Answer a coaching question with current game stats as context.

        Returns a CoachResponse dict with 'advice', 'action', and optional 'raise_to'.
        """
        stats_text = _format_stats_for_prompt(coaching_data)
        message = f"Current stats:\n{stats_text}\n\nPlayer question: {question}"
        response = self._assistant.chat(message, json_format=True)
        return _parse_coach_response(response, coaching_data)

    def get_proactive_tip(self, coaching_data: Dict) -> CoachResponse:
        """Generate a brief proactive coaching tip.

        Returns a CoachResponse dict with 'advice', 'action', and optional 'raise_to'.
        """
        stats_text = _format_stats_for_prompt(coaching_data)
        message = f"Current stats:\n{stats_text}\n\n{PROACTIVE_TIP_PROMPT}"
        response = self._assistant.chat(message, json_format=True)
        return _parse_coach_response(response, coaching_data)

    def review_preflop_leaks(self, profile_text: str) -> str:
        """Interpret a player's preflop PROFILE into prioritized feedback.

        Grounded: the coach explains the supplied profile (which is computed from
        real data) — it must not invent hands or stats. Returns the advice text.
        """
        message = f"{profile_text}\n\n{LEAK_FEEDBACK_PROMPT}"
        response = self._assistant.chat(message, json_format=True)
        try:
            data = json.loads(response)
            return data.get('advice') or response.strip()
        except json.JSONDecodeError as e:
            logger.warning(f"Coach leak feedback JSON parse failed: {e}, raw: {response[:200]}")
            return response.strip()

    def review_hand(self, hand_context_text: str) -> str:
        """Generate a post-hand review.

        The shared system prompt mandates a JSON envelope, so we parse and
        return just the advice text — the review UI renders this verbatim.
        """
        message = f"Completed hand:\n{hand_context_text}\n\n{HAND_REVIEW_PROMPT}"
        response = self._assistant.chat(message, json_format=True)
        try:
            data = json.loads(response)
            advice = data.get('advice')
            if advice:
                return advice
            logger.warning(f"Coach review missing 'advice' field, raw: {response[:200]}")
            return response.strip()
        except json.JSONDecodeError as e:
            logger.warning(f"Coach review JSON parse failed: {e}, raw: {response[:200]}")
            return response.strip()


def apply_coach_highlight(stats, coach_action, coach_raise_to) -> None:
    """Point the UI's recommendation highlight at the coach's pick.

    When `COACH_HIGHLIGHT_SOURCE` is 'coach' (the default) and the coach
    returned an action, overwrite the bounded-options default highlight in
    `stats` with the coach's action/raise_to. No-op when the env opts out, the
    coach gave no action, or there are no stats. Mutates `stats` in place.
    Shared by the interactive ask route and the background prefetch so the two
    can't drift.
    """
    if stats and coach_action and os.getenv('COACH_HIGHLIGHT_SOURCE', 'coach') == 'coach':
        stats['recommendation'] = coach_action
        stats['raise_to'] = coach_raise_to


def _format_stats_for_prompt(data: Dict) -> str:
    """Convert coaching data dict into human-readable text for the LLM."""
    lines = []

    lines.append(f"Phase: {data.get('phase', '?')}")

    # Position with context
    position = data.get('position', '?')
    position_context = data.get('position_context', '')
    if position_context:
        lines.append(f"Position: {position} ({position_context})")
    else:
        lines.append(f"Position: {position}")

    big_blind = data.get('big_blind', 0)
    stack = data.get('stack', 0)
    if big_blind > 0:
        # Small blind is half big blind (truncated), matching poker/poker_game.py:722
        lines.append(f"Blinds: ${big_blind // 2}/${big_blind}")
        lines.append(f"Stack: ${stack} ({stack // big_blind} BB)")
    else:
        lines.append(f"Stack: ${stack}")

    # Effective stack — what you can actually win or lose this hand.
    # Mirrors the depth signal the hybrid/rule bots reason from.
    eff_stack = data.get('effective_stack')
    eff_stack_bb = data.get('effective_stack_bb')
    if eff_stack is not None and eff_stack != stack:
        if eff_stack_bb is not None and big_blind > 0:
            lines.append(f"Effective stack: ${eff_stack} ({eff_stack_bb:.0f} BB)")
        else:
            lines.append(f"Effective stack: ${eff_stack}")

    lines.append(f"Pot: ${data.get('pot_total', 0)}")
    lines.append(f"Cost to call: ${data.get('cost_to_call', 0)}")

    # SPR (stack-to-pot ratio) — the primary lens for postflop strategy.
    # Skip when undefined (empty pot — preflop before any betting).
    spr = data.get('spr')
    if spr is not None and spr != float('inf') and data.get('pot_total', 0) > 0:
        lines.append(f"SPR: {spr:.1f}")

    # Available actions - critical for valid recommendations
    available = data.get('available_actions', [])
    if available:
        lines.append(f"Available actions: {', '.join(available)}")

    equity = data.get('equity')
    if equity is not None:
        lines.append(f"Equity: {equity * 100:.1f}%")

    pot_odds = data.get('pot_odds')
    # `pot_odds` is Optional[float] — None means free to act (math undefined).
    # The `is not None` gate keeps the `{:.1f}` format from blowing up; we
    # still surface the "free check" wording so the LLM coach knows the call
    # was zero-cost rather than just absent.
    if pot_odds is not None and pot_odds > 0:
        lines.append(f"Pot odds: {pot_odds:.1f}:1")
    elif pot_odds is None and data.get('cost_to_call', 0) == 0:
        lines.append("Pot odds: n/a (free to check)")

    req = data.get('required_equity')
    if req is not None:
        lines.append(f"Required equity to call: {req * 100:.1f}%")

    ev = data.get('ev_call')
    if ev is not None:
        sign = '+' if ev >= 0 else ''
        lines.append(f"EV of calling: {sign}{ev}")

    is_pos = data.get('is_positive_ev')
    if is_pos is not None:
        lines.append(f"Positive EV: {'Yes' if is_pos else 'No'}")

    # Surface the literal hole cards and board even when hand_strength
    # gives a description — the coach often wants to reference specific
    # cards (e.g. "your Kd plays well on this board").
    hole = data.get('hand_hole_cards') or []
    if hole:
        # Annotate preflop hand with tier + range-fit when available.
        range_analysis = data.get('player_range_analysis') or {}
        suffix = ''
        canonical = range_analysis.get('canonical_hand')
        tier = range_analysis.get('hand_tier')
        in_range = range_analysis.get('in_range')
        if canonical and tier:
            range_note = 'in standard range' if in_range else 'outside standard range'
            suffix = f" — {canonical} ({tier}, {range_note})"
        lines.append(f"Your cards: {' '.join(hole)}{suffix}")

    board = data.get('hand_community_cards') or []
    if board:
        lines.append(f"Board: {' '.join(board)}")

        # Board texture — compact wet/dry summary with the salient flags
        # (paired, monotone, connected, etc.) so the coach can talk about
        # draws and protection without re-deriving it from the cards.
        texture = data.get('board_texture') or {}
        category = texture.get('texture_category')
        if category:
            flags = []
            if texture.get('paired'):
                flags.append('paired')
            if texture.get('trips_on_board'):
                flags.append('trips on board')
            if texture.get('monotone'):
                flags.append('monotone')
            elif texture.get('two_tone'):
                flags.append('two-tone')
            if texture.get('connected'):
                flags.append('connected')
            flag_str = f" ({', '.join(flags)})" if flags else ''
            lines.append(f"Board texture: {category}{flag_str}")

    hs = data.get('hand_strength')
    if hs:
        lines.append(f"Hand: {hs}")

    outs = data.get('outs')
    if outs is not None:
        lines.append(f"Outs: {outs}")

    rec = data.get('recommendation')
    if rec:
        lines.append(f"Recommended action: {rec}")

    # Live recall of one of THIS player's recurring preflop leaks, graded vs the
    # solver charts — the proactive prompt turns this into a Socratic reminder.
    leak = data.get('known_preflop_leak')
    if leak:
        spot = {
            'rfi': 'opening from',
            'vs_open': 'facing a raise in',
            'vs_3bet': 'facing a 3-bet in',
        }.get(leak.get('scenario'), leak.get('scenario', ''))
        subject = (
            f"{leak['hand']} {spot} {leak['position']}"
            if leak.get('hand')
            else f"{spot} {leak['position']}"
        )
        kind_desc = {
            'limp': "they tend to OPEN-LIMP here — the solver raises or folds, never limps",
            'too_loose': "they tend to PLAY hands the solver folds here",
            'over_fold': "they tend to OVER-FOLD hands the solver continues with here",
            'too_passive': "they tend to just CALL where the solver raises",
        }.get(leak.get('kind'), "their play diverges from the solver here")
        tag = (
            'KNOWN LEAK (confirmed)'
            if leak.get('status') == 'confirmed'
            else 'WATCHING (small sample)'
        )
        lines.append(f"{tag}: {subject} — {kind_desc}.")

    opponents = data.get('opponent_stats', [])
    if opponents:
        lines.append("Opponents:")
        for opp in opponents:
            # Header: name with position (e.g. "Yoda (BTN)")
            name = opp['name']
            pos = abbreviate_position(opp.get('position'))
            header = f"{name} ({pos})" if pos else name
            parts = [header]

            # Stack / all-in (critical for valid recommendations) — show
            # in BB too when we know the blinds so depth is comparable.
            stack = opp.get('stack')
            is_all_in = opp.get('is_all_in', False)
            if is_all_in:
                parts.append("ALL-IN")
            elif stack is not None:
                if big_blind > 0:
                    parts.append(f"${stack} ({stack // big_blind} BB)")
                else:
                    parts.append(f"${stack}")

            # Lead with the detection-layer archetype when we have one — it's a
            # diagnosis the player can act on ("exploit the calling station by
            # value-betting thin"). Fall back to the looser style label.
            if opp.get('archetype'):
                parts.append(opp['archetype'])
            elif opp.get('style') and opp['style'] != 'unknown':
                parts.append(opp['style'])

            # Compact VPIP/PFR/AF triple — the standard read-trio.
            # Skip the full triple when we don't have enough samples
            # to make it meaningful (< ~10 hands).
            vpip = opp.get('vpip')
            pfr = opp.get('pfr')
            af = opp.get('aggression')
            hands = opp.get('hands_observed', 0) or 0
            if vpip is not None and pfr is not None and af is not None and hands >= 10:
                parts.append(f"VPIP/PFR/AF: {vpip:.0%}/{pfr:.0%}/{af:.1f}")
            elif vpip is not None:
                parts.append(f"VPIP={vpip:.0%}")

            if hands > 0:
                parts.append(f"{hands} hands")
            lines.append(f"  - {', '.join(parts)}")

            # Tier-2 tells — only reads we actually have a sample for (a None
            # rate means the spot hasn't been observed, so it's omitted rather
            # than shown as a misleading default). These give the coach
            # exploit-grade detail beyond the VPIP/PFR/AF triple.
            dr = opp.get('deep_reads') or {}
            tell_parts = []
            if dr.get('fold_to_cbet') is not None:
                tell_parts.append(f"folds to c-bet {dr['fold_to_cbet']:.0%}")
            if dr.get('cbet_attempt_rate') is not None:
                tell_parts.append(f"c-bets flop {dr['cbet_attempt_rate']:.0%}")
            if dr.get('barrel_frequency') is not None:
                tell_parts.append(f"barrels turn {dr['barrel_frequency']:.0%}")
            if dr.get('aggression_factor_postflop') is not None:
                tell_parts.append(f"postflop AF {dr['aggression_factor_postflop']:.1f}")
            if dr.get('limp_rate') is not None:
                tell_parts.append(f"limps {dr['limp_rate']:.0%} of open spots")
            if dr.get('showdown_win_rate') is not None:
                tell_parts.append(f"wins {dr['showdown_win_rate']:.0%} at showdown")
            if dr.get('fold_to_big_bet') is not None:
                tell_parts.append(f"folds to big bets {dr['fold_to_big_bet']:.0%}")
            sp = dr.get('sizing_polarization_score')
            if sp is not None and sp > 0.15:
                tell_parts.append("bet size telegraphs strength (big = strong)")
            jam = dr.get('all_in_per_facing_bet')
            if jam is not None and jam > 0.15:
                tell_parts.append(f"jams into bets {jam:.0%} (don't bluff thin)")
            trap = dr.get('flop_check_then_barrel_rate')
            if trap is not None and trap > 0.5:
                tell_parts.append(f"checks flop then barrels turn {trap:.0%} (trap line)")
            if tell_parts:
                lines.append(f"      tells: {', '.join(tell_parts)}")

            # Synthesized EXPLOIT reads — the named leak + the actionable play the
            # sharp bots would run. This is the teaching payload: don't just list
            # the rate, hand the player the counter-strategy. Render tell → play so
            # the LLM can explain the read AND the line that beats it.
            for er in opp.get('exploit_reads') or []:
                tag = '' if er.get('confidence') == 'confirmed' else ' (small sample)'
                lines.append(f"      EXPLOIT [{er['tendency']}{tag}]: {er['tell']} → {er['play']}")

            # Cross-session history — surface only when present so the
            # line stays compact in fresh games. Notes are the player's
            # own observations from prior sessions, useful continuity.
            hist = opp.get('historical') or {}
            if hist:
                hist_parts = []
                sessions = hist.get('session_count')
                total = hist.get('total_hands')
                if sessions and total:
                    hist_parts.append(f"{sessions} prior session(s), {total} hands total")
                hv = hist.get('vpip')
                hp = hist.get('pfr')
                ha = hist.get('aggression')
                if hv is not None and hp is not None and ha is not None:
                    hist_parts.append(f"VPIP/PFR/AF: {hv:.0%}/{hp:.0%}/{ha:.1f}")
                notes = hist.get('notes') or []
                if notes:
                    note_str = ' | '.join(str(n) for n in notes[-2:])
                    hist_parts.append(f"notes: {note_str}")
                if hist_parts:
                    lines.append(f"      history: {'; '.join(hist_parts)}")

    # YOUR PLAY — the player analyzed by the SAME detectors as the opponents, so
    # the coach can flag the player's OWN exploitable leaks ("you fold to c-bets
    # too often — opponents punish it"). Sample-gated like the opponent reads.
    me = data.get('player_stats') or {}
    me_leaks = me.get('leaks') or []
    if me.get('archetype') or me_leaks:
        lines.append("\nYOUR PLAY (how the table reads YOU):")
        if me.get('archetype'):
            lines.append(f"  archetype: {me['archetype']} ({me.get('hands_observed', 0)} hands)")
        for lk in me_leaks:
            tag = '' if lk.get('confidence') == 'confirmed' else ' (small sample)'
            lines.append(f"  LEAK [{lk['tendency']}{tag}]: {lk['tell']} → fix: {lk['play']}")

    # Progression context — skill focus for coaching
    progression = data.get('progression', {})
    primary_skill = progression.get('primary_skill')
    if primary_skill:
        coaching_mode = progression.get('coaching_mode', '')
        skill_info = progression.get('skill_states', {}).get(primary_skill, {})
        skill_state = skill_info.get('state', '')
        accuracy = skill_info.get('window_accuracy', 0)
        opps = skill_info.get('total_opportunities', 0)

        skill_def = get_skill_by_id(primary_skill)
        skill_name = skill_def.name if skill_def else primary_skill
        skill_desc = skill_def.description if skill_def else ''

        lines.append(f"\nSKILL FOCUS: {skill_name}")
        if skill_desc:
            lines.append(f"Concept: {skill_desc}")
        if skill_state:
            lines.append(
                f"Player level: {skill_state} ({accuracy:.0%} accuracy, {opps} opportunities)"
            )
        if coaching_mode:
            mode_labels = {
                'learn': 'Teaching — explain the concept using this hand',
                'compete': 'Reinforcing — brief reminder only',
                'review': 'Reviewing — analyze what happened',
            }
            label = mode_labels.get(coaching_mode, coaching_mode)
            lines.append(f"Coaching approach: {label}")

    # Hand timeline (actions so far this hand)
    timeline = _format_hand_timeline(
        data.get('hand_actions', []),
        data.get('hand_community_cards', []),
        player_name=data.get('player_name'),
    )
    if timeline:
        lines.append(f"\nHand timeline:\n{timeline}")

    return '\n'.join(lines)


def _format_hand_timeline(
    actions: List[Dict],
    community_cards: List[str],
    player_name: Optional[str] = None,
) -> str:
    """Format in-progress hand actions into a readable timeline.

    Args:
        actions: List of action dicts (from RecordedAction.to_dict()).
        community_cards: Community card strings dealt so far.
        player_name: Human player's name; their actions render as "You".

    One action per indented line — easier for the LLM to parse than a
    comma-joined run, and consistent with the post-hand recap format.
    """
    if not actions:
        return ''

    phases = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER']
    actions_by_phase: Dict[str, list] = defaultdict(list)
    for a in actions:
        actions_by_phase[a['phase']].append(a)

    phase_cards = {
        'FLOP': community_cards[0:3] if len(community_cards) >= 3 else [],
        'TURN': [community_cards[3]] if len(community_cards) >= 4 else [],
        'RIVER': [community_cards[4]] if len(community_cards) >= 5 else [],
    }

    parts = []
    for phase in phases:
        phase_actions = actions_by_phase.get(phase, [])
        if not phase_actions:
            continue

        cards = phase_cards.get(phase, [])
        header = f"{phase} [{' '.join(cards)}]" if cards else phase

        # Action wording (raise-TO semantics, "You" substitution) is
        # delegated to the shared helper in poker.hand_narrator so the
        # coach, post-round chat, and post-hand recap all agree.
        action_lines = [format_action_phrase(a, perspective=player_name) for a in phase_actions]
        indented = "\n".join(f"    {line}" for line in action_lines)
        parts.append(f"  {header}:\n{indented}")

    return '\n'.join(parts)


def get_or_create_coach_with_mode(
    game_data: dict,
    game_id: str,
    player_name: str = '',
    mode: str = '',
    skill_context: str = '',
) -> CoachAssistant:
    """Get or create a CoachAssistant, replacing it when mode changes."""
    existing = game_data.get('coach_assistant')
    if existing and getattr(existing, 'mode', '') == mode:
        return existing

    owner_id = game_data.get('owner_id', '')
    coach = CoachAssistant(
        game_id,
        owner_id,
        player_name=player_name,
        mode=mode,
        skill_context=skill_context,
    )
    game_data['coach_assistant'] = coach
    return coach
