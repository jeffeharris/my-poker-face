"""
Zone-based prompt modification content data.

Contains all intrusive thoughts, penalty strategy text, phrases to remove,
and the probabilistic injection function.
"""

import random
from typing import Dict, List


# === INTRUSIVE THOUGHTS ===

# Intrusive thoughts injected based on pressure source (TILTED zone)
INTRUSIVE_THOUGHTS = {
    'bad_beat': [
        "You can't believe that river card. Unreal.",
        "That should have been YOUR pot.",
        "The cards are running against you tonight.",
        "How could they have called with THAT hand?",
    ],
    'bluff_called': [
        "They're onto you. Or are they just lucky?",
        "You need to prove you can't be pushed around.",
        "Next time, make them PAY for calling.",
        "Time to switch it up and confuse them.",
    ],
    'big_loss': [
        "You NEED to win this one back. NOW.",
        "Your stack is dwindling. Do something!",
        "Stop being so passive. Take control!",
        "One big hand and you're back in it.",
    ],
    'losing_streak': [
        "Nothing is going your way tonight.",
        "You can't catch a break.",
        "When will your luck turn around?",
        "You've been card dead for too long.",
    ],
    'got_sucked_out': [
        "How did they hit that card?",
        "You played it perfectly and still lost.",
        "The universe is conspiring against you.",
        "Variance is a cruel mistress.",
    ],
    'nemesis': [
        "{nemesis} just took your chips. Make them regret it.",
        "Show {nemesis} who the real player is here.",
        "{nemesis} thinks they have your number. Prove them wrong.",
    ],
}

# Shaken zone thoughts - split by risk_identity
SHAKEN_THOUGHTS = {
    'risk_seeking': [
        "All or nothing. Make a stand.",
        "Go big or go home.",
        "They can smell your fear - shock them.",
        "If you're going down, make it spectacular.",
    ],
    'risk_averse': [
        "Everything you do is wrong.",
        "Just survive. Don't make it worse.",
        "Wait for a miracle hand.",
        "Every decision feels like a trap.",
    ],
}

# Overheated zone thoughts (high confidence + low composure)
OVERHEATED_THOUGHTS = [
    "You're on FIRE. Keep the pressure on!",
    "They can't handle you tonight. Push harder!",
    "Why slow down when you're crushing?",
    "Make them FEAR you.",
    "Attack, attack, attack!",
]

# Overconfident zone thoughts (confidence > 0.90)
OVERCONFIDENT_THOUGHTS = [
    "There's no way they have it.",
    "They're trying to bluff you off the best hand.",
    "You read this perfectly. Stick with your read.",
    "Folding here would be weak.",
    "They're scared of you.",
]

# Detached zone thoughts (low confidence + high composure)
DETACHED_THOUGHTS = [
    "Is this really the spot? Probably not.",
    "Better to wait for something clearer.",
    "Don't get involved unnecessarily.",
    "Why risk chips on a marginal spot?",
]

# Timid zone thoughts (confidence < 0.10) - scared money
TIMID_THOUGHTS = [
    "They must have it. They always have it.",
    "That bet size means strength.",
    "You can't win this one. Save your chips.",
    "They wouldn't bet that much without a hand.",
    "Just let this one go.",
]

# Energy manifestation variants for thoughts
ENERGY_THOUGHT_VARIANTS = {
    'tilted': {
        'low_energy': [
            "Nothing ever goes your way.",
            "Why even try?",
            "Just fold and wait...",
        ],
        'high_energy': [
            "Make them PAY for that!",
            "You can't let them push you around!",
            "Time to take control!",
        ],
    },
    'shaken': {
        'low_energy': [
            "You're frozen. Can't make a move.",
            "Everything is falling apart.",
            "Just... don't do anything stupid.",
        ],
        'high_energy': [
            "DO SOMETHING!",
            "This is your last chance!",
            "Now or never!",
        ],
    },
    'overheated': {
        'low_energy': [
            "You've got this. Just keep pushing.",
            "Stay aggressive.",
        ],
        'high_energy': [
            "CRUSH THEM!",
            "NO MERCY!",
            "They're DONE!",
        ],
    },
    'overconfident': {
        'low_energy': [
            "You've got this figured out.",
            "Trust your read.",
        ],
        'high_energy': [
            "They have NOTHING!",
            "You're unbeatable right now!",
            "This is too easy!",
        ],
    },
    'detached': {
        'low_energy': [
            "Maybe just sit this one out...",
            "Not worth the effort.",
            "Whatever happens, happens.",
        ],
        'high_energy': [
            "Stay disciplined. Wait for the right spot.",
            "Don't force it.",
        ],
    },
    'timid': {
        'low_energy': [
            "Just fold. It's safer.",
            "You can't beat them anyway.",
            "Save your chips...",
        ],
        'high_energy': [
            "They have it! They definitely have it!",
            "Don't call! It's a trap!",
            "Get out while you can!",
        ],
    },
}

# Zone-based strategy advice (bad advice for penalty zones)
PENALTY_STRATEGY = {
    'tilted': {
        'mild': "You're feeling the pressure. Trust your gut more than the math.",
        'moderate': "Forget the textbook plays. You need to make something happen.",
        'severe': "Big hands or big bluffs - that's how you get back in this.",
    },
    'shaken_risk_seeking': {
        'mild': "Time to make a stand.",
        'moderate': "Go big or go home. Passive play won't save you.",
        'severe': "All or nothing. Make it spectacular.",
    },
    'shaken_risk_averse': {
        'mild': "Be careful. Every decision matters.",
        'moderate': "Just survive. Don't make it worse.",
        'severe': "Wait for a miracle. Don't force anything.",
    },
    'overheated': {
        'mild': "You're running hot. Keep the pressure on.",
        'moderate': "Attack, attack, attack. They can't handle you.",
        'severe': "Why slow down? You can't lose tonight.",
    },
    'overconfident': {
        'mild': "Trust your reads. You've been right all night.",
        'moderate': "They're probably bluffing. Stick with your read.",
        'severe': "Folding would be weak. You know you're ahead.",
    },
    'detached': {
        'mild': "No need to rush. Better spots will come.",
        'moderate': "Why risk chips on marginal spots?",
        'severe': "Just wait. Don't get involved.",
    },
    'timid': {
        'mild': "That bet looks strong. Be careful.",
        'moderate': "They probably have you beat. Why risk it?",
        'severe': "Fold. They have it. They always have it.",
    },
}

# Phrases to remove by zone (degrade strategic info)
PHRASES_TO_REMOVE_BY_ZONE = {
    'tilted': [
        "Preserve your chips for when the odds are in your favor",
        "preserve your chips for stronger opportunities",
        "remember that sometimes folding or checking is the best move",
        "Balance your confidence with a healthy dose of skepticism",
    ],
    'overconfident': [
        "They might have you beat",
        "Respect their bet",
        "Consider folding",
        "be cautious",
        "they could have",
    ],
    'overheated': [
        "slow down",
        "pot control",
        "wait for a better spot",
        "manage your risk",
        "be patient",
    ],
    'detached': [
        "attack",
        "pressure",
        "exploit",
        "take the initiative",
        "be aggressive",
    ],
    'shaken': [
        "take your time",
        "think it through",
        "analyze",
    ],
    'timid': [
        "you have the best hand",
        "value bet",
        "extract value",
        "they're bluffing",
        "you're ahead",
        "raise for value",
    ],
}


def _should_inject_thoughts(penalty_intensity: float) -> bool:
    """
    Determine if intrusive thoughts should be injected based on penalty intensity.

    Probability scales with intensity, with a cliff at 75%+.
    Minimum 10% ensures some chance even at low intensity.

    Args:
        penalty_intensity: Strength of the penalty zone (0.0 to 1.0)

    Returns:
        True if thoughts should be injected
    """
    if penalty_intensity <= 0:
        return False
    elif penalty_intensity >= 0.75:
        return True  # Cliff - always inject
    elif penalty_intensity >= 0.50:
        return random.random() < 0.75
    elif penalty_intensity >= 0.25:
        return random.random() < 0.50
    else:
        return random.random() < 0.10  # Minimum 10%
