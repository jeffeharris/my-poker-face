import copy
import json
from collections import Counter
import logging
import random
from enum import Enum
from typing import List, Dict, Optional

from cards import Card, Deck, render_cards, render_two_cards
from game import Player, Game, Interface, OpenAILLMAssistant, ConsoleInterface

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)     # DEBUG, INFO, WARNING, ERROR, CRITICAL


class HandEvaluator:
    def __init__(self, cards):
        self.cards = cards
        self.ranks = [card.value for card in cards]
        self.suits = [card.suit for card in cards]
        self.rank_counts = Counter(self.ranks)
        self.suit_counts = Counter(self.suits)

    def evaluate_hand(self):
        checks = [
            self.check_royal_flush,
            self.check_straight_flush,
            self.check_four_of_a_kind,
            self.check_full_house,
            self.check_flush,
            self.check_straight,
            self.check_three_of_a_kind,
            self.check_two_pair,
            self.check_one_pair,
        ]
        for i, check in enumerate(checks, start=1):
            result = check()
            if result[0]:
                return {"hand_rank": i, "hand_values": result[1], "kicker_values": result[2]}
        return {"hand_rank": 10, "hand_values": [], "kicker_values": sorted(self.ranks, reverse=True)[:5]}

    def check_royal_flush(self):
        has_straight_flush, straight_flush_values, _, straight_flush_suit = self.check_straight_flush()
        if has_straight_flush:
            # straight_flush_ranks = [card.value for card in self.cards if card.suit == straight_flush_suit]
            comparison_set = list(set(range(10, 15)))
            comparison_set.reverse()
            if straight_flush_values == comparison_set:
                return True, comparison_set, []
        return False, [], []

    def check_straight_flush(self):
        has_flush, flush_values, _, flush_suit = self.check_flush()
        if has_flush:
            flush_cards = [card for card in self.cards if card.suit == flush_suit]
            has_straight, straight_values, _ = HandEvaluator(flush_cards).check_straight()
            if has_straight:
                return True, straight_values, [], flush_suit
        return False, [], [], []

    def check_four_of_a_kind(self):
        for rank, count in self.rank_counts.items():
            if count == 4:
                kicker = sorted([card for card in self.ranks if card != rank], reverse=True)
                return True, [rank]*4, [kicker]
        return False, [], []

    def check_full_house(self):
        three = None
        two = None
        for rank, count in sorted(self.rank_counts.items(), reverse=True):
            if count >= 3 and three is None:
                three = rank
            elif count >= 2 and two is None:
                two = rank
        if three is not None and two is not None:
            return True, [three]*3 + [two]*2, []
        return False, [], []

    def check_flush(self):
        for suit, count in self.suit_counts.items():
            if count >= 5:
                flush_cards = sorted([card.value for card in self.cards if card.suit == suit], reverse=True)
                return True, flush_cards, [], suit
        return False, [], [], None

    def check_straight(self):
        sorted_values = sorted(self.ranks, reverse=True)
        if not sorted_values:
            return False, [], []
        for top in range(sorted_values[0], 4, -1):
            if set(range(top-4, top+1)).issubset(set(sorted_values)):
                straight_values = list(range(top, top-5, -1))
                return True, straight_values, []
        return False, [], []

    def check_three_of_a_kind(self):
        for rank, count in self.rank_counts.items():
            if count == 3:
                kickers = sorted([card for card in self.ranks if card != rank], reverse=True)[:2]
                return True, [rank]*3, kickers
        return False, [], []

    def check_two_pair(self):
        pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]
        if len(pairs) >= 2:
            pairs = sorted(pairs, reverse=True)[:2]
            kicker = sorted([card for card in self.ranks if card not in pairs], reverse=True)[0]
            kickers = [kicker]
            return True, pairs*2, kickers
        return False, [], []

    def check_one_pair(self):
        pairs = [rank for rank, count in self.rank_counts.items() if count >= 2]
        if pairs:
            pair = max(pairs)
            kickers = sorted([card for card in self.ranks if card != pair], reverse=True)[:3]
            return True, [pair]*2, kickers
        return False, [], []


class PokerPlayer(Player):
    class PlayerAction(Enum):
        BET = "bet"
        RAISE = "raise"
        CALL = "call"
        FOLD = "fold"
        ALL_IN = "all-in"
        CHECK = "check"
        NONE = None

    money: int
    cards: List['Card']
    options: List['PlayerAction']
    folded: bool

    def __init__(self, name="Player", starting_money=10000):
        super().__init__(name)
        self.money = starting_money
        self.cards = []
        self.options = []
        self.folded = False
    
    def __str__(self):
        # Create a function that prints the name of the poker player
        return self.name

    @property
    def player_state(self):
        player_state = {
            "name": self.name,
            "player_money": self.money,
            "player_cards": self.cards,
            "player_options": self.options,
            "has_folded": self.folded
        }

        return player_state

    def get_player_action(self, hand_state):
        game_interface = hand_state["game_interface"]
        community_cards = hand_state['community_cards']
        current_bet = hand_state['current_bet']
        current_pot = hand_state['current_pot']
        cost_to_call = current_pot.get_player_cost_to_call(self)
        total_to_pot = current_pot.get_player_pot_amount(self)

        game_interface.display_text(display_hole_cards(self.cards))
        text_lines = [
            f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}",
            f"Community cards: {community_cards}",
            f"Current bet: {current_bet}",
            f"Current pot: {current_pot.total}",
            f"Cost to call: {cost_to_call}",
            f"Total to pot: {total_to_pot}"
        ]

        text = "\n".join(text_lines)

        game_interface.display_text(text)
        action = game_interface.request_action(self.options, "Enter action: \n")

        add_to_pot = 0
        if action is None:
            if "check" in self.options:
                action = "check"
            elif "call" in self.options:
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
            add_to_pot = self.money
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
        poker_action = PokerAction(self, action, add_to_pot, hand_state)
        return poker_action

    def get_for_pot(self, amount):
        self.money -= amount

    def set_for_new_hand(self):
        self.cards = []
        self.folded = False

    def get_index(self, players):
        return players.index(self)


class AIPokerPlayer(PokerPlayer):
    name: str
    money: int
    confidence: str
    attitude: str
    assistant: OpenAILLMAssistant

    def __init__(self, name="AI Player", starting_money=10000, ai_temp=.9):
        # Options for models ["gpt-3.5-turbo", "gpt-3.5-turbo-16k", "gpt-4","gpt-4-32k"]
        super().__init__(name, starting_money=starting_money)
        self.confidence = "Unsure"
        self.attitude = "Distracted"
        self.assistant = OpenAILLMAssistant(ai_temp=ai_temp,
                                            system_message=self.persona_prompt)

    @property
    def player_state(self):
        ai_player_state = super().player_state
        ai_player_state["confidence"] = self.confidence
        ai_player_state["attitude"] = self.attitude
        return ai_player_state

    def set_for_new_hand(self):
        super().set_for_new_hand()
        self.assistant.memory = []  # TODO: change this to use a reset_memory call in the assistant class

    def initialize_attribute(self, attribute, constraints="Use less than 50 words.", opponents="other players", mood=1):
        formatted_string = \
            f"""You are {self.name}'s inner voice. Describe their {attribute} as they enter a poker game against 
{opponents}. This description is being used for a simulation of a poker game and we want to have a variety of 
personalities and emotions for the players. Your phrasing must be as if you are their inner voice and you are speaking 
to them. {constraints}

Provide 3 responses with different levels of {attribute} (low, regular, high) and put them in JSON format like: 
{{{{\"responses\" =  [\"string\", \"string\", \"string\"]}}}}"""

        response = self.assistant.get_json_response(messages=[{"role": "user", "content": formatted_string}])

        content = json.loads(response.choices[0].message.content)
        selection = content["responses"]
        random.shuffle(selection)     # used to randomly select the response mood
        return selection[mood]

    @property
    def persona_prompt(self):
        name = self.name
        confidence = self.confidence
        attitude = self.attitude
        player_money = self.money

        poker_prompt = (
            f"""
        Persona: {name}
        Attitude: {attitude}
        Confidence: {confidence}
        Starting money: {player_money}

        You are taking on the role of {name} playing a round of Texas Hold em with a group of celebrities.
        All of your actions should be taken with your persona, attitude and confidence in mind.

        Strategy:
        Begin by examining your cards and any cards that may be on the table. Evaluate your hand and decide how
        you want to play. You can bluff, be strategic, or any other way you think would be appropriate and fun to
        approach the game.

        Direction:
        Feel free to express yourself verbally and physically.
            * Verbal responses should use "" like this: "words you say"
            * Actions you take should use ** like this: *things i'm doing*
        Don't over do this though, you are playing poker and you don't want to give anything away that would hurt your
        chances of winning. You should respond with a JSON containing your action, bet (if applicable), any comments
        or things you want to say to the table, any physical movements you make at the table, and your inner monologue

        When asked for your action, you must always respond in JSON format based on the example below

        Response template:
        {{{{
            "hand_strategy": <short analysis of current situation based on your persona and the cards>,
            "action": <enter the action you're going to take here, select from the options provided>,
            "amount": <enter the dollar amount to bet here>,
            "comment": <enter what you want to say here, this will be heard by your opponents. try to use this to your advantage>,
            "inner_monologue": <enter your internal thoughts here, these won't be shared with the others at the table>,
            "persona_response": <based on your persona, attitude, and confidence, provide a unique response to the situation. Use dialect, slang, etc. appropriate to your persona>,
            "physical": <enter a list of strings with the physical actions you take in the order you take them>
            "new_confidence": <a single word indicating how confident you feel about your chances of winning the game>
            "new_attitude": <a single word indicating your attitude in the moment, it can be the same as before or change>
            "bluff_likelihood": <int representing % likelihood you will bluff>
        }}}}

        Sample response for an Eyeore persona
        {{{{
            "hand_analysis": "With a 2D and 3C I don't feel confident in playing, my odds are 2%",
            "action": "check",
            "amount": 0,
            "comment": "I check",
            "inner_monologue": "I could really use a better hand, my cards have been awful",
            "persona_response": "Oh bother, just my luck. Another miserable hand, I suppose. It seems I'm destined to
                                   lose at this game as well. Sigh... Why even bother? No surprises here, I'm afraid.
                                   Just another gloomy day in the Hundred Acre Wood.",
            "physical": [ "*looks at feet*",
                          "*lets out a big sigh*",
                          "*slouches shoulders*"
                        ],
            "new_confidence": "Abysmal",
            "new_attitude": "Gloomy",
            "bluff_likelihood": 30
        }}}}

        Remember {name}, you're feeling {attitude} and {confidence}.
        Stay in character and keep your responses in JSON format.

        """)

        return poker_prompt

    def get_player_action(self, hand_state):
        game_interface = hand_state["game_interface"]
        community_cards = hand_state["community_cards"]
        current_bet = hand_state["current_bet"]
        current_pot = hand_state["current_pot"]
        cost_to_call = current_pot.get_player_cost_to_call(self)
        total_to_pot = current_pot.get_player_pot_amount(self)

        text_lines = [
            f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}",
            f"Community cards: {community_cards}",
            f"Current bet: {current_bet}",
            f"Current pot: {current_pot.total}",
            f"Cost to call: {cost_to_call}",
            f"Total to pot: {total_to_pot}"
        ]

        text = "\n".join(text_lines)

        game_interface.display_text(text)

        '''
        if len(community_cards) < 3:
            hand_rank = self.evaluate_hole_cards()
        else:
            hand_rank = HandEvaluator(self.cards + community_cards).evaluate_hand()["hand_rank"]

        pot_odds = current_pot / current_bet if current_bet else 1
        money_left = self.money / current_bet if current_bet else 1

        bet = 0

        # Adjust these thresholds as needed
        if current_bet == 0:
            if hand_rank < 5 or pot_odds > 3 or money_left > 3:
                action = "raise"
                bet = self.money // 10  # Bet 10% of AI's money
            else:
                action = "check"
        elif hand_rank > 5 and pot_odds < 2 and money_left < 2:
            action = "fold"
        elif hand_rank < 5 or pot_odds > 3 or money_left > 3:
            action = "raise"
            bet = self.money // 10  # Bet 10% of AI's money
        else:
            action = "call"
            bet = current_bet

        self.chat_message = f"{self.name} chooses to {action} by {bet}."
        return action, bet'''

        response_json = self.get_player_response(hand_state)

        game_interface.display_expander(label="Player Insights", body=response_json)

        action = response_json["action"]
        add_to_pot = response_json["amount"]
        chat_message = response_json["comment"]
        self.attitude = response_json["new_attitude"]
        self.confidence = response_json["new_confidence"]

        game_interface.display_text(f"{self.name}: '{chat_message}'")
        game_interface.display_text(f"{self.name} chooses to {action} by {add_to_pot}.")

        poker_action = PokerAction(self, action, add_to_pot, hand_state, response_json)
        return poker_action

    # def evaluate_hole_cards(self):
    #     # Use Monte Carlo method to approximate hand strength
    #     hand_ranks = []
    #     for _ in range(100):  # Adjust this number as needed
    #         simulated_community = Deck().deal(5)
    #         simulated_hand_rank = HandEvaluator(self.cards + simulated_community).evaluate_hand()["hand_rank"]
    #         hand_ranks.append(simulated_hand_rank)
    #     hand_rank = sum(hand_ranks) / len(hand_ranks)
    #     return hand_rank

    def get_player_response(self, hand_state) -> Dict[str, str]:
        persona = self.name
        confidence = self.confidence
        attitude = self.attitude
        opponents = hand_state["players"]
        number_of_opponents = len(opponents) - 1
        # position = hand_state["positions"][self]
        player_money = self.money
        current_situation = hand_state["current_situation"]
        hole_cards = self.cards
        community_cards = hand_state["community_cards"]
        current_pot = hand_state["current_pot"]
        current_bet = current_pot.current_bet
        cost_to_call = current_pot.get_player_cost_to_call(self)
        player_options = self.options
        opponent_positions = hand_state["opponent_positions"]
        current_round = hand_state["current_round"]

        hand_update_message = (
            f"""Persona: {persona}
Attitude: {attitude}
Confidence: {confidence}
Opponents: {opponent_positions}
Game Round: {current_round}
Community Cards: {community_cards}

You are {persona} playing a round of Texas Hold 'em with {number_of_opponents} other people.
You have ${player_money} in chips remaining. {current_situation}.
You have {hole_cards} in your hand. The current pot is ${current_pot.total}, the current bet is ${current_bet} and
it is {cost_to_call} to you.
Your options are: {player_options}

Remember {persona}, you're feeling {attitude} and {confidence}. And you can not bet more than you have, ${player_money}.

What is your move?""")

        player_response = json.loads(self.assistant.chat(hand_update_message, json_format=True))

        return player_response


class PokerSettings:
    all_in_allowed: bool
    starting_small_blind: int
    player_starting_money: int
    ai_player_starting_money: int or None

    def __init__(self,
                 all_in_allowed: bool = True,
                 starting_small_blind: int = 50,
                 player_starting_money: int = 10000,
                 ai_player_starting_money: int = None
                 ):
        self.all_in_allowed = all_in_allowed
        self.starting_small_blind = starting_small_blind
        self.player_starting_money = player_starting_money

        if ai_player_starting_money is None:
            self.ai_player_starting_money = self.player_starting_money
        else:
            self.ai_player_starting_money = ai_player_starting_money


class PokerAction:
    player: PokerPlayer
    player_action: PokerPlayer.PlayerAction
    amount: Optional[int]
    hand_state: Optional[dict]
    action_detail: Optional[str]

    def __init__(self,
                 player: PokerPlayer,
                 action: str,
                 amount: int or None = None,
                 hand_state: dict or None = None,
                 action_detail: str or None = None):
        self.player = player
        self.player_action = PokerPlayer.PlayerAction(action)
        self.amount = amount
        self.hand_state = hand_state.copy()
        self.action_detail = action_detail

    def __str__(self):
        return (f"PokerAction("
                f" player={self.player}, "
                f" action={self.player_action}, "
                f" amount={self.amount}, "
                f" hand_state={self.hand_state}, "
                f" detail={self.action_detail}"
                f")")


class PokerHandPot:
    player_pot_amounts: Dict['PokerPlayer', int]
    pot_winner: PokerPlayer or None

    @property
    def total(self) -> int:
        return sum(self.player_pot_amounts.values())

    @property
    def current_bet(self) -> int:
        return max(self.player_pot_amounts.values())

    def __init__(self, poker_players: List[PokerPlayer]):
        self.player_pot_amounts = {}
        self.pot_winner = None

        for player in poker_players:
            self.player_pot_amounts[player] = 0

    def get_player_pot_amount(self, player: PokerPlayer) -> int:
        return self.player_pot_amounts[player]

    def get_player_cost_to_call(self, player: PokerPlayer) -> int:
        player_contributed = self.get_player_pot_amount(player)
        return self.current_bet - player_contributed

    def add_to_pot(self, player: PokerPlayer, amount: int) -> None:
        player.get_for_pot(amount)
        self.player_pot_amounts[player] += amount

    def resolve_pot(self, pot_winner: PokerPlayer) -> None:
        pot_winner.money += self.total
        self.pot_winner = pot_winner


def print_queue_status(player_queue: List['PokerPlayer']):
    for index, player in enumerate(player_queue):
        print(f"{index}: {player.name} - {player.folded}")


class PokerHand:
    class PokerHandPhase(Enum):
        INITIALIZING = "initializing"
        PRE_FLOP = "pre-flop"
        FLOP = "flop"
        TURN = "turn"
        RIVER = "river"

    interface: Interface
    players: List['PokerPlayer']
    starting_players: List['PokerPlayer']
    remaining_players: List['PokerPlayer']
    deck: Deck
    table_positions: Dict[str, PokerPlayer]
    dealer: PokerPlayer
    small_blind_player: PokerPlayer
    big_blind_player: PokerPlayer
    under_the_gun: PokerPlayer
    poker_actions: List['PokerAction']
    community_cards: List['Card']
    current_round: PokerHandPhase
    pots: List['PokerHandPot']
    small_blind: int
    min_bet: int

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    @property
    def hand_state(self):
        hand_state = {
            "game_interface": self.interface,
            "community_cards": copy.deepcopy(self.community_cards),
            "current_bet": self.pots[0].current_bet,
            "current_pot": self.pots[0],
            "players": self.players,
            "opponent_positions": self.get_table_positions(),
            "current_situation": f"The {self.current_round.value} cards have just been dealt",
            "current_round": self.current_round,
        }
        return hand_state

    def __init__(self,
                 interface: Interface,
                 players: List['PokerPlayer'],
                 dealer: PokerPlayer,
                 deck: Deck):
        self.interface = interface
        self.players = players
        self.starting_players = list(players)
        self.remaining_players = list(players)
        self.dealer = dealer
        self.deck = deck
        self.poker_actions = []
        self.community_cards = []
        self.current_round = PokerHand.PokerHandPhase.INITIALIZING
        self.pots = [PokerHandPot(self.players)]
        self.small_blind = PokerSettings().starting_small_blind
        self.small_blind_player = self.players[(self.dealer_position + 1) % len(self.players)]
        self.big_blind_player = self.players[(self.dealer_position + 2) % len(self.players)]
        self.under_the_gun = self.players[(self.dealer_position + 3) % len(self.players)]

    def get_opponent_positions(self, requesting_player=None) -> List[str]:
        opponent_positions = []
        for player in self.players:
            if player != requesting_player:
                position = f'{player.name} has ${player.money}'
                position += ' and they have folded' if player.folded else ''
                position += '.\n'
                opponent_positions.append(position)
        return opponent_positions

    def set_current_round(self, current_round: PokerHandPhase):
        self.current_round = current_round

    def set_remaining_players(self):
        self.remaining_players = [player for player in self.players if not player.folded]

    def player_bet_this_hand(self, player: PokerPlayer) -> int:
        pot_contributions = []
        for pot in self.pots:
            pot_contributions.append(pot.get_player_pot_amount(player))
        return sum(pot_contributions)

    def post_blinds(self):
        small_blind = self.small_blind
        big_blind = small_blind * 2
        self.pots[0].add_to_pot(self.small_blind_player, small_blind)
        self.pots[0].add_to_pot(self.big_blind_player, big_blind)

    def deal_hole_cards(self):
        for player in self.players:
            player.cards = self.deck.deal(2)

    def determine_start_player(self):
        start_player = None
        if self.current_round == PokerHand.PokerHandPhase.PRE_FLOP:
            # Player after big blind starts
            start_player = self.players[(self.dealer_position + 3) % len(self.players)]
        else:
            # Find the first player after the dealer who hasn't folded
            for j in range(1, len(self.players) + 1):
                index = (self.dealer_position + j) % len(self.players)
                if not self.players[index].folded:
                    start_player = self.players[index]
                    break
        return start_player

    def process_pot_update(self, player: PokerPlayer, amount_to_add: int):
        self.pots[0].add_to_pot(player, amount_to_add)

    def handle_bet_or_raise(self, player: PokerPlayer, add_to_pot: int, next_round_queue: List['PokerPlayer']):
        self.process_pot_update(player, add_to_pot)
        return self.betting_round(next_round_queue, is_initial_round=False)

    def handle_all_in(self, player: PokerPlayer, add_to_pot: int, next_round_queue: List['PokerPlayer']):
        self.process_pot_update(player, add_to_pot)
        raising = add_to_pot > self.pots[0].current_bet
        if raising:
            return self.betting_round(next_round_queue, is_initial_round=False)

    def handle_call(self, player: PokerPlayer, add_to_pot: int):
        self.process_pot_update(player, add_to_pot)

    def handle_fold(self, player: PokerPlayer):
        player.folded = True
        self.set_remaining_players()

    # TODO: change this to return the options as a PlayerAction enum
    def set_player_options(self, poker_player: PokerPlayer, settings: PokerSettings):
        # How much is it to call the bet for the player?
        player_cost_to_call = self.pots[0].get_player_cost_to_call(poker_player)
        # Does the player have enough to call
        player_has_enough_to_call = poker_player.money > player_cost_to_call
        # Is the current player also the big_blind TODO: add "and have they played this hand yet"
        current_player_is_big_blind = poker_player is self.big_blind_player

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if (current_player_is_big_blind
                and self.current_round == PokerHand.PokerHandPhase.PRE_FLOP
                and self.pots[0].current_bet == self.small_blind * 2):
            player_options = ['check', 'raise', 'all-in']
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in']
            if player_cost_to_call == 0:
                player_options.remove('fold')
            if player_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or player_cost_to_call == 0:
                player_options.remove('call')
            if self.pots[0].current_bet > 0 or player_cost_to_call > 0:
                player_options.remove('bet')
            if poker_player.money - self.pots[0].current_bet <= 0 or 'bet' in player_options:
                player_options.remove('raise')
            if not settings.all_in_allowed or poker_player.money == 0:
                player_options.remove('all-in')

        poker_player.options = player_options.copy()

    def get_next_round_queue(self, round_queue, betting_player: Optional['PokerPlayer']):
        next_round_queue = round_queue.copy()
        if betting_player:
            index = round_queue.index(betting_player) + 1
        else:
            index = 1
        shift_list_left(next_round_queue, index)
        return next_round_queue

    def betting_round(self, player_queue: List['PokerPlayer'], is_initial_round: bool = True):
        active_players = self.initialize_active_players(player_queue, is_initial_round)

        if len(self.remaining_players) <= 1:
            raise ValueError("No remaining players left in the hand")

        for player in active_players:
            if player.folded:
                continue

            print_queue_status(player_queue)
            self.set_player_options(player, PokerSettings())

            poker_action = player.get_player_action(self.hand_state)
            self.poker_actions.append(poker_action)

            if self.process_player_action(player, poker_action):
                return

    def initialize_active_players(self, player_queue: List['PokerPlayer'], is_initial_round: bool) -> List[
        'PokerPlayer']:
        return player_queue.copy() if is_initial_round else player_queue[:-1]

    def process_player_action(self, player: 'PokerPlayer', poker_action: 'PokerAction') -> bool:
        player_action = poker_action.player_action
        amount = poker_action.amount

        if player_action in {PokerPlayer.PlayerAction.BET, PokerPlayer.PlayerAction.RAISE}:
            self.handle_bet_or_raise(player, amount, self.get_next_round_queue(self.remaining_players, player))
            return True
        elif player_action == PokerPlayer.PlayerAction.ALL_IN:
            self.handle_all_in(player, amount, self.get_next_round_queue(self.remaining_players, player))
            return True
        elif player_action == PokerPlayer.PlayerAction.CALL:
            self.handle_call(player, amount)
        elif player_action == PokerPlayer.PlayerAction.FOLD:
            self.handle_fold(player)
        elif player_action == PokerPlayer.PlayerAction.CHECK:
            return False
        else:
            raise ValueError("Invalid action selected: " + str(player_action))
        return False

    def reveal_cards(self, num_cards: int, round_name: PokerHandPhase):
        """
        Reveal the cards.

        :param num_cards: Number of cards to reveal
        :param round_name: Name of the current round
        :return: string with text to output and revealed cards
        """
        self.deck.discard(1)
        new_cards = self.deck.deal(num_cards)
        self.community_cards += new_cards
        self.current_round = round_name
        output_text = f"""
                    ---***{round_name}***---
            {self.community_cards}
"""
        output_text += render_cards(self.community_cards)

        return output_text, new_cards

    def reveal_flop(self):
        output_text, new_cards = self.reveal_cards(3, PokerHand.PokerHandPhase.FLOP)
        self.interface.display_text(output_text)

    def reveal_turn(self):
        output_text, new_cards = self.reveal_cards(1, PokerHand.PokerHandPhase.TURN)
        self.interface.display_text(output_text)

    def reveal_river(self):
        output_text, new_cards = self.reveal_cards(1, PokerHand.PokerHandPhase.RIVER)
        self.interface.display_text(output_text)

    def determine_winner(self):
        hands = []

        for player in self.players:
            if not player.folded:
                hands.append((player, HandEvaluator(player.cards + self.community_cards).evaluate_hand()))

        print("Before sorting:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        hands.sort(key=lambda x: sorted(x[1]["kicker_values"]), reverse=True)

        print("After sorting by kicker values:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        hands.sort(key=lambda x: sorted(x[1]["hand_values"]), reverse=True)

        print("After sorting by hand values:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        hands.sort(key=lambda x: x[1]["hand_rank"])

        print("After sorting by hand rank:")
        for player, hand_info in hands:
            print(f"{player.name}'s hand: {hand_info}")

        winner = hands[0][0]
        return winner

    def rotate_dealer(self):
        current_dealer_starting_player_index = self.starting_players.index(self.dealer)
        new_dealer_starting_player_index = (current_dealer_starting_player_index + 1) % len(self.starting_players)
        self.dealer = self.starting_players[new_dealer_starting_player_index]
        if self.dealer.money <= 0:
            self.rotate_dealer()

    def end_hand(self):
        # Evaluate and announce the winner
        winning_player = self.determine_winner()
        self.interface.display_text(f"The winner is {winning_player.name}! They win the pot of {self.pots[0].total}")

        # Reset game for next round
        self.pots[0].resolve_pot(winning_player)
        self.rotate_dealer()

        # Check if the game should continue
        self.players = [player for player in self.starting_players if player.money > 0]
        if len(self.players) == 1:
            self.interface.display_text(f"{self.players[0].name} is the last player remaining and wins the game!")
            return
        elif len(self.players) == 0:
            self.interface.display_text("You... you all lost. Somehow you all have no money.")
            return

        # Reset players
        for player in self.players:
            self.deck.return_cards_to_deck(player.cards)
            player.folded = False

        self.deck.reset()

    def play_hand(self):
        round_queue = self.setup_hand()

        self.betting_round(round_queue)

        self.reveal_flop()
        start_player = self.determine_start_player()
        index = self.players.index(start_player)
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player
        self.betting_round(round_queue)

        self.reveal_turn()
        self.betting_round(round_queue)

        self.reveal_river()
        self.betting_round(round_queue)

        self.end_hand()

        return self.remaining_players, self.dealer

    def setup_hand(self):
        self.set_remaining_players()
        self.set_current_round(PokerHand.PokerHandPhase.PRE_FLOP)
        self.post_blinds()
        self.interface.display_text(f"{self.dealer.name}'s deal.\n")
        self.interface.display_text(
            f"Small blind: {self.small_blind_player.name}\n Big blind: {self.big_blind_player.name}\n")
        self.deal_hole_cards()

        start_player = self.determine_start_player()

        index = self.players.index(start_player)  # Set index at the start_player
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player

        return round_queue

    def get_table_positions(self) -> Dict[str, PokerPlayer]:
        table_positions = {"dealer": self.dealer,
                           "small_blind_player": self.players[(self.dealer_position + 1) % len(self.players)],
                           "big_blind_player": self.players[(self.dealer_position + 2) % len(self.players)],
                           "under_the_gun": self.players[(self.dealer_position + 3) % len(self.players)]
                           }
        return table_positions


class PokerGame(Game):
    settings: PokerSettings
    players: List['PokerPlayer']
    starting_players: List['PokerPlayer']
    remaining_players: List['PokerPlayer']
    deck: Deck
    hands: List['PokerHand']
    assistant: OpenAILLMAssistant

    def __init__(self, players: [PokerPlayer], interface: Interface):
        super().__init__(players, interface)
        self.settings = PokerSettings()
        self.starting_players = list(self.players)
        self.remaining_players = list(self.starting_players)
        self.deck = Deck()
        self.hands = []
        self.assistant = OpenAILLMAssistant()

    def play_game(self):
        poker_hand = PokerHand(interface=self.interface,
                               players=self.players,
                               dealer=self.players[random.randint(0, len(self.players) - 1)],
                               deck=self.deck)
        while len(self.remaining_players) > 1:
            self.hands.append(poker_hand)
            self.remaining_players, dealer = poker_hand.play_hand()
            play_again = self.interface.request_action(
                ["yes", "no"],
                "Would you like to play another hand? ",
                0)
            if play_again != "yes":
                break
            else:
                poker_hand = PokerHand(interface=self.interface,
                                       players=self.remaining_players,
                                       dealer=dealer,
                                       deck=self.deck)

        self.display_text("Game over!")


def get_players(test=False, num_players=2):
    definites = [
        PokerPlayer("Jeff")
    ]

    if test:
        basic_test_players = [
            PokerPlayer("Player1"),
            PokerPlayer("Player2"),
            PokerPlayer("Player3"),
            PokerPlayer("Player4")
        ]

        players = basic_test_players

    else:
        celebrities = [
            AIPokerPlayer("Ace Ventura", ai_temp=.9),
            AIPokerPlayer("Khloe and Kim Khardashian"),
            AIPokerPlayer("Fred Durst"),
            AIPokerPlayer("Tom Cruise"),
            AIPokerPlayer("James Bond"),
            AIPokerPlayer("Jon Stewart"),
            AIPokerPlayer("Jim Cramer", ai_temp=.7),
            AIPokerPlayer("Marjorie Taylor Greene", ai_temp=.7),
            AIPokerPlayer("Lizzo"),
            AIPokerPlayer("Bill Clinton"),
            AIPokerPlayer("Barack Obama"),
            AIPokerPlayer("Jesus Christ"),
            AIPokerPlayer("Triumph the Insult Dog", ai_temp=.7),
            AIPokerPlayer("Donald Trump", ai_temp=.7),
            AIPokerPlayer("Batman"),
            AIPokerPlayer("Deadpool"),
            AIPokerPlayer("Lance Armstrong"),
            AIPokerPlayer("A Mime", ai_temp=.8),
            AIPokerPlayer("Jay Gatsby"),
            AIPokerPlayer("Whoopi Goldberg"),
            AIPokerPlayer("Dave Chappelle"),
            AIPokerPlayer("Chris Rock"),
            AIPokerPlayer("Sarah Silverman"),
            AIPokerPlayer("Kathy Griffin"),
            AIPokerPlayer("Dr. Seuss", ai_temp=.7),
            AIPokerPlayer("Dr. Oz"),
            AIPokerPlayer("A guy who tells too many dad jokes")
        ]

        random.shuffle(celebrities)
        randos = celebrities[0:(num_players - len(definites))]
        players = definites + randos
        for player in players:
            if isinstance(player, AIPokerPlayer):
                i = random.randint(0, 2)
                player.confidence = player.initialize_attribute("confidence",
                                                                "Use less than 20 words",
                                                                "other players",
                                                                mood=i)
                player.attitude = player.initialize_attribute("attitude",
                                                              "Use less than 20 words",
                                                              "other players",
                                                              mood=i)
    return players


def shift_list_left(my_list: list, count: int = 1):
    """
    :param my_list: list that you want to manipulate
    :param count: how many shifts you want to make
    """
    for i in range(1, count + 1):
        # Pop from the beginning of the list and append to the end
        my_list.append(my_list.pop(0))


def shift_list_right(my_list: list, count: int = 1):
    """
    :param my_list: list that you want to manipulate
    :param count: how many shifts you want to make
    """
    for i in range(1, count + 1):
        # Pop from the end of the list and insert it at the beginning
        my_list.insert(0, my_list.pop())


def display_hole_cards(cards: [Card, Card]):
    # Define the ASCII art templates for each rank and suit combination
    card_template = \
        '''
.---.---------.
|{}  |{}        |
|  {}|  {}      |
|   |         |
|   |         |
|   |       {} |
|   |        {}|
`---`---------'
'''

    sorted_cards = sorted(cards, key=lambda card: card.value)
    card_1 = sorted_cards[0]
    card_2 = sorted_cards[1]

    # Generate and print each card
    hole_card_art = render_two_cards(card_1, card_2)
    return hole_card_art


def main(test=False, num_players=3):
    players = get_players(test=test, num_players=num_players)
    poker_game = PokerGame(players, ConsoleInterface())
    poker_game.play_game()


if __name__ == "__main__":
    main()
