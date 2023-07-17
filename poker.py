from collections import Counter
from cards import *
import random
import json
import pickle

from langchain import ConversationChain

from langchain.chat_models import ChatOpenAI

from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    HumanMessage
)
from langchain.memory import ConversationBufferMemory, ReadOnlySharedMemory, CombinedMemory

from dotenv import load_dotenv

load_dotenv()


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
        return {"hand_rank": 10, "hand_values": [], "kicker_values": sorted(self.ranks, reverse=True)}

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


class Player:
    def __init__(self, name="Player", starting_money=10000):
        self.name = name
        self.money = starting_money
        self.cards = []
        self.chat_message = ""
        self.confidence = ""
        self.attitude = ""
        self.options = ""
        self.folded = False
        self.total_bet_this_round = 0

    """@property
    def current_state(self):
        my_state = {"persona": self.name,
                    "confidence": "Unshakeable",
                    "attitude": "Smitten",
                    "player_money": self.money,
                    "hole_cards": self.cards,
                    # could break this out into "game_state" or "hand_state" vs. "player_state"
                    "number_of_opponents": 2,
                    "opponent_positions": ["Jeff has $1000 to your left", "Hal has $900 to your right"],
                    "position": "small blind",
                    "current_situation": "The hole cards have just been dealt",
                    "current_pot": 30,
                    "player_options": "call, raise, fold",
                    }

        return my_state"""

    def action(self, game_state):

        community_cards = game_state['community_cards']
        current_bet = game_state['current_bet']
        current_pot = game_state['current_pot']

        print(f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}\n",
              f"Community cards: {community_cards}\n",
              f"Current bet: {current_bet}\n",
              f"Current pot: {current_pot}\n")

        if current_bet == 0:
            action = input("Enter action (check/bet): ")
        else:
            action = input("Enter action (call/raise/fold): ")

        bet = 0
        if action in ("bet", "raise"):
            bet = int(input("Enter amount: "))
            # self.money -= bet
        elif action == "call":
            bet = current_bet
            # self.money -= current_bet
        self.chat_message = input("Enter chat message (optional): ")
        if not self.chat_message:
            f"{self.name} chooses to {action}."
        return action, bet

    def speak(self):
        return self.chat_message


class AIPlayer(Player):
    def __init__(self, name="AI Player", starting_money=10000, ai_temp=.9):
        super().__init__(name, starting_money=starting_money)
        self.chat = ChatOpenAI(temperature=ai_temp, model="gpt-3.5-turbo-16k")
        self.memory = ConversationBufferMemory(return_messages=True, ai_prefix=self.name, human_prefix="Narrator")
        # TODO: create logic to pull the Human prefix from the Human player name
        self.conversation = ConversationChain(memory=self.memory, prompt=self.create_prompt(), llm=self.chat)
        
        print(f"Initializing {self.name}")

        self.confidence = self.chat([HumanMessage(
                content=f"Describe {self.name}'s confidence as they enter a poker game in less than 50 words")]).content
        print(self.confidence)
        
        self.attitude = self.chat([HumanMessage(
                content=f"Describe {self.name}'s attitude as they enter a poker game in less than 50 words")]).content
        print(self.attitude)

    def create_prompt(self):
        persona = self.name
        confidence = self.confidence
        attitude = self.attitude
        player_money = self.money

        sample_string = (
            f"""
        Persona: {persona}
        Attitude: {attitude}
        Confidence: {confidence}
        Starting money: {player_money}

        You are taking on the role of {persona} playing a round of Texas Hold em with a group of celebrities.
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
        or things you want to say to the table, any pysical movements you make at the table, and your inner monologue

        When asked for your action, you must always respond in JSON format based on the example below

        Response template:
        {{{{
            "hand_strategy": <short analysis of current situation based on your persona and the cards>,
            "action": <enter the action you're going to take here, select from the options provided>,
            "amount": <enter the dollar amount to bet here>,
            "comment": <enter what you want to say here, this will be heard by your opponents. try to use this to your advantage>,
            "inner_monologue": <enter your internal thoughts here, these won't be shared with the others at the table>,
            "persona_response": <based on your persona, attitude, and confidence, provide a unique response to the situation>,
            "physical": <enter a list of strings with the physical actions you take in the order you take them>
            "new_confidence": <a single word indicating how confident you feel about your chances of winning the game>
            "new_attitude": <a single word indicating your attitude in the moment, it can be the same as before or change>
            "bluff_liklihood": <int representing % liklihood you will bluff>
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
            "new_attiude": "Gloomy",
            "bluff_liklihood": 30
        }}}}

        Remember {persona}, you're feeling {attitude} and {confidence}.
        Stay in character and keep your responses in JSON format.

        """)

        poker_prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(template=sample_string),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{input}")
        ])
        return poker_prompt

    def action(self, game_state):

        community_cards = game_state["community_cards"]
        current_bet = game_state["current_bet"]
        current_pot = game_state["current_pot"]

        print(f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}\n",
              f"Community cards: {community_cards}\n",
              f"Current bet: {current_bet}\n",
              f"Current pot: {current_pot}\n")

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
            print(response)
            response = self.conversation.predict(input="Please correct your response, it wasn't valid JSON.")
            response_json = json.loads(response)
            
        #print(json.dumps(response_json, indent=4))

        action = response_json["action"]
        bet = response_json["amount"]
        self.chat_message = response_json["comment"]
        self.attitude = response_json["new_attitude"]
        self.confidence = response_json["new_confidence"]

        print(f"{self.name} chooses to {action} by {bet}.")

        return action, bet

    def evaluate_hole_cards(self):
        # Use Monte Carlo method to approximate hand strength
        hand_ranks = []
        for _ in range(100):  # Adjust this number as needed
            simulated_community = Deck().draw(5)
            simulated_hand_rank = HandEvaluator(self.cards + simulated_community).evaluate_hand()["hand_rank"]
            hand_ranks.append(simulated_hand_rank)
        hand_rank = sum(hand_ranks) / len(hand_ranks)
        return hand_rank

    def speak(self):
        return self.chat_message

    def retrieve_response(self, game_state):
        persona = self.name
        confidence = self.confidence
        attitude = self.attitude
        opponents = game_state["players"]
        number_of_opponents = len(opponents) - 1
        position = "small blind"
        player_money = self.money
        current_situation = game_state["current_situation"]
        hole_cards = self.cards
        community_cards = game_state["community_cards"]
        current_bet = game_state["current_bet"]
        current_pot = game_state["current_pot"]
        player_options = game_state["player_options"]
        opponent_positions = game_state["opponent_positions"]
        current_round = game_state["current_round"]

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
        #print(sample_string)

        player_response = self.conversation.predict(input=sample_string)

        return player_response

  
class Game:
    def __init__(self, *players):
        self.betting_round_state = None
        self.deck = Deck()
        self.starting_players = list(players)
        self.players = list(players)
        self.remaining_players = list(players)
        self.community_cards = []
        self.current_bet = 0
        self.pot = 0
        self.small_blind = 50
        self.current_round = "initializing"
        self.dealer = Player("dealer")
        self.small_blind_player = Player("small_blind")
        self.big_blind_player = Player("big_blind")
        self.under_the_gun = Player("under_the_gun")

        self.current_player = None
        self.player_options = []
        self.min_bet = self.small_blind * 2
        self.max_bet = None
        self.pot_limit = None
        self.chat = ChatOpenAI(temperature=.3, model="gpt-3.5-turbo-16k")
        self.memory = ConversationBufferMemory(return_messages=True, ai_prefix="Poker Game Host", human_prefix="Inner Guide")
        # TODO: create a prompt for the game manager
        # self.conversation = ConversationChain(memory=self.memory, prompt=self.create_prompt(), llm=self.chat)

    def set_current_round(self, current_round):
        self.current_round = current_round

    def reset_deck(self):
        self.deck = Deck()

    def set_dealer(self, player):
        self.dealer = player

    def set_current_player(self, player):
        if type(player) == type(Player("Player")):
            self.current_player = player
        elif type(player) is type(int()):
            self.current_player = self.players[player]
        else:
            return False

    @property
    def cost_to_call(self):
        # Calculate the cost for the current player to call and be even with the pot
        return self.current_bet - self.current_player.total_bet_this_round

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    def get_next_player(self, i):
        return self.players[i % len(self.players)]

    @property
    def game_state(self):
        opponent_positions = ""

        for player in self.players:
            position = f"{player.name} has ${player.money}\n"
            opponent_positions += position

        game_state = {"players": self.players,
                      "opponent_positions": opponent_positions,
                      "current_situation": f"The {self.current_round} cards have just been dealt",
                      "current_pot": self.pot,
                      "player_options": self.player_options,
                      "community_cards": self.community_cards,
                      "current_bet": self.current_bet,
                      "current_round": self.current_round
                      }
        return game_state

    def play_hand(self):
        self.reset_deck()  # Create a new deck at the beginning of each hand
        self.deck.shuffle()
        self.set_remaining_players()
        self.set_dealer(self.players[random.randint(0, len(self.players) - 1)])
        self.post_blinds()

        print(f"{self.dealer.name}'s deal.\n")
        print(f"Small blind: {self.small_blind_player.name}\n Big blind: {self.big_blind_player.name}\n")

        self.deal_hole_cards()
        self.betting_round(self.determine_start_player())

        self.reveal_flop()
        self.betting_round(self.determine_start_player())

        self.reveal_turn()
        self.betting_round(self.determine_start_player())

        self.reveal_river()
        self.betting_round(self.determine_start_player())

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
        self.big_blind_player.money -= big_blind

        self.pot += small_blind + big_blind
        self.current_bet = big_blind

    def set_remaining_players(self):
        remaining_players = []
        for player in self.players:
            if not player.folded:
                remaining_players.append(player)
        self.remaining_players = remaining_players

    """def bad_betting_round(self):
        if len(self.remaining_players) <= 1:
            return  # round is over if there's only 1 player left in the hand
        i = None    # initialize counter
        if self.current_round == "preflop":
            i = (self.dealer_position + 3) % len(self.players)  # Player to left of big blind starts
            last_raiser = self.big_blind_player
        else:
            # Find the first player to the left of the dealer who hasn't folded
            for j in range(1, len(self.players)):
                if not self.players[(self.dealer_position + j) % len(self.players)].folded:
                    # Find the index for the player in the remaining_players_list
                    i = self.remaining_players.index(self.players[(self.dealer_position + j) % len(self.players)])
                    break

            # If all players have folded, end the betting round
            if i is None:
                return

            last_raiser = self.remaining_players[i]  # Assign last_raiser from remaining_players, not players

        new_bet = False

        while True:
            player = self.players[i % len(self.players)]

            # If we've gone around to the last raiser without encountering any new raises, end the betting round
            if player == last_raiser and not new_bet and not last_raiser.folded:
                break

            if not player.folded:
                action, bet = player.action(self.current_state)

                if action == "bet" or action == "raise":
                    self.current_bet = bet
                    self.pot += bet
                    player.money -= bet
                    last_raiser = player
                    new_bet = True
                elif action == "call":
                    player.money -= self.current_bet
                    self.pot += self.current_bet
                    new_bet = False
                elif action == "fold":
                    player.folded = True
                    # Update last_raiser to the next active player
                    if player == last_raiser:
                        last_raiser = self.remaining_players[i % len(self.remaining_players)]
                    new_bet = False
                elif action == "check" and self.current_bet == 0:
                    new_bet = False  # No new bet is made when a player checks
                else:
                    print("Invalid action")

                # SPEAK
                print(f"\n{player.name}:\t{player.speak()}\n")

            i = (i + 1) % len(self.players)

        self.current_bet = 0"""

    # TODO change to use the "last_raised" vs. "start_"player"
    def betting_round(self, start_player=None):
        if start_player is None:  # This is the start of a new betting round
            start_player = self.determine_start_player()

        i = self.players.index(start_player)  # Start at the start_player

        while True:
            player = self.players[i % len(self.players)]

            if not player.folded:
                self.set_player_options()
                action, bet = player.action(self.game_state)

                if action == "bet" or action == "raise":
                    self.current_bet = bet
                    self.pot += bet
                    player.money -= bet
                    
                    i += 1

                    next_player = self.players[i % len(self.players)]  # The next player to act

                    # A bet or raise starts a new betting round
                    # Call betting_round recursively with the next player as the start_player
                    return self.betting_round(next_player)
                elif action == "call":
                    player.money -= self.current_bet
                    self.pot += self.current_bet
                elif action == "fold":
                    player.folded = True
                    self.set_remaining_players()
                    if len(self.remaining_players) <= 1:
                        # self.current_bet = 0
                        break
                elif action == "check" and self.current_bet == 0:
                    pass
                else:
                    print("Invalid action")

                # SPEAK
                print(f"\n{player.name}:\t{player.speak()}\n")

            i = (i + 1) % len(self.players)

            # If we've gone through all players without starting a new betting round, the betting round is over
            if self.players[i] == start_player:
                break
                
        self.current_bet = 0
        
    def reveal_flop(self):
        self.community_cards = self.deck.deal(3)
        self.current_round = "flop"
        print(f"""
                    ---***FLOP***---
            {self.community_cards}
        """)

    def reveal_turn(self):
        self.community_cards += self.deck.deal(1)
        self.current_round = "turn"
        print(f"""
                    ---***TURN***---
            {self.community_cards}
        """)

    def reveal_river(self):
        self.community_cards += self.deck.deal(1)
        self.current_round = "river"
        print(f"""
                    ---***RIVER***---
            {self.community_cards}
        """)
        
    def rotate_dealer(self):
        self.dealer = (self.dealer_position + 1) % len(self.players)

    def determine_winner(self):
        hands = [(player, HandEvaluator(player.cards + self.community_cards).evaluate_hand())
                 for player in self.remaining_players]

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
        winner = self.determine_winner()
        print(f"The winner is {winner.name}! They win the pot of {self.pot}")

        winner.money += self.pot
        self.pot = 0
        self.community_cards = []
        self.current_round = "preflop"
        # Clear the players' hands
        for player in self.players:
            player.cards = []
        # Check if the game should continue
        self.players = [player for player in self.starting_players if player.money > 0]
        if len(self.players) == 1:
            print(f"{self.players[0].name} is the last player remaining and wins the game!")
            return
        
        for player in self.players:
            player.folded = False
        
        self.rotate_dealer()

    def determine_start_player(self):
        start_player = None
        if self.current_round == "preflop":
            # Player to left of big blind starts
            start_player = self.players[(self.dealer_position + 3) % len(self.players)]
        else:
            # Find the first player to the left of the dealer who hasn't folded
            for j in range(1, len(self.players)+1):
                if not self.players[(self.dealer_position + j) % len(self.players)].folded:
                    start_player = self.players[(self.dealer_position + j) % len(self.players)]
                    break
        return start_player

    def set_betting_round_state(self):
        # Sets the state of betting round i.e. Player 1 raised 20. Player 2 you're next, it's $30 to call. You can also raise or fold.
        self.betting_round_state = f"{self.last_move}. {self.next_player} you are up next. It is ${self.cost_to_call} to call, you can also raise or fold."

    @staticmethod
    def export_game(self, file_name='game_state.pkl'):
        with open(file_name, 'wb') as f:
            return pickle.dump(self, f)

    @staticmethod
    def load_game(file_name='game_state.pkl'):
        with open(file_name, 'rb') as f:
            return pickle.load(f)

    def determine_player_options(self):
        # How much is it to call the bet for the player?
        players_cost_to_call = self.current_bet - self.current_player.total_bet_this_round
        # Does the player have enough to call
        player_has_enough_to_call = self.current_player.money > players_cost_to_call
        # Is the current player also the big_blind TODO: add "and have they played this hand yet"
        current_player_is_big_blind = self.current_player is self.big_blind_player

        if current_player_is_big_blind and self.current_round == "preflop":
            player_options = ['check', 'raise', 'all-in']   # If the current player is last to act aka big blind, and we're still in the blind round
        else:
            player_options = ['fold', 'check', 'call', 'bet', 'raise', 'all-in']
            if self.cost_to_call == 0:
                player_options.remove('fold')
            if self.cost_to_call > 0:
                player_options.remove('check')
            if not player_has_enough_to_call or players_cost_to_call == 0:
                player_options.remove('call')
            if self.current_bet > 0:
                player_options.remove('bet')
            if self.current_bet - self.current_player.money == 0:
                player_options.remove('raise')
            if not self.all_in_allowed:
                player_options.remove('all-in')
            
        self.player_options = player_options.copy()
        self.current_player.options = player_options.copy()


def main():
    # game = Game(Player("Jeff"), AIPlayer("Kanye West"), AIPlayer("Tiger Woods"), AIPlayer("Charles Barkley"))
    # game = Game(AIPlayer("Phil Hellmuth"), AIPlayer("Tom Cruise"), AIPlayer("Whoopi Goldberg"))
    game = Game(AIPlayer("Jon Stewart", ai_temp=.9), AIPlayer("Jim Cramer", ai_temp=.9))

    while len(game.players) > 1:
        game.play_hand()
        game.current_bet = 0
        play_again = input("Play another hand? (y/n): ")
        if play_again.lower() != "y":
            break


if __name__ == "__main__":
    main()
    