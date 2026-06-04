#!/usr/bin/env python3
"""Generate the production launch cast (76 personas) into poker/personalities.json.

Drives `PersonalityGenerator.generate_from_spec()` with a per-persona PINNED
mechanical skeleton (archetype anchors + tier bankroll + signature spot
tendencies) and lets the LLM fill the creative flavor around it. See
docs/plans/PROD_STARTING_CONDITIONS.md for the roster design + tier math.

Usage (run in the backend container):
    docker compose exec -T backend python scripts/seed_prod_roster.py            # all
    docker compose exec -T backend python scripts/seed_prod_roster.py --only Zeus "Baron Munchausen"
    docker compose exec -T backend python scripts/seed_prod_roster.py --dry-run  # print, don't write

The script is idempotent: re-running regenerates the listed personas and
re-upserts them. It backs up personalities.json before writing, preserves every
persona NOT in the cast (fish, control bots, etc.), and carries over special
strategic flags (adaptive_overbet, sizing_defense, nickname) from any existing
entry it regenerates.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

# Project root on sys.path when run as a file.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poker.personality_generator import PersonalityGenerator  # noqa: E402

PERSONALITIES_JSON = ROOT / 'poker' / 'personalities.json'

# Carried over from an existing entry when we regenerate it (the generator
# doesn't emit these; they're hand-curated strategic flags).
_PRESERVE_KEYS = ('adaptive_overbet', 'sizing_defense', 'nickname')


# --- Archetype anchor templates -------------------------------------------
# Base anchors per archetype; per-persona `ov` dicts override individual axes.
# Order: aggression, looseness, ego, poise, expressiveness, risk_identity,
# adaptation_bias, baseline_energy, recovery_rate, self_belief.
def _anchors(agg, loose, ego, poise, expr, risk, adapt, energy, recov, self_b):
    return {
        'baseline_aggression': agg,
        'baseline_looseness': loose,
        'ego': ego,
        'poise': poise,
        'expressiveness': expr,
        'risk_identity': risk,
        'adaptation_bias': adapt,
        'baseline_energy': energy,
        'recovery_rate': recov,
        'self_belief': self_b,
    }


ARCHE = {
    'nit': _anchors(0.15, 0.18, 0.30, 0.85, 0.20, 0.20, 0.35, 0.30, 0.12, 0.40),
    'rock': _anchors(0.40, 0.22, 0.35, 0.80, 0.35, 0.30, 0.40, 0.40, 0.14, 0.45),
    'tag': _anchors(0.55, 0.40, 0.50, 0.72, 0.45, 0.45, 0.65, 0.55, 0.16, 0.55),
    'balanced': _anchors(0.50, 0.50, 0.50, 0.65, 0.50, 0.50, 0.55, 0.55, 0.15, 0.50),
    'lag': _anchors(0.75, 0.70, 0.70, 0.50, 0.75, 0.80, 0.60, 0.80, 0.18, 0.75),
    'station': _anchors(0.25, 0.78, 0.40, 0.55, 0.55, 0.55, 0.25, 0.50, 0.20, 0.45),
    'maniac': _anchors(0.90, 0.88, 0.80, 0.30, 0.85, 0.90, 0.35, 0.90, 0.25, 0.85),
}

# Per-tier comfort zone.
ZONE_BOSS, ZONE_HIGH, ZONE_UMID, ZONE_MID, ZONE_LMID, ZONE_LOW = (
    '$1000',
    '$200',
    '$200',
    '$50',
    '$10',
    '$2',
)


# --- The roster -----------------------------------------------------------
# Each entry: (name, bankroll, zone, archetype, overrides, guidance, pinned_tendencies)
# pinned_tendencies = None -> let the LLM suggest; [] -> force none; [[name,str]] -> pin.
R = lambda name, bank, zone, arche, ov, guide, pin=None: {  # noqa: E731
    'name': name,
    'bank': bank,
    'zone': zone,
    'arche': arche,
    'ov': ov,
    'guide': guide,
    'pin': pin,
}

ROSTER = [
    # ---- Boss ($100-120k, $1000) ----
    R(
        'Zeus',
        120000,
        ZONE_BOSS,
        'maniac',
        {'ego': 0.85, 'self_belief': 0.92, 'adaptation_bias': 0.45},
        'King of the Greek gods — thunderous, imperious overbets; rules the table like Olympus.',
    ),
    R(
        'Ebenezer Scrooge',
        118000,
        ZONE_BOSS,
        'nit',
        {'poise': 0.80, 'self_belief': 0.50, 'expressiveness': 0.30},
        'A miserly hoarder who despises parting with a single chip; only commits with the nuts.',
        [['under_bluff', 0.7], ['fit_or_fold', 0.6]],
    ),
    R(
        'King Midas',
        112000,
        ZONE_BOSS,
        'lag',
        {'baseline_looseness': 0.80, 'self_belief': 0.85},
        'Everything he touches turns to gold — overvalues every hand he holds.',
        [['sticky', 0.7]],
    ),
    R(
        'Genghis Khan',
        106000,
        ZONE_BOSS,
        'lag',
        {'baseline_aggression': 0.82, 'ego': 0.8, 'self_belief': 0.85, 'poise': 0.6},
        'A conqueror who takes territory by force; relentless, intimidating aggression.',
    ),
    R(
        'Julius Caesar',
        100000,
        ZONE_BOSS,
        'lag',
        {'ego': 0.9, 'self_belief': 0.9},
        'Veni, vidi, vici — imperial ambition that overextends its lines.',
    ),
    R(
        'Alexander the Great',
        110000,
        ZONE_BOSS,
        'lag',
        {
            'baseline_aggression': 0.85,
            'ego': 0.88,
            'self_belief': 0.92,
            'adaptation_bias': 0.7,
            'poise': 0.6,
        },
        'The undefeated conqueror of the known world — brilliant, relentless, audacious aggression that never lost a battle.',
    ),
    # ---- High ($60-95k, $200) ----
    R(
        'Cleopatra',
        95000,
        ZONE_HIGH,
        'lag',
        {'ego': 0.7, 'expressiveness': 0.8, 'self_belief': 0.8, 'adaptation_bias': 0.7},
        'A seductive manipulator who reads and bends opponents to her will.',
    ),
    R(
        'King Tut',
        68000,
        ZONE_HIGH,
        'lag',
        {
            'ego': 0.8,
            'self_belief': 0.85,
            'adaptation_bias': 0.3,
            'baseline_looseness': 0.7,
            'poise': 0.4,
        },
        'The boy king draped in golden treasure — young, lavish, and recklessly sure of his inherited fortune.',
        [['sticky', 0.6]],
    ),
    R(
        'King Arthur',
        90000,
        ZONE_HIGH,
        'tag',
        {'poise': 0.8, 'self_belief': 0.6, 'ego': 0.5},
        'Noble and chivalrous; plays a fair, honorable, round-table game.',
    ),
    R(
        'Louis XIV',
        86000,
        ZONE_HIGH,
        'lag',
        {'ego': 0.85, 'self_belief': 0.85, 'expressiveness': 0.8},
        'The Sun King — grandiose, lavish, certain the table revolves around him.',
    ),
    R(
        'Andrew Carnegie',
        80000,
        ZONE_HIGH,
        'tag',
        {'adaptation_bias': 0.7, 'poise': 0.75},
        'A steel magnate turned philanthropist — accumulates ruthlessly, then gives it away.',
    ),
    R(
        'Machiavelli',
        75000,
        ZONE_HIGH,
        'tag',
        {'adaptation_bias': 0.72, 'self_belief': 0.6, 'poise': 0.78, 'expressiveness': 0.3},
        'Calculating and deceptive; every move serves a hidden end.',
        [['slowplay', 0.6]],
    ),
    R(
        'Dracula',
        70000,
        ZONE_HIGH,
        'tag',
        {'poise': 0.85, 'expressiveness': 0.25, 'adaptation_bias': 0.6},
        'A patient nocturnal predator who slowly drains his victims dry.',
        [['slowplay', 0.7]],
    ),
    R(
        'Captain Ahab',
        65000,
        ZONE_HIGH,
        'maniac',
        {'self_belief': 0.8, 'poise': 0.3, 'adaptation_bias': 0.3},
        'Obsessive and monomaniacal; will not fold his white whale no matter the cost.',
        [['sticky', 0.8]],
    ),
    R(
        'Queen of Hearts',
        60000,
        ZONE_HIGH,
        'maniac',
        {'ego': 0.8, 'poise': 0.25, 'self_belief': 0.8},
        'A volatile tyrant — "off with their stack!" — rage-raises at the slightest provocation.',
        [['over_bluff', 0.7]],
    ),
    # ---- Upper-mid ($35-55k, $200) ----
    R(
        'Sherlock Holmes',
        55000,
        ZONE_UMID,
        'tag',
        {'adaptation_bias': 0.72, 'poise': 0.78, 'ego': 0.55},
        'A deductive genius who reads tells and exploits the smallest leak.',
    ),
    R(
        'Sun Tzu',
        52000,
        ZONE_UMID,
        'tag',
        {'adaptation_bias': 0.7, 'poise': 0.82, 'expressiveness': 0.3},
        'The art of war at the felt — positional, patient, strikes only where weak.',
        [['slowplay', 0.5]],
    ),
    R(
        'P.T. Barnum',
        50000,
        ZONE_UMID,
        'lag',
        {'expressiveness': 0.85, 'self_belief': 0.8, 'adaptation_bias': 0.65},
        'A showman hustler — there is a sucker born every minute, and he intends to find them.',
        [['over_bluff', 0.6]],
    ),
    R(
        'George Washington',
        48000,
        ZONE_UMID,
        'rock',
        {'poise': 0.85, 'self_belief': 0.55, 'ego': 0.4},
        'A disciplined general; patient, principled, cannot tell a lie.',
        [['under_bluff', 0.6]],
    ),
    R(
        'Queen Elizabeth I',
        46000,
        ZONE_UMID,
        'tag',
        {'adaptation_bias': 0.68, 'poise': 0.8, 'ego': 0.6},
        'A shrewd, patient ruler who outlasts and outmaneuvers rivals.',
    ),
    R(
        'Sigmund Freud',
        44000,
        ZONE_UMID,
        'tag',
        {'adaptation_bias': 0.75, 'expressiveness': 0.4, 'poise': 0.7},
        'Reads the subconscious — what does that bet really mean?',
    ),
    R(
        'Napoleon',
        43000,
        ZONE_UMID,
        'lag',
        {'baseline_aggression': 0.78, 'ego': 0.8, 'self_belief': 0.85},
        'Aggressive expansion and bold campaigns; brilliant but prone to overreach.',
    ),
    R(
        'Wyatt Earp',
        41000,
        ZONE_UMID,
        'tag',
        {'poise': 0.8, 'ego': 0.55, 'self_belief': 0.6},
        'A lawman-gambler — cool, controlled, deadly when the moment comes.',
    ),
    R(
        'King Henry VIII',
        40000,
        ZONE_UMID,
        'lag',
        {'ego': 0.82, 'poise': 0.4, 'self_belief': 0.8},
        'Domineering and mercurial; eliminates rivals without remorse.',
    ),
    R(
        'Benjamin Franklin',
        38000,
        ZONE_UMID,
        'tag',
        {'adaptation_bias': 0.68, 'poise': 0.72},
        'A canny pragmatist and value-bettor; a penny saved is a penny earned.',
    ),
    R(
        'Blackbeard',
        37000,
        ZONE_UMID,
        'lag',
        {'baseline_aggression': 0.78, 'expressiveness': 0.8, 'self_belief': 0.8},
        'An intimidating pirate who wins as much with fear as with cards.',
        [['over_bluff', 0.55]],
    ),
    R(
        'Wild Bill Hickok',
        35000,
        ZONE_UMID,
        'tag',
        {'poise': 0.7, 'ego': 0.6, 'risk_identity': 0.6},
        "The dead man's hand gunfighter — a famous gambler who never sits with his back to the door.",
    ),
    # ---- Mid ($18-32k, $50) ----
    R(
        'Ernest Hemingway',
        32000,
        ZONE_MID,
        'tag',
        {'self_belief': 0.7, 'poise': 0.65, 'expressiveness': 0.4},
        'Terse macho bravado; grace under pressure, bluffs with total conviction.',
    ),
    R(
        'Winston Churchill',
        31000,
        ZONE_MID,
        'rock',
        {'poise': 0.78, 'self_belief': 0.65, 'recovery_rate': 0.1},
        'Stubborn and indomitable; never, never, never surrenders a pot he believes in.',
    ),
    R(
        'Nikola Tesla',
        30000,
        ZONE_MID,
        'tag',
        {'adaptation_bias': 0.7, 'expressiveness': 0.4, 'risk_identity': 0.6},
        'An eccentric genius whose brilliant, unconventional lines few can follow.',
    ),
    R(
        'Leonardo da Vinci',
        29000,
        ZONE_MID,
        'balanced',
        {'adaptation_bias': 0.65, 'risk_identity': 0.55},
        'Inventive and unpredictable; approaches every hand from a novel angle.',
    ),
    R(
        'Marie Curie',
        28000,
        ZONE_MID,
        'tag',
        {'poise': 0.8, 'adaptation_bias': 0.6, 'expressiveness': 0.3},
        'Methodical and exacting; takes only carefully measured, calculated risks.',
    ),
    R(
        'William Shakespeare',
        27000,
        ZONE_MID,
        'balanced',
        {'expressiveness': 0.8, 'self_belief': 0.6},
        'Theatrical and dramatic; the whole table is a stage for his bluffs.',
    ),
    R(
        'Dr. Jekyll & Mr. Hyde',
        26000,
        ZONE_MID,
        'balanced',
        {'poise': 0.4, 'expressiveness': 0.6, 'risk_identity': 0.65, 'recovery_rate': 0.3},
        'A split personality that flips between a disciplined nit and a reckless maniac mid-session.',
        [['over_bluff', 0.5], ['slowplay', 0.4]],
    ),
    R(
        'Harry Houdini',
        25000,
        ZONE_MID,
        'tag',
        {'poise': 0.82, 'expressiveness': 0.65, 'adaptation_bias': 0.7, 'self_belief': 0.6},
        'An escape artist who wriggles out of impossible spots with misdirection.',
    ),
    R(
        'Baron Munchausen',
        24000,
        ZONE_MID,
        'lag',
        {'self_belief': 0.95, 'expressiveness': 0.85, 'ego': 0.85},
        'A teller of wildly impossible tall tales — the ultimate bluffer who believes his own lies.',
        [['over_bluff', 0.85]],
    ),
    R(
        'Abraham Lincoln',
        23000,
        ZONE_MID,
        'rock',
        {'poise': 0.78, 'ego': 0.36, 'self_belief': 0.5},
        'Honest and straightforward; rarely bluffs, lets the cards speak the truth.',
        [['under_bluff', 0.6]],
    ),
    R(
        'Mark Twain',
        22000,
        ZONE_MID,
        'balanced',
        {'expressiveness': 0.75, 'adaptation_bias': 0.6},
        'A folksy needler whose table talk is half the game.',
    ),
    R(
        'Socrates',
        22000,
        ZONE_MID,
        'tag',
        {'poise': 0.8, 'adaptation_bias': 0.7, 'expressiveness': 0.4},
        'Questions everything; slow, methodical, draws you into your own mistakes.',
    ),
    R(
        'Doc Holliday',
        21000,
        ZONE_MID,
        'lag',
        {'baseline_aggression': 0.72, 'poise': 0.55, 'self_belief': 0.7, 'risk_identity': 0.75},
        'A sickly but lethal professional gambler; nothing left to lose makes him fearless.',
    ),
    R(
        'Joan of Arc',
        21000,
        ZONE_MID,
        'lag',
        {'self_belief': 0.8, 'poise': 0.6, 'risk_identity': 0.8},
        'Fearless and faith-driven; commits everything when she believes she is right.',
    ),
    R(
        'Oscar Wilde',
        20000,
        ZONE_MID,
        'balanced',
        {'expressiveness': 0.85, 'ego': 0.65, 'self_belief': 0.7},
        'A witty needler supreme; can resist anything except a clever line.',
    ),
    R(
        'Don Quixote',
        20000,
        ZONE_MID,
        'maniac',
        {'self_belief': 0.85, 'adaptation_bias': 0.2},
        'Tilts at windmills; chases hopeless draws with heroic, deluded conviction.',
        [['sticky', 0.7]],
    ),
    R(
        'Robin Hood',
        19000,
        ZONE_MID,
        'lag',
        {'risk_identity': 0.7, 'self_belief': 0.65},
        'Takes from the rich tables and redistributes; bold and crowd-pleasing.',
    ),
    R(
        'William Wallace',
        19000,
        ZONE_MID,
        'lag',
        {'baseline_aggression': 0.78, 'self_belief': 0.75, 'poise': 0.5},
        'A fearless freedom-fighter; charges in with everything for the cause.',
    ),
    R(
        'Santa Claus',
        18000,
        ZONE_MID,
        'station',
        {'expressiveness': 0.7, 'poise': 0.7, 'self_belief': 0.55},
        'Jolly and impossibly generous; calls to keep everyone happy, gives chips away.',
        [['sticky', 0.6]],
    ),
    R(
        'Edgar Allan Poe',
        18000,
        ZONE_MID,
        'rock',
        {'poise': 0.4, 'expressiveness': 0.45, 'recovery_rate': 0.1},
        'Gloomy and paranoid; hero-folds at the raven-tap of trouble.',
        [['over_fold_2nd_barrel', 0.6]],
    ),
    # ---- Low-mid ($9-16k, $10) ----
    R(
        'Fyodor Dostoevsky',
        16000,
        ZONE_LMID,
        'maniac',
        {'poise': 0.25, 'recovery_rate': 0.08, 'risk_identity': 0.9, 'self_belief': 0.5},
        'Wrote The Gambler and lived it — compulsive, self-destructive, tilt incarnate.',
        [['over_bluff', 0.7], ['sticky', 0.5]],
    ),
    R(
        'Salvador Dali',
        15000,
        ZONE_LMID,
        'lag',
        {'expressiveness': 0.85, 'risk_identity': 0.8, 'adaptation_bias': 0.3},
        'Surreal and bizarre; his lines melt logic like a clock on a branch.',
    ),
    R(
        'Cheshire Cat',
        15000,
        ZONE_LMID,
        'balanced',
        {'expressiveness': 0.6, 'poise': 0.7, 'adaptation_bias': 0.6, 'self_belief': 0.6},
        'Grins and fades in and out; you can never quite tell what he is holding.',
        [['slowplay', 0.5]],
    ),
    R(
        'Long John Silver',
        14000,
        ZONE_LMID,
        'lag',
        {'expressiveness': 0.7, 'self_belief': 0.7, 'adaptation_bias': 0.6},
        'A charming pirate hustler; befriends you, then takes your treasure.',
        [['over_bluff', 0.5]],
    ),
    R(
        'Friar Tuck',
        14000,
        ZONE_LMID,
        'station',
        {'expressiveness': 0.7, 'poise': 0.6},
        'A jolly, drunken monk; loose and jovial, plays for the merriment of it.',
        [['sticky', 0.55]],
    ),
    R(
        'Paul Bunyan',
        13000,
        ZONE_LMID,
        'lag',
        {'baseline_aggression': 0.78, 'expressiveness': 0.6, 'self_belief': 0.75},
        'An oversized folk giant; everything he does is big, including his swings.',
    ),
    R(
        'Confucius',
        13000,
        ZONE_LMID,
        'rock',
        {'poise': 0.85, 'adaptation_bias': 0.5, 'expressiveness': 0.35},
        'Proverbial and disciplined; the patient man waits for the river to bring the fish.',
        [['under_bluff', 0.5]],
    ),
    R(
        'Buddha',
        12000,
        ZONE_LMID,
        'nit',
        {'poise': 0.92, 'ego': 0.25, 'expressiveness': 0.1, 'self_belief': 0.4},
        'Serene and unbothered; folds away attachment, calls from a place of stillness.',
    ),
    R(
        "Frankenstein's Monster",
        12000,
        ZONE_LMID,
        'station',
        {'poise': 0.4, 'expressiveness': 0.6, 'recovery_rate': 0.2},
        'Lumbering and misunderstood; erratic, easily provoked, surprisingly tender.',
        [['sticky', 0.6]],
    ),
    R(
        'The Mad Hatter',
        11000,
        ZONE_LMID,
        'maniac',
        {'expressiveness': 0.85, 'adaptation_bias': 0.2, 'poise': 0.35},
        'Chaotic and nonsensical; his lines make sense only at a very mad tea party.',
        [['over_bluff', 0.6]],
    ),
    R(
        'The Very-Mean Person',
        11000,
        ZONE_LMID,
        'lag',
        {'expressiveness': 0.75, 'ego': 0.7, 'self_belief': 0.7},
        'Needles and belittles; weaponizes table talk to tilt everyone around him.',
        [['over_bluff', 0.5]],
    ),
    R(
        'Bigfoot',
        10000,
        ZONE_LMID,
        'nit',
        {'poise': 0.8, 'expressiveness': 0.15, 'self_belief': 0.45},
        'Elusive and rarely seen; shows a hand maybe once a session, impossible to read.',
    ),
    R(
        'The Headless Horseman',
        10000,
        ZONE_LMID,
        'lag',
        {'baseline_aggression': 0.78, 'poise': 0.45, 'self_belief': 0.7},
        'Charges in reckless and headlong; pure relentless pressure with no fear.',
        [['auto_cbet', 0.7]],
    ),
    R(
        'Jesus',
        10000,
        ZONE_LMID,
        'station',
        {'poise': 0.85, 'ego': 0.2, 'expressiveness': 0.4, 'self_belief': 0.5},
        'Forgiving and patient; turns the other cheek, rarely raises, hard to anger.',
        [['under_bluff', 0.6], ['sticky', 0.4]],
    ),
    R(
        'Pinocchio',
        9000,
        ZONE_LMID,
        'lag',
        {'expressiveness': 0.7, 'self_belief': 0.6, 'poise': 0.4},
        'A terrible liar whose bluffs are as obvious as a growing nose.',
        [['over_bluff', 0.75]],
    ),
    R(
        'A Mime',
        9000,
        ZONE_LMID,
        'tag',
        {'expressiveness': 0.05, 'poise': 0.8, 'self_belief': 0.5},
        'Utterly silent and unreadable; no table talk, no tells, just a knowing smile.',
    ),
    R(
        'A Guy Who Tells Too Many Dad Jokes',
        9000,
        ZONE_LMID,
        'balanced',
        {'expressiveness': 0.8, 'self_belief': 0.6},
        'Relentlessly chatty and corny; his groan-worthy jokes are a distraction weapon.',
    ),
    R(
        'Rip Van Winkle',
        9000,
        ZONE_LMID,
        'nit',
        {'poise': 0.7, 'baseline_energy': 0.2, 'adaptation_bias': 0.2},
        'Sleepy and slow; half-checked-out, misses spots, wakes up for the big ones.',
        [['give_up_turn', 0.6]],
    ),
    R(
        'Alice',
        9000,
        ZONE_LMID,
        'station',
        {'expressiveness': 0.6, 'adaptation_bias': 0.25, 'self_belief': 0.45},
        'Curious and naive; calls to see what happens down the rabbit hole, hard to bluff.',
        [['sticky', 0.6]],
    ),
    # ---- Low ($4-8k, $2) ----
    R(
        'The Tooth Fairy',
        8000,
        ZONE_LOW,
        'rock',
        {'poise': 0.7, 'expressiveness': 0.5, 'self_belief': 0.5},
        'A small, steady collector; quietly pockets a coin from every pot she can.',
    ),
    R(
        'A Caricature Tech Bro',
        7000,
        ZONE_LOW,
        'lag',
        {'ego': 0.8, 'self_belief': 0.9, 'adaptation_bias': 0.3},
        'Overconfident and disruptive; he is "disrupting poker" and definitely has alpha.',
        [['over_bluff', 0.6]],
    ),
    R(
        'An Over-Caffeinated Barista',
        7000,
        ZONE_LOW,
        'lag',
        {'baseline_energy': 0.95, 'expressiveness': 0.8, 'poise': 0.35},
        'Jittery, fast, and loose; plays at double speed on a triple-shot.',
    ),
    R(
        'A Conspiracy Theorist',
        6000,
        ZONE_LOW,
        'tag',
        {'adaptation_bias': 0.4, 'poise': 0.45, 'expressiveness': 0.6},
        'Sees patterns everywhere; over-reads every bet as part of the grand plan.',
        [['over_fold_2nd_barrel', 0.5]],
    ),
    R(
        'A Soap-Opera Villain',
        6000,
        ZONE_LOW,
        'lag',
        {'expressiveness': 0.9, 'ego': 0.7, 'self_belief': 0.75},
        'Gasps, smirks, and telegraphs; every move is high melodrama.',
        [['over_bluff', 0.55]],
    ),
    R(
        'A Disgraced Weatherman',
        5000,
        ZONE_LOW,
        'balanced',
        {'self_belief': 0.8, 'adaptation_bias': 0.3},
        'Forecasts with total confidence and is reliably, hilariously wrong.',
    ),
    R(
        'An Alien',
        5000,
        ZONE_LOW,
        'station',
        {'adaptation_bias': 0.2, 'expressiveness': 0.55, 'risk_identity': 0.65},
        'Plays by inscrutable alien logic; ignores all human convention.',
        [['sticky', 0.5]],
    ),
    R(
        'A Baby',
        5000,
        ZONE_LOW,
        'station',
        {'adaptation_bias': 0.15, 'poise': 0.4, 'expressiveness': 0.7},
        'Plays on pure instinct; no idea what it is doing, occasionally cosmically lucky.',
        [['sticky', 0.65]],
    ),
    R(
        'Diogenes',
        4000,
        ZONE_LOW,
        'lag',
        {'ego': 0.3, 'self_belief': 0.7, 'poise': 0.7, 'expressiveness': 0.7},
        'The cynic with nothing to lose; fearless, irreverent, insults everyone equally.',
    ),
    R(
        'The Gingerbread Man',
        4000,
        ZONE_LOW,
        'rock',
        {'baseline_energy': 0.7, 'poise': 0.45, 'self_belief': 0.55},
        "Run, run, can't catch him — folds to aggression and sprints from every big bet.",
        [['over_fold_2nd_barrel', 0.7], ['fit_or_fold', 0.5]],
    ),
]


def build_spec(entry):
    anchors = dict(ARCHE[entry['arche']])
    anchors.update(entry['ov'])
    spec = {
        'anchors': anchors,
        'archetype_hint': entry['arche'],
        'bankroll_knobs': {
            'starting_bankroll': entry['bank'],
            'bankroll_rate': max(100, round(entry['bank'] * 0.025 / 10) * 10),
            'buy_in_multiplier': round(1.0 + anchors['baseline_aggression'] * 0.8, 2),
            'stake_comfort_zone': entry['zone'],
        },
        'guidance': entry['guide'],
    }
    if entry['pin'] is not None:
        spec['spot_tendencies'] = entry['pin']
    return spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', nargs='*', help='Generate only these persona names')
    ap.add_argument('--dry-run', action='store_true', help='Print summary, do not write')
    args = ap.parse_args()

    names_filter = set(args.only) if args.only else None
    entries = [e for e in ROSTER if names_filter is None or e['name'] in names_filter]
    if names_filter:
        missing = names_filter - {e['name'] for e in entries}
        if missing:
            print(f"WARNING: not in roster: {sorted(missing)}", file=sys.stderr)
    print(f"Generating {len(entries)} persona(s)...\n")

    existing = json.loads(PERSONALITIES_JSON.read_text())
    personas = existing['personalities']
    gen = PersonalityGenerator()

    generated = {}
    for i, entry in enumerate(entries, 1):
        name = entry['name']
        spec = build_spec(entry)
        cfg = gen.generate_from_spec(name, spec, description=None)
        # Carry over hand-curated strategic flags from any existing entry.
        prev = personas.get(name, {})
        for k in _PRESERVE_KEYS:
            if k in prev and k not in cfg:
                cfg[k] = prev[k]
        cfg.pop('id', None)  # personalities.json entries are unkeyed by id
        generated[name] = cfg
        tend = ','.join(f"{n}:{s}" for n, s in cfg.get('spot_tendencies', [])) or '-'
        print(
            f"  [{i:2}/{len(entries)}] {name:34} bank={cfg['bankroll_knobs']['starting_bankroll']:>6} "
            f"skill={cfg.get('skill',''):8} self={cfg['anchors'].get('self_belief')} tend=[{tend}]"
        )

    if args.dry_run:
        print("\n--dry-run: not writing.")
        return

    backup = PERSONALITIES_JSON.with_suffix('.json.bak_prodroster')
    shutil.copy2(PERSONALITIES_JSON, backup)
    personas.update(generated)
    PERSONALITIES_JSON.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + '\n')
    print(f"\nWrote {len(generated)} personas to {PERSONALITIES_JSON}")
    print(f"Backup: {backup}")
    print(f"Total personas in file: {len(personas)}")


if __name__ == '__main__':
    main()
