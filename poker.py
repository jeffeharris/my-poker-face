import json
from collections import Counter
import logging
import random
from enum import Enum
from typing import List

from cards import Card, Deck, render_cards
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

    money: int
    cards: List['Card']
    options: List['PlayerAction']
    folded: bool
    total_bet_this_hand: int

    def __init__(self, name="Player", starting_money=10000):
        super().__init__(name)
        self.money = starting_money
        self.cards = []
        self.options = []
        self.folded = False
        self.total_bet_this_hand = 0        # TODO: move this to a Hand class or something that doesn't exist outside
        # self.chat_message = ""

    @property
    def player_state(self):
        player_state = {
            "name": self.name,
            "player_money": self.money,
            "player_cards": self.cards,
            "player_options": self.options,
            "has_folded": self.folded,
            "total_bet_this_hand": self.total_bet_this_hand
            # could break this out into "game_state" or "hand_state" vs. "player_state"
            # "number_of_opponents": 2,
            # "opponent_positions": ["Jeff has $1000 to your left", "Hal has $900 to your right"],
            # "position": "small blind",
            # "current_situation": "The hole cards have just been dealt",
            # "current_pot": 30,
        }

        return player_state

    def action(self, game_state):
        game_interface = game_state["game_interface"]
        player_options = game_state["player_options"]
        community_cards = game_state['community_cards']
        current_bet = game_state['current_bet']
        current_pot = game_state['current_pot']
        cost_to_call = game_state['cost_to_call']

        # TODO: update display_hole_cards to use the interface
        game_interface.display_text(display_hole_cards(self.cards))
        text_lines = [
            f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}",
            f"Community cards: {community_cards}",
            f"Current bet: {current_bet}",
            f"Current pot: {current_pot}",
            f"Cost to call: {cost_to_call}",
            f"Total to pot: {self.total_bet_this_hand}"
        ]

        text = "\n".join(text_lines)

        game_interface.display_text(text)
        action = game_interface.request_action(self.options, "Enter action: \n")

        add_to_pot = 0
        if action in ["bet", "b", "be"]:
            add_to_pot = int(input("Enter amount: "))
            action = "bet"
        elif action in ["raise", "r", "ra", "rai", "rais"]:
            raise_amount = int(input(f"Calling {cost_to_call}.\nEnter amount to raise: "))
            # TODO: this causes an issue for the ai bet amount, it isn't aware of how i'm doing the math, need to update
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
        return action, add_to_pot

    # TODO: decide to remove this function or keep it
    # def speak(self):
    #     return self.chat_message

    def get_for_pot(self, amount):
        self.money -= amount
        self.total_bet_this_hand += amount

    # TODO: add reset player to reset a player for a new round

    def set_for_new_hand(self):
        self.cards = []
        self.folded = False
        self.total_bet_this_hand = 0

    def get_index(self, players):
        return players.index(self)


class AIPokerPlayer(PokerPlayer):
    def __init__(self, name="AI Player", starting_money=10000, ai_temp=.9):
        # Options for models ["gpt-3.5-turbo", "gpt-3.5-turbo-16k", "gpt-4","gpt-4-32k"]
        super().__init__(name, starting_money=starting_money)
        self.confidence = "Unsure"
        self.attitude = "Distracted"
        self.assistant = OpenAILLMAssistant(ai_temp=ai_temp, system_message=self.persona_prompt)

    @property
    def player_state(self):
        ai_player_state = super().player_state
        ai_player_state["confidence"] = self.confidence
        ai_player_state["attitude"] = self.attitude
        return ai_player_state

    def set_for_new_hand(self):
        super().set_for_new_hand()
        self.assistant.memory = []  #TODO: change this to use a reset_memory call in the assistant class

    def initialize_attribute(self, attribute, constraints="Use less than 50 words", opponents="other players", mood=1):
        formatted_string = \
            f"""You are {self.name}'s inner voice. Describe their {attribute} as they enter a poker game against 
{opponents}. This description is being used for a simulation of a poker game and we want to have a variety of 
personalities and emotions for the players. Your phrasing must be as if you are their inner voice and you are speaking 
to them. {constraints}

Provide 3 responses with different levels of {attribute} (low, regular, high) and put them in JSON format like: 
{{{{\"responses\" =  [\"string\", \"string\", \"string\"]}}}}"""

        response = self.assistant.get_response(messages=[{"role": "user", "content": formatted_string}])

        content = json.loads(response.choices[0].message.content)
        # content = response.choices[0].message.content
        selection = content["responses"]
        random.shuffle(selection)     # used to randomly select the response mood
        # print(f"{selection[mood]}\n")
        return selection[mood]
        # print(content)
        # return content

    @property
    def persona_prompt(self):
        name = self.name
        confidence = self.confidence
        attitude = self.attitude
        player_money = self.money

        sample_string = (
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

        poker_prompt = sample_string
        # poker_prompt = ChatPromptTemplate.from_messages([
        #     SystemMessagePromptTemplate.from_template(template=sample_string),
        #     MessagesPlaceholder(variable_name="history"),
        #     HumanMessagePromptTemplate.from_template("{input}")
        # ])
        return poker_prompt

    def action(self, game_state):
        community_cards = game_state["community_cards"]
        cost_to_call = game_state["cost_to_call"]
        current_bet = game_state["current_bet"]
        current_pot = game_state["current_pot"]

        text = (f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}\n",
              f"Community cards: {community_cards}\n",
              f"Cost to call: {cost_to_call}\n",
              f"Current bet: {current_bet}\n",
              f"Current pot: {current_pot}\n")

        game_state["game_interface"].display_text(text)

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

        response = self.retrieve_response(game_state)
        try:
            response_json = json.loads(response)
        except:
            print("Error response: \n" + response)
            response = self.assistant.chat("Please correct your response, it wasn't valid JSON.")
            response_json = json.loads(response)

        # print(json.dumps(response_json, indent=4))

        action = response_json["action"]
        bet = response_json["amount"]
        self.chat_message = response_json["comment"]
        self.attitude = response_json["new_attitude"]
        self.confidence = response_json["new_confidence"]

        print(f"{self.name} chooses to {action} by {bet}.")

        return action, bet

    # TODO: move this to the poker.py subclass AIPokerPlayer
    # def evaluate_hole_cards(self):
    #     # Use Monte Carlo method to approximate hand strength
    #     hand_ranks = []
    #     for _ in range(100):  # Adjust this number as needed
    #         simulated_community = Deck().draw(5)
    #         simulated_hand_rank = HandEvaluator(self.cards + simulated_community).evaluate_hand()["hand_rank"]
    #         hand_ranks.append(simulated_hand_rank)
    #     hand_rank = sum(hand_ranks) / len(hand_ranks)
    #     return hand_rank

    def retrieve_response(self, players_game_state):
        persona = self.name
        confidence = self.confidence
        attitude = self.attitude
        opponents = players_game_state["players"]
        number_of_opponents = len(opponents) - 1
        position = "small blind"
        player_money = self.money
        current_situation = players_game_state["current_situation"]
        hole_cards = self.cards
        community_cards = players_game_state["community_cards"]
        current_bet = players_game_state["current_bet"]
        current_pot = players_game_state["current_pot"]
        player_options = players_game_state["player_options"]
        opponent_positions = players_game_state["opponent_positions"]
        current_round = players_game_state["current_round"]

        sample_string = (
            f"""Persona: {persona}
Attitude: {attitude}
Confidence: {confidence}
Opponents: {opponent_positions}
Game Round: {current_round}
Community Cards: {community_cards}

You are {persona} playing a round of Texas Hold 'em with {number_of_opponents} other people.
You are {position} and have ${player_money} in chips remaining. {current_situation},
you have {hole_cards} in your hand. The current pot is ${current_pot}, the current bet is ${current_bet} to you.
Your options are: {player_options}

Remember {persona}, you're feeling {attitude} and {confidence}. And you can not bet more than you have, ${player_money}.

What is your move?""")
        # it's ${amount_to_call} to you to call and cover the blind and $20 to bet. Would you like to call or fold?
        # print(sample_string)

        player_response = self.assistant.chat(sample_string)

        print(player_response)

        return player_response


class PokerGame(Game):
    players: List['PokerPlayer']
    starting_players: List['PokerPlayer']
    remaining_players: List['PokerPlayer']

    def __init__(self, players: [PokerPlayer], interface: Interface):
        super().__init__(players, interface)
        self.starting_players = list(self.players)
        self.remaining_players = list(self.starting_players)
        self.discard_pile = []  # list for the discarded cards to be placed in
        self.all_in_allowed = True
        self.last_to_act = None
        self.betting_round_state = None
        self.last_action = None  # represents the last action taken in a betting round
        self.deck = Deck()
        self.community_cards = []
        self.current_bet = 0
        self.pot = 0
        self.small_blind = 50
        self.current_round = "initializing"
        self.dealer = PokerPlayer("dealer")
        self.small_blind_player = PokerPlayer("small_blind")
        self.big_blind_player = PokerPlayer("big_blind")
        self.under_the_gun = PokerPlayer("under_the_gun")
        self.current_player = None
        self.player_options = []
        self.min_bet = self.small_blind * 2
        self.max_bet = None
        self.pot_limit = None
        # self.chat = ChatOpenAI(temperature=.3, model="gpt-3.5-turbo-16k")
        # self.memory = ConversationBufferMemory(return_messages=True,
        # ai_prefix="Poker Game Host", human_prefix="Inner Guide")
        # TODO: create a prompt for the game manager
        # self.conversation = ConversationChain(memory=self.memory, prompt=self.create_prompt(), llm=self.chat)

    def set_current_round(self, current_round):
        self.current_round = current_round

    def reset_deck(self):
        self.deck = Deck()

    def set_dealer(self, player):
        self.dealer = player

    def set_current_player(self, player):
        self.current_player = player

    # TODO: review this property, i think it's introduced a bug to how the AI is calling bets
    @property
    def cost_to_call(self):
        # Calculate the cost for the current player to call and be even with the pot
        if self.current_player.total_bet_this_hand is None:
            return None
        else:
            return self.current_bet - self.current_player.total_bet_this_hand

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    @property
    def next_player(self):
        index = self.players.index(self.current_player)

        while True:
            index = (index + 1) % len(self.players)  # increment the index by 1 and wrap around the loop if needed
            player = self.players[index]
            if player in self.remaining_players:
                remaining_players_index = self.remaining_players.index(player)
                return self.remaining_players[remaining_players_index]

    def get_opponent_positions(self, requesting_player=None):
        opponent_positions = []
        for player in self.players:
            if player != requesting_player:
                position = f'{player.name} has ${player.money}'
                position += ' and they have folded' if player.folded else ''
                position += '.\n'
                opponent_positions.append(position)
        return ''.join(opponent_positions)

    @property
    def game_state(self):
        game_state = {
            "players": self.players,
            "opponent_positions": self.get_opponent_positions(),
            "current_situation": f"The {self.current_round} cards have just been dealt",
            "current_pot": self.pot,
            "player_options": self.player_options,
            "community_cards": self.community_cards,
            "current_bet": self.current_bet,
            "current_round": self.current_round,
            "cost_to_call": self.cost_to_call,
            "last_action": self.last_action,
            "game_interface": self.interface
        }
        return game_state

    def play_hand(self):
        self.deck.shuffle()
        self.set_remaining_players()
        self.set_current_round("pre-flop")
        self.post_blinds()

        self.display_text(f"{self.dealer.name}'s deal.\n")
        self.display_text(f"Small blind: {self.small_blind_player.name}\n Big blind: {self.big_blind_player.name}\n")

        self.deal_hole_cards()

        start_player = self.determine_start_player()

        index = self.players.index(start_player)  # Set index at the start_player
        round_queue = self.players.copy()  # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)  # Move to the start_player
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
        # TODO: add return winner, self.pot

    def deal_hole_cards(self):
        for player in self.players:
            player.cards = self.deck.deal(2)

    def post_blinds(self):
        small_blind = self.small_blind
        big_blind = small_blind * 2

        self.small_blind_player = self.players[(self.dealer_position + 1) % len(self.players)]
        self.big_blind_player = self.players[(self.dealer_position + 2) % len(self.players)]
        self.under_the_gun = self.players[(self.dealer_position + 3) % len(self.players)]

        self.small_blind_player.money -= small_blind
        self.small_blind_player.total_bet_this_hand += small_blind
        self.big_blind_player.money -= big_blind
        self.big_blind_player.total_bet_this_hand += big_blind
        self.last_to_act = self.big_blind_player

        self.pot += small_blind + big_blind
        self.current_bet = big_blind

    def set_remaining_players(self):
        remaining_players = []
        for player in self.players:
            if not player.folded:
                remaining_players.append(player)
        self.remaining_players = remaining_players

    def player_add_to_pot(self, player, add_to_pot=0):
        player.get_for_pot(add_to_pot)
        self.pot += add_to_pot

    def process_pot_update(self, player: PokerPlayer, add_to_pot: int):
        player.money -= add_to_pot
        player.total_bet_this_hand += add_to_pot
        self.pot += add_to_pot

    def handle_bet_or_raise(self, player, add_to_pot, next_round_queue):
        self.process_pot_update(player, add_to_pot)
        self.current_bet = player.total_bet_this_hand
        return self.betting_round(next_round_queue, first_round=False)

    def handle_all_in(self, player, add_to_pot, next_round_queue):
        self.process_pot_update(player, add_to_pot)
        raising = add_to_pot > self.current_bet
        if raising:
            self.current_bet = player.total_bet_this_hand
            return self.betting_round(next_round_queue, first_round=False)

    def handle_call(self, player, add_to_pot):
        self.process_pot_update(player, add_to_pot)

    def handle_fold(self, player):
        player.folded = True

    def get_next_round_queue(self, round_queue):
        next_round_queue = round_queue.copy()
        shift_list_left(next_round_queue)
        return next_round_queue

    def betting_round(self, round_queue, first_round: bool = True):
        # betting_round takes in a list of Players in order of their turns
        if not first_round:
            # all 4 players in the queue should bet in the first round,
            # after any raise the entire queue is sent but the raiser is removed from the turn queue as they don't get a
            round_queue.pop()  # Remove the last raiser from the betting round. last_raiser is currently unused, keeping it in case we need it later

        for player in round_queue:
            # Before each player action, several checks and updates are performed
            self.set_remaining_players()  # Update the hands' remaining players list
            if len(self.remaining_players) <= 1:  # Round ends if there are no players to bet
                return False

            self.set_current_player(player)  # Update the game's current player property
            player_options = self.determine_player_options(self.current_player)  # Set action options for the current player
            self.player_options = player_options

            # Once checks and updates above are complete, we can get the action from the player and decide what to do with it
            if player.folded:
                round_queue.remove(player)
                # TODO: do other things when the player has folded, let them interact with the table, etc.

            else:
                # TODO: update the cost_to_call calculation or how the data is received or sent to the AI
                # TODO: the issue seems to come from the AI not sending the right number when calling because
                # TODO: we send them bad info on what the bet/cost to call is
                action, add_to_pot = player.action(self.game_state)
                self.last_action = action
                # No checks are performed here on input. Relying on "determine_player_options" to work as expected
                if action == "bet" or action == "raise":
                    return self.handle_bet_or_raise(player, add_to_pot, self.get_next_round_queue(round_queue))
                elif action == "all-in":
                    return self.handle_all_in(player, add_to_pot, self.get_next_round_queue(round_queue))
                elif action == "call":
                    self.handle_call(player, add_to_pot)
                elif action == "fold":
                    self.handle_fold(player)
                elif action == "check":
                    pass
                else:
                    return "ERROR: Invalid action"

    def reveal_cards(self, num_cards, round_name):
        """
        Reveal the cards.

        :param num_cards: Number of cards to reveal
        :param round_name: Name of the current round
        :return: string with text to output and revealed cards
        """
        self.discard_pile = self.deck.deal(1)
        new_cards = self.deck.deal(num_cards)
        self.community_cards += new_cards
        self.current_round = round_name
        output_text = f"""
                    ---***{round_name.upper()}***---
            {self.community_cards}
"""
        output_text += render_cards(new_cards)

        return output_text, new_cards

    def reveal_flop(self):
        output_text, new_cards = self.reveal_cards(3, "flop")
        # render_cards(new_cards)
        self.display_text(output_text)

    def reveal_turn(self):
        self.reveal_cards(1, "turn")

    def reveal_river(self):
        self.reveal_cards(1, "river")

    def rotate_dealer(self):
        current_dealer_starting_player_index = self.starting_players.index(self.dealer)
        new_dealer_starting_player_index = (current_dealer_starting_player_index + 1) % len(self.starting_players)
        self.dealer = self.starting_players[new_dealer_starting_player_index]
        if self.dealer.money <= 0:
            self.rotate_dealer()

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

    def end_hand(self):
        # Evaluate and announce the winner
        winner = self.determine_winner()
        self.display_text(f"The winner is {winner.name}! They win the pot of {self.pot}")

        # Reset game for next round
        winner.money += self.pot
        self.pot = 0
        self.community_cards = []
        self.current_round = "pre-flop"  # TODO: move this to the initialization of the round
        self.rotate_dealer()
        self.reset_deck()

        # Check if the game should continue
        self.players = [player for player in self.starting_players if player.money > 0]
        if len(self.players) == 1:
            self.display_text(f"{self.players[0].name} is the last player remaining and wins the game!")
            return
        elif len(self.players) == 0:
            self.display_text("You... you all lost. Somehow you all have no money.")
            return

        # Reset players
        for player in self.players:
            player.cards = []
            player.folded = False
            player.total_bet_this_hand = 0
            if isinstance(player, AIPokerPlayer):
                player.assistant.trim_memory()

    def determine_start_player(self):
        start_player = None
        if self.current_round == "pre-flop":
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

    def set_betting_round_state(self):
        # Sets the state of betting round i.e. Player 1 raised 20. Player 2 you're next, it's $30 to call. You can also raise or fold.
        self.betting_round_state = f"{self.last_move}. {self.next_player} you are up next. It is ${self.cost_to_call} to call, you can also raise or fold."

    # @staticmethod
    # def export_game(self, file_name='game_state.pkl'):
    #     with open(file_name, 'wb') as f:
    #         return pickle.dump(self, f)
    #
    # @staticmethod
    # def load_game(file_name='game_state.pkl'):
    #     with open(file_name, 'rb') as f:
    #         return pickle.load(f)

    # TODO: change this to return the options as a PlayerAction enum
    def determine_player_options(self, poker_player: PokerPlayer):
        # How much is it to call the bet for the player?
        players_cost_to_call = self.current_bet - poker_player.total_bet_this_hand
        # Does the player have enough to call
        player_has_enough_to_call = poker_player.money > players_cost_to_call
        # Is the current player also the big_blind TODO: add "and have they played this hand yet"
        current_player_is_big_blind = poker_player is self.big_blind_player

        # If the current player is last to act (aka big blind), and we're still in the pre-flop round
        if (current_player_is_big_blind
                and self.current_round == "pre-flop"
                and self.current_bet == self.small_blind * 2):
            player_options = ['check', 'raise', 'all-in']
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in']
            if players_cost_to_call == 0:
                player_options.remove('fold')
            if players_cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or players_cost_to_call == 0:
                player_options.remove('call')
            if self.current_bet > 0 or players_cost_to_call > 0:
                player_options.remove('bet')
            if poker_player.money - self.current_bet <= 0 or 'bet' in player_options:
                player_options.remove('raise')
            if not self.all_in_allowed or poker_player.money == 0:
                player_options.remove('all-in')

        # self.player_options = player_options.copy()
        poker_player.options = player_options.copy()
        return player_options


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
                player.confidence = player.initialize_attribute("confidence", mood=i)
                player.attitude = player.initialize_attribute("attitude", mood=i)

    return players


def main():
    # Create Players for the game
    players = get_players()

    poker_game = PokerGame(players, ConsoleInterface())
    poker_game.set_dealer(players[random.randint(0, len(players) - 1)])

    # Run the game until it ends
    while len(poker_game.players) > 1:
        poker_game.play_hand()
        play_again = poker_game.interface.request_action(
            ["yes", "no"],
            "Would you like to play another hand?")
        if play_again.lower() != "yes":
            break


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
    hole_card_art = card_template.format(card_1.rank, card_2.rank,
                                         card_1.suit_ascii[card_1.suit], card_2.suit_ascii[card_2.suit],
                                         card_2.suit_ascii[card_2.suit],
                                         card_2.rank)
    return hole_card_art


if __name__ == "__main__":
    main()
