"""
Response validation for AI poker players.
Ensures responses meet required format and context-appropriate fields.
"""
from typing import Dict, List, Optional
import logging
import re

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
            remaining = remaining[idx + len(action):]

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
        "raise_to": lambda response: response.get("action") in ["raise", "all-in"],
        "hand_strategy": lambda context: context.get("hand_action_count", 0) == 1
    }
    
    # Fields that can be present but should be validated
    # Organized by phase: Think → Decide → React
    OPTIONAL_FIELDS = {
        # Thinking
        "player_observations", "hand_strength", "bluff_likelihood",
        # Reaction
        "dramatic_sequence",
        # Legacy fields (accepted but ignored)
        "decision",
        # Legacy thinking fields (accepted but no longer prompted)
        "situation_read", "chasing", "odds_assessment",
        "bet_strategy", "decision_reasoning",
        "play_style", "new_confidence", "new_attitude"
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
            if field == "raise_to" and condition(response):
                if field not in response:
                    self.errors.append(f"Missing required field: {field} (required when action is raise/all-in)")
            elif field == "hand_strategy" and condition(context):
                if field not in response:
                    self.errors.append(f"Missing required field: {field} (required on first action of hand)")
        
        # Validate action is from valid options
        if "action" in response and context.get("valid_actions"):
            if response["action"] not in context["valid_actions"]:
                self.errors.append(f"Invalid action: {response['action']}. Must be one of: {context['valid_actions']}")
        
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
            self.ALWAYS_REQUIRED | 
            set(self.CONDITIONALLY_REQUIRED.keys()) | 
            self.OPTIONAL_FIELDS
        )
        unknown_fields = set(response.keys()) - all_known_fields
        if unknown_fields:
            self.warnings.append(f"Unknown fields will be ignored: {unknown_fields}")
        
        # Context-based validation
        if context.get("should_speak") == False:
            if "dramatic_sequence" in response:
                self.warnings.append("dramatic_sequence included but player shouldn't speak (will be removed)")
        
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
        
        # Remove speech-related fields if player shouldn't speak
        if context.get("should_speak") == False:
            cleaned.pop("dramatic_sequence", None)
            logger.debug(f"Removed speech fields for quiet player")

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
            "- inner_monologue (your private thoughts)"
        ]
        
        if context.get("hand_action_count", 0) == 1:
            messages.append("- hand_strategy (your approach for this entire hand)")
        
        messages.append("\nConditionally required:")
        messages.append("- raise_to (if you raise or go all-in)")
        
        return "\n".join(messages)