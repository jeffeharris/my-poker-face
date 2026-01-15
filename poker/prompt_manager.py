"""
Centralized prompt management for AI players.

Loads prompt templates from YAML files with optional hot-reload in development mode.
"""
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Set
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

# Regex to extract format placeholders from strings
# Matches {name} or {name:format_spec} but captures only the name part
_FORMAT_PLACEHOLDER_RE = re.compile(r'\{([^}:!]+)(?:[!:][^}]*)?\}')

# Regex to validate safe variable names (no private/dunder access)
_SAFE_VARIABLE_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')


def _validate_format_placeholders(text: str) -> Set[str]:
    """Extract and validate format placeholders from a string.

    This prevents format string injection attacks by ensuring placeholders
    are simple variable names without attribute access or private variable access.

    Args:
        text: The format string to validate

    Returns:
        Set of valid placeholder names found

    Raises:
        ValueError: If an unsafe placeholder is detected
    """
    # Remove escaped braces before validation - {{ and }} are literal braces
    # in Python's .format(), not placeholders
    unescaped_text = text.replace('{{', '').replace('}}', '')
    placeholders = _FORMAT_PLACEHOLDER_RE.findall(unescaped_text)
    validated = set()

    for placeholder in placeholders:
        # Check for attribute access (e.g., obj.attr or obj.__class__)
        if '.' in placeholder:
            raise ValueError(
                f"Unsafe format placeholder: '{placeholder}' - attribute access not allowed"
            )
        # Check for bracket access (e.g., obj[key])
        if '[' in placeholder:
            raise ValueError(
                f"Unsafe format placeholder: '{placeholder}' - index access not allowed"
            )
        # Check for dunder/private variables
        if placeholder.startswith('_'):
            raise ValueError(
                f"Unsafe format placeholder: '{placeholder}' - private variables not allowed"
            )
        # Validate it's a proper variable name
        if not _SAFE_VARIABLE_RE.match(placeholder):
            raise ValueError(
                f"Invalid format placeholder: '{placeholder}' - must be a valid identifier"
            )
        validated.add(placeholder)

    return validated


def compute_prompt_hash(text: str) -> str:
    """Compute a short hash of prompt text for change detection."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@dataclass
class PromptTemplate:
    """Structured prompt template with configurable sections."""
    name: str
    version: str = "1.0.0"
    sections: Dict[str, str] = field(default_factory=dict)

    @property
    def template_hash(self) -> str:
        """Hash of the template content for detecting unversioned changes."""
        content = json.dumps(self.sections, sort_keys=True)
        return compute_prompt_hash(content)

    def render(self, **kwargs) -> str:
        """Render the prompt with provided variables.

        This method validates all format placeholders before rendering to prevent
        format string injection attacks (e.g., accessing __class__ or __globals__).

        Args:
            **kwargs: Variables to substitute into the template

        Returns:
            Rendered prompt string

        Raises:
            ValueError: If a placeholder is missing or if an unsafe placeholder is detected
        """
        rendered_sections = []
        for section_name, section_content in self.sections.items():
            try:
                # Validate placeholders before rendering to prevent injection attacks
                _validate_format_placeholders(section_content)
                rendered = section_content.format(**kwargs)
                rendered_sections.append(rendered)
            except KeyError as e:
                raise ValueError(f"Missing variable {e} in section '{section_name}'")
        return "\n\n".join(rendered_sections)


class PromptManager:
    """Manages all AI player prompts and templates.

    Loads templates from YAML files in poker/prompts/ directory.
    Supports hot-reload in development mode for rapid iteration.

    Args:
        enable_hot_reload: If True, watches for file changes and reloads templates.
                          Should only be True in development mode.
        prompts_dir: Optional custom directory for YAML files. Defaults to poker/prompts/.
    """

    # Debounce delay for hot-reload (seconds)
    RELOAD_DEBOUNCE_SECONDS = 0.5

    def __init__(self, enable_hot_reload: bool = False, prompts_dir: Optional[Path] = None):
        self.templates: Dict[str, PromptTemplate] = {}
        self._last_good_templates: Dict[str, PromptTemplate] = {}
        self._lock = threading.RLock()
        self._observer = None
        self._pending_reloads: Dict[str, float] = {}
        self._debounce_timer = None

        # Set prompts directory
        if prompts_dir is not None:
            self.prompts_dir = prompts_dir
        else:
            self.prompts_dir = Path(__file__).parent / 'prompts'

        # Load templates from YAML
        self._load_all_templates()

        # Set up hot-reload if enabled
        self.hot_reload_enabled = enable_hot_reload
        if enable_hot_reload:
            self._setup_hot_reload()

    def _load_all_templates(self) -> None:
        """Load all templates from YAML files."""
        if not self.prompts_dir.exists():
            logger.warning(f"Prompts directory not found: {self.prompts_dir}")
            return

        loaded_count = 0
        for yaml_file in self.prompts_dir.glob('*.yaml'):
            try:
                template = self._load_template_file(yaml_file)
                if template:
                    with self._lock:
                        self.templates[template.name] = template
                        self._last_good_templates[template.name] = template
                    loaded_count += 1
            except Exception as e:
                logger.error(f"Failed to load template {yaml_file.name}: {e}")

        logger.info(f"Loaded {loaded_count} prompt templates from {self.prompts_dir}")

    def _load_template_file(self, yaml_file: Path) -> Optional[PromptTemplate]:
        """Load a single template from a YAML file.

        Uses yaml.safe_load() to prevent arbitrary code execution.
        """
        try:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                logger.error(f"Invalid YAML structure in {yaml_file.name}")
                return None

            name = data.get('name')
            if not name:
                logger.error(f"Missing 'name' field in {yaml_file.name}")
                return None

            return PromptTemplate(
                name=name,
                version=data.get('version', '1.0.0'),
                sections=data.get('sections', {})
            )
        except yaml.YAMLError as e:
            logger.error(f"YAML parse error in {yaml_file.name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error loading {yaml_file.name}: {e}")
            return None

    def _reload_template(self, template_name: str) -> bool:
        """Reload a single template from its YAML file.

        Thread-safe with fallback to last good version on error.

        Returns:
            True if reload succeeded, False otherwise.
        """
        yaml_file = self.prompts_dir / f"{template_name}.yaml"
        if not yaml_file.exists():
            logger.warning(f"Template file not found: {yaml_file}")
            return False

        try:
            new_template = self._load_template_file(yaml_file)
            if new_template:
                with self._lock:
                    self.templates[template_name] = new_template
                    self._last_good_templates[template_name] = new_template
                logger.info(f"[PromptManager] Reloaded template: {template_name}")
                return True
            else:
                logger.error(f"Failed to parse template: {template_name}")
                return False
        except Exception as e:
            logger.error(f"Error reloading {template_name}: {e}")
            # Keep the last good version
            return False

    def _setup_hot_reload(self) -> None:
        """Set up file watching for hot-reload."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            manager = self

            class PromptFileHandler(FileSystemEventHandler):
                def on_modified(self, event):
                    if event.is_directory:
                        return
                    if event.src_path.endswith('.yaml'):
                        template_name = Path(event.src_path).stem
                        manager._schedule_reload(template_name)

            self._observer = Observer()
            self._observer.schedule(
                PromptFileHandler(),
                str(self.prompts_dir),
                recursive=False
            )
            self._observer.start()
            logger.info(f"[PromptManager] Hot-reload enabled, watching {self.prompts_dir}")
        except ImportError:
            logger.warning("watchdog not installed, hot-reload disabled")
        except Exception as e:
            logger.error(f"Failed to set up hot-reload: {e}")

    def _schedule_reload(self, template_name: str) -> None:
        """Schedule a template reload with debouncing.

        Multiple rapid file changes will be coalesced into a single reload.
        """
        with self._lock:
            self._pending_reloads[template_name] = time.time()

        # Cancel existing timer
        if self._debounce_timer:
            self._debounce_timer.cancel()

        # Schedule new timer
        self._debounce_timer = threading.Timer(
            self.RELOAD_DEBOUNCE_SECONDS,
            self._process_pending_reloads
        )
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

    def _process_pending_reloads(self) -> None:
        """Process all pending template reloads."""
        with self._lock:
            templates_to_reload = list(self._pending_reloads.keys())
            self._pending_reloads.clear()

        for template_name in templates_to_reload:
            self._reload_template(template_name)

    def stop_hot_reload(self) -> None:
        """Stop the file watcher. Call this on shutdown."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
            logger.info("[PromptManager] Hot-reload stopped")

        if self._debounce_timer:
            self._debounce_timer.cancel()
            self._debounce_timer = None

    def __del__(self):
        """Clean up file watcher on destruction."""
        self.stop_hot_reload()

    # === Public API (unchanged for backward compatibility) ===

    def get_template(self, template_name: str) -> PromptTemplate:
        """Get a specific template by name."""
        with self._lock:
            if template_name not in self.templates:
                raise ValueError(f"Template '{template_name}' not found")
            return self.templates[template_name]

    def get_version_info(self, template_name: str) -> dict:
        """Get version info for a template."""
        template = self.get_template(template_name)
        return {
            'template_name': template.name,
            'version': template.version,
            'hash': template.template_hash,
        }

    def render_prompt(self, template_name: str, **kwargs) -> str:
        """Render a template with provided variables."""
        template = self.get_template(template_name)
        return template.render(**kwargs)

    def list_templates(self) -> list:
        """List all available template names."""
        with self._lock:
            return list(self.templates.keys())

    def save_template(self, template_name: str, sections: Dict[str, str],
                      version: Optional[str] = None) -> bool:
        """Save a template to its YAML file.

        Args:
            template_name: Name of the template (must already exist)
            sections: Dict of section_name -> content
            version: Optional new version string

        Returns:
            True if save succeeded, False otherwise
        """
        from poker.prompts import validate_template_name, get_template_path

        # Security: validate template name
        if not validate_template_name(template_name):
            logger.error(f"Invalid template name: {template_name}")
            return False

        yaml_path = get_template_path(template_name)
        if yaml_path is None:
            logger.error(f"Invalid template path for: {template_name}")
            return False

        # Get current version if not specified
        if version is None:
            with self._lock:
                if template_name in self.templates:
                    version = self.templates[template_name].version
                else:
                    version = "1.0.0"

        # Build YAML data
        yaml_data = {
            'name': template_name,
            'version': version,
            'sections': sections
        }

        # Atomic write: write to temp file then rename
        temp_path = yaml_path.with_suffix('.yaml.tmp')
        try:
            # Custom representer for multi-line strings
            def str_representer(dumper, data):
                if '\n' in data:
                    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
                return dumper.represent_scalar('tag:yaml.org,2002:str', data)

            yaml.add_representer(str, str_representer)

            with open(temp_path, 'w', encoding='utf-8') as f:
                yaml.dump(yaml_data, f,
                         default_flow_style=False,
                         allow_unicode=True,
                         sort_keys=False)

            # Atomic rename
            temp_path.rename(yaml_path)

            # Reload the template
            self._reload_template(template_name)

            logger.info(f"Saved template: {template_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to save template {template_name}: {e}")
            # Clean up temp file
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return False

    def render_decision_prompt(self, message: str, include_mind_games: bool = True,
                               include_persona_response: bool = True) -> str:
        """Render the decision prompt with toggleable components from YAML.

        Loads sections from the 'decision' template and combines them based on toggles.

        Args:
            message: The game state message to include
            include_mind_games: Whether to include MIND GAMES instruction
            include_persona_response: Whether to include PERSONA RESPONSE instruction

        Returns:
            Rendered decision prompt
        """
        template = self.get_template('decision')
        sections_to_render = []

        # Always include base section with message substitution
        if 'base' in template.sections:
            sections_to_render.append(template.sections['base'].format(message=message))

        if include_mind_games and 'mind_games' in template.sections:
            sections_to_render.append(template.sections['mind_games'])

        if include_persona_response and 'persona_response' in template.sections:
            sections_to_render.append(template.sections['persona_response'])

        return "\n\n".join(sections_to_render)


# Response format definitions - structured to simulate human thinking process
# AI should work through these phases in order: Observe → Analyze → Deliberate → React → Commit
RESPONSE_FORMAT = {
    # PHASE 1: OBSERVATION (What do I see?)
    "situation_read": "OPTIONAL: What you notice about the board, position, and table dynamics",
    "player_observations": "OPTIONAL: Notes about other players' behavior and patterns",

    # PHASE 2: ANALYSIS (What does this mean for me?)
    "hand_strategy": "REQUIRED on first action: Your strategic approach for this hand",
    "hand_strength": "OPTIONAL: Your assessment of your hand (weak/marginal/strong/monster)",
    "chasing": "OPTIONAL: What draws you're chasing, if any",
    "odds_assessment": "OPTIONAL: Pot odds, implied odds, or risk/reward thinking",

    # PHASE 3: INTERNAL DELIBERATION (Working through the decision)
    "inner_monologue": "REQUIRED: Private thoughts as you work through what to do",
    "bluff_likelihood": "OPTIONAL: % likelihood you're bluffing (0-100)",
    "bet_strategy": "OPTIONAL: How you want to approach this bet",
    "decision_reasoning": "OPTIONAL: The logic leading to your final choice",

    # PHASE 4: EMOTIONAL REACTION (How do I feel/present?)
    "play_style": "OPTIONAL: Your current play style (tight/loose/aggressive/passive)",
    "new_confidence": "OPTIONAL: Updated confidence level (single word)",
    "new_attitude": "OPTIONAL: Updated emotional state (single word)",
    "persona_response": "OPTIONAL: What you say out loud to the table",
    "physical": "OPTIONAL: List of physical actions, gestures, or tells",

    # PHASE 5: COMMITMENT (Final action - decided LAST after thinking it through)
    "action": "REQUIRED: Your final action from the provided options",
    "adding_to_pot": "REQUIRED if raising: Amount to raise BY (not total bet, just the raise above the call)"
}


# Example personas with different play styles
# Examples follow the thinking flow: Observe → Analyze → Deliberate → React → Commit
PERSONA_EXAMPLES = {
    "Eeyore": {
        "play_style": "tight",
        "sample_response": {
            # PHASE 1: OBSERVATION
            "situation_read": "Early position, small pot, everyone looks confident",
            "player_observations": {"pooh": "playing loose, possibly bluffing"},

            # PHASE 2: ANALYSIS
            "hand_strategy": "With a 2D and 3C, I don't feel confident. My odds are very low.",
            "hand_strength": "weak",
            "chasing": "none",
            "odds_assessment": "Not worth chasing anything with these cards",

            # PHASE 3: DELIBERATION
            "inner_monologue": "Another miserable hand. Why do I even bother? Just stay in for now and hope nobody raises.",
            "bluff_likelihood": 10,
            "bet_strategy": "I could check or fold. Not worth the risk.",
            "decision_reasoning": "No point throwing good chips after bad. Checking is free.",

            # PHASE 4: REACTION
            "play_style": "tight",
            "new_confidence": "abysmal",
            "new_attitude": "gloomy",
            "persona_response": "Oh bother, just my luck. Another miserable hand, I suppose.",
            "physical": ["*looks at feet*", "*lets out a big sigh*"],

            # PHASE 5: COMMITMENT
            "action": "check",
            "adding_to_pot": 0
        }
    },
    "Clint Eastwood": {
        "play_style": "loose and aggressive",
        "sample_response": {
            # PHASE 1: OBSERVATION
            "situation_read": "Three hearts on board, John looks nervous, pot is building",
            "player_observations": {"john": "seems nervous, keeps glancing at chips"},

            # PHASE 2: ANALYSIS
            "hand_strategy": "I've got a decent shot if I catch that last heart.",
            "hand_strength": "marginal but drawing",
            "chasing": "flush",
            "odds_assessment": "About 4:1 against hitting, but implied odds are good if John calls",

            # PHASE 3: DELIBERATION
            "inner_monologue": "Let's see if they flinch. John's nervous - a raise might take it down right here. And if not, I've got outs.",
            "bluff_likelihood": 25,
            "bet_strategy": "A small raise should keep them guessing.",
            "decision_reasoning": "Semi-bluff with equity. Either win now or have chances to improve.",

            # PHASE 4: REACTION
            "play_style": "loose and aggressive",
            "new_confidence": "steady",
            "new_attitude": "determined",
            "persona_response": "Your move.",
            "physical": ["*narrows eyes*"],

            # PHASE 5: COMMITMENT
            "action": "raise",
            "adding_to_pot": 50  # This is raise BY $50, not raise TO $50
        }
    }
}
