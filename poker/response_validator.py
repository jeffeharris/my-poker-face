"""
Response validation for AI poker players.
Ensures responses meet required format and context-appropriate fields.
"""
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class ResponseValidator:
    """Validates AI player responses according to game context."""
    
    # Fields that are always required
    ALWAYS_REQUIRED = {"action", "inner_monologue"}
    
    # Fields required conditionally
    CONDITIONALLY_REQUIRED = {
        "adding_to_pot": lambda response: response.get("action") in ["raise", "all-in"],
        "hand_strategy": lambda context: context.get("hand_action_count", 0) == 1
    }
    
    # Fields that can be present but should be validated
    # Organized by thinking phase: Observe → Analyze → Deliberate → React → Commit
    OPTIONAL_FIELDS = {
        # Phase 1: Observation
        "situation_read", "player_observations",
        # Phase 2: Analysis
        "hand_strength", "chasing", "odds_assessment",
        # Phase 3: Deliberation
        "bluff_likelihood", "bet_strategy", "decision_reasoning",
        # Phase 4: Reaction
        "play_style", "new_confidence", "new_attitude", "persona_response", "physical",
        # Legacy field (kept for backwards compatibility)
        "decision"
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
            if field == "adding_to_pot" and condition(response):
                if field not in response:
                    self.errors.append(f"Missing required field: {field} (required when action is raise/all-in)")
            elif field == "hand_strategy" and condition(context):
                if field not in response:
                    self.errors.append(f"Missing required field: {field} (required on first action of hand)")
        
        # Validate action is from valid options
        if "action" in response and context.get("valid_actions"):
            if response["action"] not in context["valid_actions"]:
                self.errors.append(f"Invalid action: {response['action']}. Must be one of: {context['valid_actions']}")
        
        # Validate and normalize adding_to_pot to int if present
        if "adding_to_pot" in response:
            try:
                amount = int(response["adding_to_pot"])
                response["adding_to_pot"] = amount  # Convert in place
                if amount < 0:
                    self.errors.append("adding_to_pot must be non-negative")
            except (ValueError, TypeError):
                self.errors.append("adding_to_pot must be a number")
        
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
            if "persona_response" in response:
                self.warnings.append("persona_response included but player shouldn't speak (will be removed)")
            if "physical" in response:
                self.warnings.append("physical actions included but player shouldn't speak (will be removed)")
        
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
            cleaned.pop("persona_response", None)
            cleaned.pop("physical", None)
            logger.debug(f"Removed speech fields for quiet player")
        
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
        messages.append("- adding_to_pot (if you raise or go all-in)")
        
        return "\n".join(messages)