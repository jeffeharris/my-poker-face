from collections import Counter
from cards import *
import random
import json

from langchain import ConversationChain

from langchain.chat_models import ChatOpenAI

from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)
from langchain.memory import ConversationBufferMemory

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
    def __init__(self, name, starting_money=1000):
        self.name = name
        self.money = starting_money
        self.cards = []
        self.chat_message = ""
        self.confidence = ""
        self.attitude = ""
        self.options = ""

    @property
    def current_state(self):
        my_state = {"persona": self.name,
                    "confidence": "Unshakeable",
                    "attitude": "Smitten",
                    "player_money": self.money,
                    "hole_cards": self.cards,
                    # could break this out into "game_state" or "hand_state" vs. "player_state"
                    "number_of_opponents": 2,
                    "opponent_positions": ["Jeff has $1000 to your left", "Halh has $900 to your right"],
                    "position": "small blind",
                    "current_situation": "The hole cards have just been dealt",
                    "current_pot": 30,
                    "player_options": "call, raise, fold",
                    }

        return my_state

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

    def chat(self):
        return self.chat_message


class AIPlayer(Player):
    def action(self, game_state):

        community_cards = game_state["community_cards"]
        current_bet = game_state["current_bet"]
        current_pot = game_state["current_pot"]

        print(f"{self.name}'s turn. Current cards: {self.cards} Current money: {self.money}\n",
              f"Community cards: {community_cards}\n",
              f"Current bet: {current_bet}\n",
              f"Current pot: {current_pot}\n")

        if len(community_cards) < 3:
            hand_rank = self.evaluate_hole_cards()
        else:
            hand_rank = HandEvaluator(self.cards + community_cards).evaluate_hand()["hand_rank"]

        pot_odds = current_pot / current_bet if current_bet else 1
        money_left = self.money / current_bet if current_bet else 1

        bet = 0

        # Adjust these thresholds as needed
        '''if current_bet == 0:
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
        #print("String response formatted as JSON:", response)
        response_json = json.loads(response)
        #print(json.dumps(response_json), 4)

        action = response_json["action"]
        bet = response_json["amount"]
        self.chat_message = response_json["comment"]

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

    def chat(self):
        return self.chat_message

    def retrieve_response(self, game_state):
        persona = self.name
        confidence = "Unshakeable"
        attitude = "Manic"
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

        sample_string = (
            f"""
        Persona: {persona}
        Attitude: {attitude}
        Confidence: {confidence}
        Opponents: {opponent_positions}
        Community Cards: {community_cards}

        You are {persona} playing a round of Texas Hold em with {number_of_opponents} other people.
        You are {position} and have ${player_money} in chips remaining. {current_situation},
        you have {hole_cards} in your hand. The current pot is ${current_pot}, the current bet is ${current_bet} to you.
        Your options are: {player_options}
        Feel free to express yourself physically using *things i'm doing* to indicate actions like *looks around table*.
        Don't over do this though, you are playing poker and you don't want to give anything away that would hurt your
        chances of winning. You should respond with a JSON containing your action, your bet (if applicable), any comments
        or things you want to say to the table, any pysical movements you make at the table, and your inner monologue

        Sample response:
        {{{{
            "action": <enter the action you're going to take here>,
            "amount": <enter the dollar amount to bet here>,
            "comment": <enter what you want to say here>,
            "inner_monologue": <enter your internal thoughts here, these won't be shared with the others at the table>,
            "persona_response": <based on your persona, attitude, and confidence, provide a unique response to the situation>,
            "physical": <enter a list of strings with the physical actions you take in the order you take them>
        }}}}

        Remember {persona}, you're feeling {attitude} and {confidence}. And you can not bet more than you have, ${player_money}.

        """)
        # it's ${amount_to_call} to you to call and cover the blind and $20 to bet. Would you like to call or fold?

        #print(sample_string)

        poker_prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(template=sample_string),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{input}")
        ])

        #print(poker_prompt)

        poker_player = ChatOpenAI(temperature=.5)

        player_memory = ConversationBufferMemory(return_messages=True)

        player_conversation = ConversationChain(memory=player_memory, prompt=poker_prompt, llm=poker_player)

        player_response = player_conversation.predict(input="What is your move?")

        return player_response

  
class Game:
    def __init__(self, *players):
        self.deck = Deck()
        self.players = list(players)
        self.community_cards = []
        self.current_bet = 0
        self.pot = 0
        self.small_blind = 10
        self.dealer = random.randint(0, len(self.players) - 1)
        self.small_blind_player = None
        self.big_blind_player = None

    def current_state(self):

        my_state = {"players": self.players,
                    "opponent_positions": ["Jeff has $1000 to your left", "Hal has $900 to your right"],
                    "position": "small blind",
                    "current_situation": f"The {self.current_round} cards have just been dealt",
                    "current_pot": self.pot,
                    "player_options": "call, raise, fold, bet",
                    "community_cards": self.community_cards,
                    "current_bet": self.current_bet,
                    "current_round": self.current_round
                    }

        return my_state

    def play_hand(self):
        self.deck = Deck()  # Create a new deck at the beginning of each hand
        self.deck.shuffle()
        
        self.deal_hole_cards()
        self.post_blinds()
        self.betting_round()
        for player in self.players:
            print(player.chat())
            
        self.reveal_flop()
        self.betting_round()
        for player in self.players:
            print(player.chat())
            
        self.reveal_turn()
        self.betting_round()
        for player in self.players:
            print(player.chat())
            
        self.reveal_river()
        self.betting_round()
        for player in self.players:
            print(player.chat())
            
        self.end_hand()
        
    def deal_hole_cards(self):
        for player in self.players:
            player.cards = self.deck.deal(2)
    
    def post_blinds(self):
        small_blind = self.small_blind
        big_blind = small_blind * 2

        self.small_blind_player = self.players[(self.dealer + 1) % len(self.players)]
        self.big_blind_player = self.players[(self.dealer + 2) % len(self.players)]

        self.small_blind_player.money -= small_blind
        self.big_blind_player.money -= big_blind

        self.pot += small_blind + big_blind
        self.current_bet = big_blind

    def betting_round(self):
        # DO LATER definite problem here that I'll need to fix. The starting player shouldn't always be the big blind,
        # just on the first round of betting. Same with the line after it
        if self.current_round == "deal":
            starting_player = (self.dealer + 3) % len(self.players)  # Player to left of big blind starts
            last_raiser = self.big_blind_player
        else:
            starting_player = self.dealer
            last_raiser = None

        i = starting_player
    
        while True:
            player = self.players[i % len(self.players)]
            # If we've gone around to the last raiser without encountering any new raises, end the betting round
            if player == last_raiser:
                break

            action, bet = player.action(self.current_state())
            
            if action == "bet":
                self.current_bet = bet
                self.pot += bet
                player.money -= bet
                last_raiser = player
            elif action == "raise":
                self.current_bet += bet
                self.pot += self.current_bet
                player.money -= self.current_bet
                last_raiser = player
            elif action == "call":
                player.money -= self.current_bet
                self.pot += self.current_bet
            elif action == "fold":
                self.players.remove(player)
            elif action == "check" and self.current_bet == 0:
                pass
            else:
                print("Invalid action")

            print(player.chat())
            i += 1
    
        self.current_bet = 0
        
    def reveal_flop(self):
        self.community_cards = self.deck.deal(3)
        self.current_round = "flop"

    def reveal_turn(self):
        self.community_cards += self.deck.deal(1)
        self.current_round = "turn"

    def reveal_river(self):
        self.community_cards += self.deck.deal(1)
        self.current_round = "river"
        
    def rotate_dealer(self):
        self.dealer = (self.dealer + 1) % len(self.players)

    def determine_winner(self):
        hands = [(player, HandEvaluator(player.cards + self.community_cards).evaluate_hand()) for player in self.players]

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
        # Clear the players' hands
        for player in self.players:
            player.cards = []
        # Check if the game should continue
        remaining_players = [player for player in self.players if player.money > 0]
        if len(remaining_players) == 1:
            print(f"{remaining_players[0].name} is the last player remaining and wins the game!")
            return
        
        
def main():
    game = Game(Player("Jeff"), AIPlayer("Hal"), AIPlayer("Alice"))
    while True:
        game.play_hand()
        play_again = input("Play another hand? (y/n): ")
        if play_again.lower() != "y":
            break


if __name__ == "__main__":
    main()
    