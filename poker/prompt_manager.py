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

# Drama context messages for response intensity calibration
DRAMA_CONTEXTS = {
    'routine': "RESPONSE STYLE: Minimal. Skip dramatic_sequence or one brief beat max.",
    'notable': "RESPONSE STYLE: Brief. One or two beats in dramatic_sequence.",
    'high_stakes': "RESPONSE STYLE: Expressive. Build your dramatic_sequence with 2-3 beats.",
    'climactic': "RESPONSE STYLE: Theatrical. Build tension in dramatic_sequence - 3-5 beats, savor the reveal."
}

# Tone modifiers that append to drama context based on hand strength
TONE_MODIFIERS = {
    'neutral': "",
    'confident': " Channel quiet confidence - you know you have the goods.",
    'desperate': " Show the pressure - this is do-or-die, make it feel that way.",
    'triumphant': " Savor the moment - you've got them right where you want them."
}


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
            try:
                self._observer.stop()
                # Only join if the observer thread was actually started
                if self._observer.is_alive():
                    self._observer.join(timeout=2.0)
                logger.info("[PromptManager] Hot-reload stopped")
            except Exception as e:
                logger.debug(f"[PromptManager] Error stopping hot-reload: {e}")
            finally:
                self._observer = None

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

    def render_decision_prompt(
        self,
        message: str,
        include_mind_games: bool = True,
        include_dramatic_sequence: bool = True,
        pot_committed_info: dict | None = None,
        short_stack_info: dict | None = None,
        made_hand_info: dict | None = None,
        equity_verdict_info: dict | None = None,
        drama_context: dict | None = None,
        include_pot_odds: bool = True,
        pot_odds_info: dict | None = None,
        use_simple_response_format: bool = False,
        expression_guidance: str | None = None,
        zone_guidance: str | None = None,
    ) -> str:
        """Render the decision prompt with toggleable components from YAML.

        Loads sections from the 'decision' template and combines them based on toggles.

        Args:
            message: The game state message to include
            include_mind_games: Whether to include MIND GAMES instruction
            include_dramatic_sequence: Whether to include DRAMATIC SEQUENCE instruction
            pot_committed_info: Dict with {pot_odds, required_equity, already_bet} if pot-committed
            short_stack_info: Dict with {stack_bb} if short-stacked (<3 BB)
            made_hand_info: Dict with {hand_name, equity, is_tilted, tier} for made hand guidance
            equity_verdict_info: Dict with {equity, required_equity, verdict, pot_odds} for GTO foundation
            drama_context: Dict with {level, factors} for response intensity calibration
            include_pot_odds: Whether to include pot odds guidance section
            pot_odds_info: Dict with {pot_odds, equity_needed, pot_fmt, call_fmt, pot_odds_extra}
                           or {'free': True} for free check. None means no pot odds section.
            use_simple_response_format: If True, use simple JSON response format instead of dramatic_sequence
            expression_guidance: Phase 2 visibility-based expression/tempo guidance string
            zone_guidance: Phase 7 zone-based strategy guidance string

        Returns:
            Rendered decision prompt
        """
        template = self.get_template('decision')
        sections_to_render = []

        # Always include base section with message substitution
        if 'base' in template.sections:
            sections_to_render.append(template.sections['base'].format(message=message))

        # Phase 7: Include zone-based strategy guidance (early to frame the decision)
        if zone_guidance:
            sections_to_render.append(zone_guidance)

        # Include pot-committed warning if applicable (high priority - insert before other guidance)
        if pot_committed_info and 'pot_committed' in template.sections:
            sections_to_render.append(template.sections['pot_committed'].format(
                pot_odds=pot_committed_info.get('pot_odds', 0),
                required_equity=pot_committed_info.get('required_equity', 0),
                already_bet_bb=pot_committed_info.get('already_bet_bb', 0),
                stack_bb=pot_committed_info.get('stack_bb', 0),
                cost_to_call_bb=pot_committed_info.get('cost_to_call_bb', 0)
            ))

        # Include short-stack warning if applicable
        if short_stack_info and 'short_stack' in template.sections:
            sections_to_render.append(template.sections['short_stack'].format(
                stack_bb=short_stack_info.get('stack_bb', 0)
            ))

        # Include made hand guidance if applicable
        # Tier determines strong (80%+) vs moderate (65-79%)
        # is_tilted determines firm vs soft tone
        if made_hand_info:
            tier = made_hand_info.get('tier', 'strong')  # 'strong' or 'moderate'
            is_tilted = made_hand_info.get('is_tilted', False)
            tone = 'soft' if is_tilted else 'firm'
            section_name = f'made_hand_{tier}_{tone}'

            if section_name in template.sections:
                sections_to_render.append(template.sections[section_name].format(
                    hand_name=made_hand_info.get('hand_name', 'a strong hand'),
                    equity=made_hand_info.get('equity', 0)
                ))

        # Include equity verdict if applicable (GTO foundation)
        if equity_verdict_info:
            # Get both equity values (fall back to equity_random if equity_ranges unavailable)
            equity_random = equity_verdict_info.get('equity_random', equity_verdict_info.get('equity', 0))
            equity_ranges = equity_verdict_info.get('equity_ranges', equity_random)
            opponent_stats = equity_verdict_info.get('opponent_stats', '')

            # Choose template based on whether verdict is provided
            if equity_verdict_info.get('verdict') and 'equity_verdict_with_call' in template.sections:
                sections_to_render.append(template.sections['equity_verdict_with_call'].format(
                    equity_random=equity_random,
                    equity_ranges=equity_ranges,
                    required_equity=equity_verdict_info.get('required_equity', 0),
                    pot_odds=equity_verdict_info.get('pot_odds', 0),
                    verdict=equity_verdict_info.get('verdict', ''),
                    opponent_stats=opponent_stats,
                ))
            elif 'equity_verdict' in template.sections:
                sections_to_render.append(template.sections['equity_verdict'].format(
                    equity_random=equity_random,
                    equity_ranges=equity_ranges,
                    required_equity=equity_verdict_info.get('required_equity', 0),
                    pot_odds=equity_verdict_info.get('pot_odds', 0),
                    opponent_stats=opponent_stats,
                ))

        # Include pot odds guidance from YAML template (if enabled and info provided)
        if include_pot_odds and pot_odds_info:
            if pot_odds_info.get('free') and 'pot_odds_free' in template.sections:
                sections_to_render.append(template.sections['pot_odds_free'])
            elif not pot_odds_info.get('free') and 'pot_odds_guidance' in template.sections:
                sections_to_render.append(template.sections['pot_odds_guidance'].format(
                    pot_odds=pot_odds_info.get('pot_odds', 0),
                    equity_needed=pot_odds_info.get('equity_needed', 0),
                    pot_fmt=pot_odds_info.get('pot_fmt', ''),
                    call_fmt=pot_odds_info.get('call_fmt', ''),
                    pot_odds_extra=pot_odds_info.get('pot_odds_extra', ''),
                ))

        if include_mind_games and 'mind_games' in template.sections:
            sections_to_render.append(template.sections['mind_games'])

        # Response format: simple JSON or full dramatic sequence
        if use_simple_response_format and 'response_format_simple' in template.sections:
            sections_to_render.append(template.sections['response_format_simple'])
        elif include_dramatic_sequence and 'dramatic_sequence' in template.sections:
            sections_to_render.append(template.sections['dramatic_sequence'])

        # Join all sections
        rendered = "\n\n".join(sections_to_render)

        # Append drama context at END (critical - avoids biasing decision)
        if drama_context:
            level = drama_context.get('level', 'routine')
            tone = drama_context.get('tone', 'neutral')
            drama_text = DRAMA_CONTEXTS.get(level, '')
            tone_modifier = TONE_MODIFIERS.get(tone, '')
            if drama_text:
                rendered = f"{rendered}\n\n{drama_text}{tone_modifier}"

        # Phase 2: Append expression guidance (visibility + tempo)
        if expression_guidance:
            rendered = f"{rendered}\n\n{expression_guidance}"

        return rendered

    def render_correction_prompt(
        self,
        original_response: str,
        error_description: str,
        valid_actions: list[str],
        context: dict,
    ) -> str:
        """Render a targeted correction prompt for AI decision errors.

        Used when an AI response has semantic errors (invalid action, missing fields, etc.)
        to request a corrected response without repeating the entire game state.

        Args:
            original_response: The AI's original (invalid) response
            error_description: Human-readable description of what went wrong
            valid_actions: List of valid actions for current game state
            context: Game context dict with call_amount, min_raise, max_raise

        Returns:
            Correction prompt string
        """
        call_amount = context.get('call_amount', 0)
        min_raise = context.get('min_raise', 0)
        max_raise = context.get('max_raise', 0)
        big_blind = context.get('big_blind', 0)

        def fmt_bb(amount):
            if big_blind > 0:
                return f"{amount / big_blind:.2f} BB"
            return f"${amount}"

        # Build context hints based on available actions
        action_hints = []
        if 'fold' in valid_actions:
            action_hints.append("- 'fold': Give up the hand")
        if 'check' in valid_actions:
            action_hints.append("- 'check': Pass without betting (free)")
        if 'call' in valid_actions:
            action_hints.append(f"- 'call': Match the bet (costs {fmt_bb(call_amount)})")
        if 'raise' in valid_actions:
            action_hints.append(f"- 'raise': Increase the bet (raise_to between {fmt_bb(min_raise)} and {fmt_bb(max_raise)}, the total you're raising TO)")
        if 'all_in' in valid_actions or 'all-in' in valid_actions:
            action_hints.append("- 'all_in': Bet all your remaining chips")

        action_hints_str = "\n".join(action_hints)

        return f"""Your previous response had an error:

ERROR: {error_description}

Your response was:
{original_response}

Please provide a CORRECTED JSON response. Keep your same reasoning and strategy, just fix the error.

VALID ACTIONS:
{action_hints_str}

REQUIREMENTS:
1. Your 'action' must be one of: {', '.join(valid_actions)}
2. If action is 'raise', include 'bet_sizing' with your sizing strategy (e.g., '2/3 pot value bet')
3. If action is 'raise', you MUST include 'raise_to' with a valid total bet amount ({fmt_bb(min_raise)} to {fmt_bb(max_raise)})
4. Include 'inner_monologue' with your thinking

Respond with valid JSON only. No explanation or markdown, just the JSON object."""


# Response format definitions - structured as: Think → Decide → React
RESPONSE_FORMAT = {
    # THINKING (Work through the decision)
    "inner_monologue": "REQUIRED: Your private thoughts — read the board, assess your hand, weigh the odds, and reason toward a decision",
    "hand_strategy": "REQUIRED on first action: Your plan for this hand — what are you trying to do and what will you do if called or raised? Be specific. (locked in for the rest of the hand)",
    "player_observations": "OPTIONAL: Notes about other players' behavior and patterns",
    "hand_strength": "OPTIONAL: Your assessment of your hand (weak/marginal/strong/monster)",
    "bluff_likelihood": "OPTIONAL: % likelihood you're bluffing (0-100)",

    # DECISION (Lock in your action)
    "action": "REQUIRED: Your final action from the provided options",
    "bet_sizing": "REQUIRED if raising: Name your bet sizing BEFORE the amount (e.g., '2/3 pot value bet', 'min-raise to see cheap flop', '1.5x pot overbet bluff')",
    "raise_to": "REQUIRED if raising: Total bet amount (the amount you're raising TO, not BY)",

    # REACTION (Visible response after deciding)
    "dramatic_sequence": "OPTIONAL: Your visible reaction as a list of beats. Mix speech (plain text) and actions (*in asterisks*). Match intensity to the moment."
}


# Example personas with different play styles
# Examples follow the thinking flow: Think → Decide → React
PERSONA_EXAMPLES = {
    "Eeyore": {
        "play_style": "tight",
        "sample_response": {
            # THINKING
            "inner_monologue": "Another miserable hand. 2D and 3C — early position, small pot, everyone looks confident. Not worth chasing anything with these cards. No point throwing good chips after bad. Checking is free, at least.",
            "hand_strategy": "Weak hand, no plan. Check-fold to any aggression.",
            "player_observations": {"pooh": "playing loose, possibly bluffing"},
            "hand_strength": "weak",
            "bluff_likelihood": 10,

            # DECISION
            "action": "check",
            "raise_to": 0,

            # REACTION
            "dramatic_sequence": ["*looks at feet*", "*lets out a big sigh*", "Oh bother, just my luck. Another miserable hand, I suppose."]
        }
    },
    "Clint Eastwood": {
        "play_style": "loose and aggressive",
        "sample_response": {
            # THINKING
            "inner_monologue": "Three hearts on the board, pot's building. John keeps glancing at his chips — he's nervous. A raise might take it down right here, and if not, I've got outs to the flush. Semi-bluff with equity. Either win now or improve on the next card.",
            "hand_strategy": "Semi-bluff the flush draw. If called, barrel turn on any heart or scare card. Give up on blanks.",
            "player_observations": {"john": "seems nervous, keeps glancing at chips"},
            "hand_strength": "marginal but drawing",
            "bluff_likelihood": 25,

            # DECISION
            "action": "raise",
            "bet_sizing": "3/4 pot semi-bluff with flush draw equity",
            "raise_to": 150,

            # REACTION
            "dramatic_sequence": ["*narrows eyes*", "Your move."]
        }
    }
}
