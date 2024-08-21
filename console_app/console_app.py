from typing import List, Optional

from core.card import Card

CARD_TEMPLATE = '''
.---------.
|{}       |
| {}       |
|         |
|         |
|    {}    |
|       {}|
`---------'
'''
TWO_CARD_TEMPLATE = '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''

def display_hole_cards(cards: [Card, Card]):
    sorted_cards = sorted(cards, key=lambda card: card.value)
    card_1 = sorted_cards[0]
    card_2 = sorted_cards[1]

    # Generate and print each card
    hole_card_art = render_two_cards(card_1, card_2)
    return hole_card_art


def render_card(card):
    rank_left = card.rank.ljust(2)
    rank_right = card.rank.rjust(2)
    card = CARD_TEMPLATE.format(rank_left, Card.SUIT_TO_ASCII[card.suit], Card.SUIT_TO_ASCII[card.suit], rank_right)
    return card


def render_cards(cards: List[Card]) -> Optional[str]:
    card_lines = [render_card(card).strip().split('\n') for card in cards]
    if not card_lines:
        return None
    ascii_card_lines = []
    for lines in zip(*card_lines):
        ascii_card_lines.append('  '.join(lines))
    card_ascii_string = '\n'.join(ascii_card_lines)
    return card_ascii_string


def render_two_cards(card_1, card_2):
    # Generate and print each card
    two_card_ascii_string = TWO_CARD_TEMPLATE.format(card_1.rank,
                                                          card_2.rank,
                                                          Card.SUIT_TO_ASCII[card_1.suit],
                                                          Card.SUIT_TO_ASCII[card_2.suit],
                                                          Card.SUIT_TO_ASCII[card_2.suit],
                                                          card_2.rank)
    return two_card_ascii_string


def get_player_action(player, hand_state):
    game_interface = hand_state["game_interface"]
    community_cards = hand_state['community_cards']
    current_bet = hand_state['current_bet']
    current_pot = hand_state['current_pot']
    cost_to_call = current_pot.get_player_cost_to_call(player)
    total_to_pot = current_pot.get_player_pot_amount(player)

    game_interface.display_text(display_hole_cards(player.cards))
    text_lines = [
        f"{player.name}'s turn. Current cards: {player.cards} Current money: {player.money}",
        f"Community cards: {community_cards}",
        f"Current bet: {current_bet}",
        f"Current pot: {current_pot.total}",
        f"Cost to call: {cost_to_call}",
        f"Total to pot: {total_to_pot}"
    ]

    text = "\n".join(text_lines)

    game_interface.display_text(text)
    action = game_interface.request_action(player.options, "Enter action: \n")

    add_to_pot = 0
    if action is None:
        if "check" in player.options:
            action = "check"
        elif "call" in player.options:
            action = "call"
        else:
            action = "fold"
    if action in ["bet", "b", "be"]:
        add_to_pot = int(input("Enter amount: "))
        action = "bet"
    elif action in ["raise", "r", "ra", "rai", "rais"]:
        raise_amount = int(input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
        add_to_pot = raise_amount + cost_to_call
        action = "raise"
    elif action in ["all-in", "all in", "allin", "a", "al", "all", "all-", "all-i", "alli"]:
        add_to_pot = player.money
        action = "all-in"
    elif action in ["call", "ca", "cal"]:
        add_to_pot = cost_to_call
        action = "call"
    elif action in ["fold", "f", "fo", "fol"]:
        add_to_pot = 0
        action = "fold"
    elif action in ["check", "ch", "che", "chec"]:
        add_to_pot = 0
        action = "check"
    # self.chat_message = input("Enter chat message (optional): ")
    # if not self.chat_message:
    #     f"{self.name} chooses to {action}."
    # TODO: return a dict that can be converted to a PokerAction on the other end
    poker_action = PokerAction(player, action, add_to_pot, hand_state)
    return poker_action