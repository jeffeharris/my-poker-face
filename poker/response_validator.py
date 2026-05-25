"""
Response validation for AI poker players.
Ensures responses meet required format and context-appropriate fields.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


_ACTION_PATTERN = re.compile(r'\*[^*]+\*')
_COMMENT_WRAPPER = re.compile(r'/\*\s*(.*?)\s*\*/', re.DOTALL)
_ARTIFACT_CHARS = ',;\n\r\t'
_SPEECH_ARTIFACT_CHARS = _ARTIFACT_CHARS + '*'


def _clean_beat(text: str) -> str:
    """Strip whitespace, punctuation artifacts, and comment wrappers from a beat."""
    text = _COMMENT_WRAPPER.sub(r'\1', text)
    return text.strip().strip(_ARTIFACT_CHARS).strip()


def _clean_speech(text: str) -> str:
    """Strip whitespace, punctuation artifacts, and orphaned asterisks from speech."""
    return text.strip().strip(_SPEECH_ARTIFACT_CHARS).strip()


def needs_llm_normalization(beats: List[str]) -> bool:
    """Cheap heuristic — should we ask the fast LLM to clean these beats?

    Returns True when at least one beat looks malformed: mixed action and
    speech in one entry, missing asterisks on a likely gesture, literal
    quote chars wrapping the beat, or a non-string element. For
    already-clean output this returns False so we don't burn a fast-tier
    call on every turn.

    Clean shapes (skipped):
    - Pure action:  starts and ends with exactly one *...* pair.
    - Pure speech:  no asterisks, no surrounding quotes.
    """
    if not beats:
        return False
    for beat in beats:
        if not isinstance(beat, str):
            return True
        text = beat.strip()
        if not text:
            continue
        if text.startswith('*') and text.endswith('*') and text.count('*') == 2:
            continue
        if '*' not in text:
            if text[0] in '"\'' and len(text) >= 2 and text[-1] in '"\'':
                return True  # quote-wrapped speech/action
            continue
        # Lone single-asterisk strings ('*' on its own) aren't real
        # action beats — skip them rather than spending a fast-tier LLM
        # call on a single character.
        if len(text) < 3:
            continue
        return True  # has asterisks but isn't a single pure action
    return False


def llm_normalize_beats(
    beats: List[str],
    llm_client,
    game_id: Optional[str] = None,
    player_name: Optional[str] = None,
) -> List[str]:
    """Ask a fast-tier LLM to clean dramatic_sequence beats.

    The model is told to: split mixed action+speech beats, wrap orphan
    gestures in asterisks, strip literal quote chars, drop empties,
    preserve order, and never paraphrase. Any failure falls back to the
    original beats — defensive degradation rather than dropping table
    talk on a transient API issue.
    """
    if not beats:
        return beats
    try:
        import json as _json

        from core.llm.tracking import CallType

        prompt = (
            "Clean the following dramatic_sequence beats from a poker AI character.\n"
            "Each beat MUST be EITHER:\n"
            "  - an ACTION: a short lowercase gesture wrapped in *asterisks* "
            "(e.g. *leans back*, *taps chips*, *narrows eyes*)\n"
            "  - or SPEECH: plain text dialogue the table can hear (no asterisks).\n"
            "\n"
            "Rules:\n"
            "- If a beat mixes an action and speech, SPLIT into separate beats.\n"
            "- If a beat is clearly a gesture without asterisks (e.g. 'leans back', "
            "'shrugs'), wrap it: *leans back*.\n"
            "- Strip surrounding literal quote characters from beats.\n"
            "- Drop empty beats.\n"
            "- Preserve the original order.\n"
            "- DO NOT paraphrase or invent content. Output the same words, just "
            "correctly formatted.\n"
            "\n"
            "Input beats (JSON array):\n"
            f"{_json.dumps(beats)}\n"
            "\n"
            "Return ONLY JSON: {\"beats\": [<cleaned beat strings, in order>]}"
        )

        response = llm_client.complete(
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise text-formatting tool. Output only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            json_format=True,
            call_type=CallType.NARRATION_CLEANUP,
            game_id=game_id,
            player_name=player_name,
            prompt_template='beat_normalizer',
        )
        import json as _json2

        data = _json2.loads(response.content)
        cleaned = data.get('beats', None) if isinstance(data, dict) else None
        if not isinstance(cleaned, list):
            return beats
        return [str(b) for b in cleaned if isinstance(b, str) and b]
    except Exception as e:
        logger.warning(f"[BEAT_NORMALIZER] LLM cleanup failed safely: {e}")
        return beats


def normalize_dramatic_sequence(beats: List[str]) -> List[str]:
    """Split mixed dramatic_sequence beats into separate action and speech beats.

    AI sometimes returns beats that combine actions and speech in one entry,
    e.g. "*leans forward* I'm going all in!" or "*leans forward* *pushes chips*".
    This function splits them so each beat is either a pure action or pure speech.
    Also strips trailing/leading punctuation artifacts (commas, semicolons, etc.).
    """
    normalized = []
    for beat in beats:
        if not isinstance(beat, str):
            continue
        beat = _clean_beat(beat)
        if not beat:
            continue

        actions = _ACTION_PATTERN.findall(beat)
        if not actions:
            # Pure speech beat — strip orphaned asterisks
            speech = _clean_speech(beat)
            if speech:
                normalized.append(speech)
            continue

        # Check if the entire beat is a single action (already correct)
        if len(actions) == 1 and beat == actions[0]:
            normalized.append(beat)
            continue

        # Mixed or multiple actions — split into segments preserving order
        remaining = beat
        for action in actions:
            idx = remaining.find(action)
            # Any text before this action is speech (strip orphaned asterisks)
            before = _clean_speech(remaining[:idx])
            if before:
                normalized.append(before)
            normalized.append(action)
            remaining = remaining[idx + len(action) :]

        # Any trailing text after the last action is speech
        trailing = _clean_speech(remaining)
        if trailing:
            normalized.append(trailing)

    return normalized


class ResponseValidator:
    """Validates AI player responses according to game context."""

    # Fields that are always required
    ALWAYS_REQUIRED = {"action", "inner_monologue"}

    # Fields required conditionally
    CONDITIONALLY_REQUIRED = {
        "bet_sizing": lambda response: response.get("action") in ["raise"],
        "raise_to": lambda response: response.get("action") in ["raise", "all-in"],
        "hand_strategy": lambda context: context.get("hand_action_count", 0) == 1,
    }

    # Fields that can be present but should be validated
    # Organized by phase: Think → Decide → React
    OPTIONAL_FIELDS = {
        # Thinking
        "player_observations",
        "hand_strength",
        "bluff_likelihood",
        # Decision
        "bet_sizing",
        # Reaction
        "dramatic_sequence",
        # Legacy fields (accepted but ignored)
        "decision",
        # Legacy thinking fields (accepted but no longer prompted)
        "situation_read",
        "chasing",
        "odds_assessment",
        "bet_strategy",
        "decision_reasoning",
        "play_style",
        "new_confidence",
        "new_attitude",
    }

    def __init__(self):
        self.errors = []
        self.warnings = []

    def validate(self, response: Dict, context: Optional[Dict] = None) -> bool:
        """
        Validate a response against requirements.

        Args:
            response: The AI's response dictionary
            context: Optional context (e.g., hand_action_count, should_speak)

        Returns:
            bool: True if valid, False otherwise
        """
        self.errors = []
        self.warnings = []
        context = context or {}

        # Check always required fields
        for field in self.ALWAYS_REQUIRED:
            if field not in response:
                self.errors.append(f"Missing required field: {field}")

        # Check conditionally required fields
        for field, condition in self.CONDITIONALLY_REQUIRED.items():
            if field == "bet_sizing" and condition(response):
                if field not in response:
                    self.errors.append(
                        f"Missing required field: {field} (required when action is raise)"
                    )
            elif field == "raise_to" and condition(response):
                if field not in response:
                    self.errors.append(
                        f"Missing required field: {field} (required when action is raise/all-in)"
                    )
            elif field == "hand_strategy" and condition(context):
                if field not in response:
                    self.errors.append(
                        f"Missing required field: {field} (required on first action of hand)"
                    )

        # Validate action is from valid options
        if "action" in response and context.get("valid_actions"):
            if response["action"] not in context["valid_actions"]:
                self.errors.append(
                    f"Invalid action: {response['action']}. Must be one of: {context['valid_actions']}"
                )

        # Validate and normalize raise_to to int if present
        if "raise_to" in response:
            try:
                amount = int(response["raise_to"])
                response["raise_to"] = amount  # Convert in place
                if amount < 0:
                    self.errors.append("raise_to must be non-negative")
            except (ValueError, TypeError):
                self.errors.append("raise_to must be a number")

        # Check for unknown fields
        all_known_fields = (
            self.ALWAYS_REQUIRED | set(self.CONDITIONALLY_REQUIRED.keys()) | self.OPTIONAL_FIELDS
        )
        unknown_fields = set(response.keys()) - all_known_fields
        if unknown_fields:
            self.warnings.append(f"Unknown fields will be ignored: {unknown_fields}")

        # Context-based validation
        if context.get("should_speak") is False:
            if "dramatic_sequence" in response:
                self.warnings.append(
                    "dramatic_sequence included but player shouldn't speak (will be removed)"
                )

        return len(self.errors) == 0

    def get_errors(self) -> List[str]:
        """Get validation errors."""
        return self.errors.copy()

    def get_warnings(self) -> List[str]:
        """Get validation warnings."""
        return self.warnings.copy()

    def clean_response(self, response: Dict, context: Optional[Dict] = None) -> Dict:
        """
        Clean a response by removing inappropriate fields based on context.

        Args:
            response: The AI's response dictionary
            context: Optional context (e.g., should_speak)

        Returns:
            Dict: Cleaned response
        """
        cleaned = response.copy()
        context = context or {}

        # Narration-mode filter. should_gesture is opt-in (default False)
        # so callers that only set should_speak get the legacy strict-strip
        # behavior. When speak is False:
        #   gesture=True  → keep only *action* beats (silent gesturing)
        #   gesture=False → strip dramatic_sequence entirely (legacy)
        should_speak = context.get("should_speak", True)
        should_gesture = context.get("should_gesture", False)
        if should_speak is False:
            if should_gesture and isinstance(cleaned.get("dramatic_sequence"), list):
                cleaned["dramatic_sequence"] = [
                    b
                    for b in cleaned["dramatic_sequence"]
                    if isinstance(b, str) and b.strip().startswith('*') and b.strip().endswith('*')
                ]
                logger.debug("Stripped speech beats — gesture-only mode")
            else:
                cleaned.pop("dramatic_sequence", None)
                logger.debug("Removed speech fields for fully silent player")

        # Normalize dramatic_sequence beats (split mixed action+speech)
        if 'dramatic_sequence' in cleaned:
            ds = cleaned['dramatic_sequence']
            if isinstance(ds, list):
                cleaned['dramatic_sequence'] = normalize_dramatic_sequence(ds)
            elif isinstance(ds, str):
                cleaned['dramatic_sequence'] = normalize_dramatic_sequence([ds])

        return cleaned

    @staticmethod
    def get_required_fields_message(context: Optional[Dict] = None) -> str:
        """
        Get a human-readable message about required fields.

        Args:
            context: Optional context to determine conditional requirements

        Returns:
            str: Message describing required fields
        """
        context = context or {}
        messages = [
            "Required fields:",
            "- action (from your available options)",
            "- inner_monologue (your private thoughts)",
        ]

        if context.get("hand_action_count", 0) == 1:
            messages.append("- hand_strategy (your approach for this entire hand)")

        messages.append("\nConditionally required:")
        messages.append("- bet_sizing (if you raise: name your sizing strategy)")
        messages.append("- raise_to (if you raise or go all-in)")

        return "\n".join(messages)
