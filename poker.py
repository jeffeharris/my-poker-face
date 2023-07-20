from collections import Counter
from cards import *
import random
import json
import pickle
from player import *

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

  
class Game:
    def __init__(self, player_list=None, player_tuple=None):
        self.discard_pile = []  # list for the discarded cards to be placed in
        self.all_in_allowed = True
        if player_list is not None:
            self.starting_players = player_list
        elif player_tuple is not None:
            self.starting_players = list(player_tuple)
        self.players = list(self.starting_players)
        self.remaining_players = list(self.starting_players)
        self.last_to_act = None
        self.betting_round_state = None
        self.last_action = None     # represents the last action taken in a betting round
        self.deck = Deck()
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

    @property
    def cost_to_call(self):
        # Calculate the cost for the current player to call and be even with the pot
        return self.current_bet - self.current_player.total_bet_this_hand

    @property
    def dealer_position(self):
        return self.players.index(self.dealer)

    @property
    def next_player(self):
        index = self.players.index(self.current_player)

        while True:
            index = (index + 1) % len(self.players)     # increment the index by 1 and wrap around the loop if needed
            player = self.players[index]
            if player in self.remaining_players:
                remaining_players_index = self.remaining_players.index(player)
                return self.remaining_players[remaining_players_index]

    @property
    def game_state(self):
        opponent_positions = ""

        for player in self.players:
            position = f"{player.name} has ${player.money}\n"
            opponent_positions += position

        current_game_state = {"players": self.players,
                              "opponent_positions": opponent_positions,
                              "current_situation": f"The {self.current_round} cards have just been dealt",
                              "current_pot": self.pot,
                              "player_options": self.player_options,
                              "community_cards": self.community_cards,
                              "current_bet": self.current_bet,
                              "current_round": self.current_round,
                              "cost_to_call": self.cost_to_call,
                              "last_action": self.last_action
                              }
        return current_game_state

    def play_hand(self):
        self.deck.shuffle()
        self.set_remaining_players()
        self.set_current_round("preflop")
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

    # TODO: implement new betting round that uses either a shifting list or a single queue for each betting round
        # round_queue = []
        # for i in range(len(self.players)):
        #     round_queue.append(self.players[i])
    def betting_round(self, start_player=None):
        if len(self.remaining_players) <= 1:
            return False
        if start_player is None:  # This is the start of a new betting round
            start_player = self.determine_start_player()
        i = self.players.index(start_player)  # Start at the start_player
        exit_next_player = False    # Flag used to indicate an exit condition for the blind betting round where things are treated different
        
        while True:
            self.set_remaining_players()
            player = self.players[i % len(self.players)]    # We start with the start player and iterate i when we go to the next player

            if not player.folded:
                self.last_to_act, _ = self.determine_last_to_act(), self.set_current_player(player)
                self.determine_player_options()
                action, amount = player.action(self.game_state)
                self.last_action = action

                if action == "bet":
                    self.last_to_act = player.player_to_left(self.players)
                    added_to_pot = amount
                    player.money -= added_to_pot
                    player.total_bet_this_hand += added_to_pot
                    self.pot += added_to_pot
                    self.current_bet = max(p.total_bet_this_hand for p in self.players)
                    # A bet or raise starts a new betting round
                    # Call betting_round recursively with the next player as the start_player
                    return self.betting_round(self.next_player)

                elif action == "raise":
                    self.last_to_act = player.player_to_left(self.players)
                    added_to_pot = amount + self.cost_to_call
                    player.money -= added_to_pot
                    player.total_bet_this_hand += added_to_pot
                    self.pot += added_to_pot
                    self.current_bet = player.total_bet_this_hand  # TODO: this line only works if the player is RAISING all-in, not calling with the last of their own chips when they cant cover
                    return self.betting_round(self.next_player)

                elif action == "all-in":
                    player_is_raising = amount >= self.cost_to_call     # Check players money to see if they are raising the bet or just going all-in
                    added_to_pot = amount
                    player.money -= added_to_pot
                    player.total_bet_this_hand += added_to_pot
                    self.pot += added_to_pot
                    if player_is_raising:
                        self.last_to_act = player.player_to_left(self.players)
                        self.current_bet = player.total_bet_this_hand
                        return self.betting_round(self.next_player)

                elif action == "call":
                    added_to_pot = self.cost_to_call
                    self.pot += added_to_pot
                    player.money -= added_to_pot
                    player.total_bet_this_hand += added_to_pot

                elif action == "fold":
                    player.folded = True
                    self.discard_pile += player.cards
                    self.set_remaining_players()
                    if len(self.remaining_players) <= 1:
                        return None

                elif action == "check" and self.cost_to_call == 0:
                    pass
                else:
                    print("Invalid action")

                # SPEAK
                print(f"\n{player.name}:\t{player.speak()}\n")

            i = (i + 1) % len(self.players)     # Iterate to the next player if

            # If we've gone through all players without starting a new betting round, the betting round is over
            if exit_next_player:
                break
            elif self.current_round == "preflop" \
              and self.next_player is self.last_to_act \
              and self.next_player is self.big_blind_player \
              and self.current_bet == self.small_blind*2:
                exit_next_player = True
            elif self.current_player is self.last_to_act:  # When this betting_round ends, set the last raiser up for the next round
                break
        
    def reveal_flop(self):
        self.discard_pile = self.deck.deal(1)
        self.community_cards = self.deck.deal(3)
        self.current_round = "flop"
        print(f"""
                    ---***FLOP***---
            {self.community_cards}
        """)
        display_cards(self.community_cards)

    def reveal_turn(self):
        self.discard_pile = self.deck.deal(1)
        self.community_cards += self.deck.deal(1)
        self.current_round = "turn"
        print(f"""
                    ---***TURN***---
            {self.community_cards}
        """)
        display_cards(self.community_cards)

    def reveal_river(self):
        self.discard_pile = self.deck.deal(1)
        self.community_cards += self.deck.deal(1)
        self.current_round = "river"
        print(f"""
                    ---***RIVER***---
            {self.community_cards}
        """)
        display_cards(self.community_cards)
        
    def rotate_dealer(self):
        current_dealer_starting_player_index = self.starting_players.index(self.dealer)
        new_dealer_starting_player_index = (current_dealer_starting_player_index + 1) % len(self.starting_players)
        self.dealer = self.starting_players[new_dealer_starting_player_index]
        if self.dealer.money <= 0:
            self.rotate_dealer()

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
        # Evaluate and announce the winner
        winner = self.determine_winner()
        print(f"The winner is {winner.name}! They win the pot of {self.pot}")

        # Check if the game should continue
        self.players = [player for player in self.starting_players if player.money > 0]
        if len(self.players) == 1:
            print(f"{self.players[0].name} is the last player remaining and wins the game!")
            return
        elif len(self.players) == 0:
            print("You... you all lost. Somehow you all have no money.")
            return

        # Reset game for next round
        winner.money += self.pot
        self.pot = 0
        self.community_cards = []
        self.current_round = "preflop"
        self.rotate_dealer()
        self.reset_deck()

        # Reset players
        for player in self.players:
            player.cards = []
            player.folded = False
            player.total_bet_this_hand = 0
    
    def determine_start_player(self):
        start_player = None
        if self.current_round == "preflop":
            # Player to left of big blind starts
            start_player = self.players[(self.dealer_position + 3) % len(self.players)]
        else:
            # Find the first player to the left of the dealer who hasn't folded
            for j in range(1, len(self.players)+1):
                index = (self.dealer_position + j) % len(self.players)
                if not self.players[index].folded:
                    start_player = self.players[index]
                    break
        return start_player

    def determine_last_to_act(self, player=None):    # TODO: add input for the player when they raise
        last_to_act = None
        reversed_players = self.players.copy()
        reversed_players.reverse()
        if player is None:
            index = reversed_players.index(self.dealer)
        else:
            index = reversed_players.index(player)
        
        if self.current_round == "preflop" and self.current_bet == self.small_blind*2:
            # Player to left of big blind starts
            last_to_act = self.big_blind_player
        else:
            # Find the first player to the right of the dealer who is in the hand
            for j in range(1, len(self.players)+1):
                index = (index + j) % len(self.players)
                if not reversed_players[index].folded:
                    last_to_act = reversed_players[index]
                    break
        return last_to_act

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
        players_cost_to_call = self.current_bet - self.current_player.total_bet_this_hand
        # Does the player have enough to call
        player_has_enough_to_call = self.current_player.money > players_cost_to_call
        # Is the current player also the big_blind TODO: add "and have they played this hand yet"
        current_player_is_big_blind = self.current_player is self.big_blind_player

        if current_player_is_big_blind and self.current_round == "preflop" and self.current_bet == self.small_blind*2:
            player_options = ['check', 'raise', 'all-in']   # If the current player is last to act aka big blind, and we're still in the blind round
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
            if self.current_player.money - self.current_bet <= 0 or 'bet' in player_options:
                player_options.remove('raise')
            if not self.all_in_allowed or self.current_player.money == 0:
                player_options.remove('all-in')
            
        self.player_options = player_options.copy()
        self.current_player.options = player_options.copy()


def main(test=False):
    # Create Players for the game
    definites = [
        Player("Jeff")
    ]
    
    if test:
        basic_test_players = [
            Player("Player1"),
            Player("Player2"),
            Player("Player3"),
            Player("Player4")
        ]
        
        players = basic_test_players
        game = Game(players)
        game.set_dealer(players[1])
        
    else:
        celebrities = [
            AIPlayer("Ace Ventura", ai_temp=.9),
            AIPlayer("Khloe and Kim Khardashian"),
            AIPlayer("Fred Durst"),
            AIPlayer("Tom Cruise"),
            AIPlayer("James Bond"),
            AIPlayer("Jon Stewart"),
            AIPlayer("Jim Cramer", ai_temp=.7),
            AIPlayer("Marjorie Taylor Greene", ai_temp=.7),
            AIPlayer("Lizzo"),
            AIPlayer("Bill Clinton"),
            AIPlayer("Barack Obama"),
            AIPlayer("Jesus Christ"),
            AIPlayer("Triumph the Insult Dog", ai_temp=.7),
            AIPlayer("Donald Trump", ai_temp=.7),
            AIPlayer("Batman"),
            AIPlayer("Deadpool"),
            AIPlayer("Lance Armstrong"),
            AIPlayer("A Mime", ai_temp=.8),
            AIPlayer("Jay Gatsby"),
            AIPlayer("Whoopi Goldberg"),
            AIPlayer("Dave Chappelle"),
            AIPlayer("Chris Rock"),
            AIPlayer("Sarah Silverman"),
            AIPlayer("Kathy Griffin"),
            AIPlayer("Dr. Seuss", ai_temp=.7),
            AIPlayer("Dr. Oz"),
            AIPlayer("A guy who tells too many dad jokes")
        ]

        random.shuffle(celebrities)
        randos = celebrities[0:(5-len(definites))]
        players = definites + randos
        for player in players:
            if isinstance(player, AIPlayer):
                i = random.randint(0, 2)
                player.confidence = player.initialize_attribute("confidence", mood=i)
                player.attitude = player.initialize_attribute("attittude", mood=i)
        game = Game(players)
        game.set_dealer(players[random.randint(0, len(players) - 1)])

    # Run the game until it ends
    while len(game.players) > 1:
        game.play_hand()
        play_again = input("Play another hand? (y/n): ")
        if play_again.lower() != "y":
            break


def shift_list_left(my_list: list, count: int = 1):
    """
    :param my_list: list that you want to manipulate
    :param count: how many shifts you want to make
    """
    for i in range(1, count+1):
        # Pop from the beginning of the list and append to the end
        my_list.append(my_list.pop(0))


def shift_list_right(my_list: list, count: int = 1):
    """
    :param my_list: list that you want to manipulate
    :param count: how many shifts you want to make
    """
    for i in range(1, count+1):
        # Pop from the end of the list and insert it at the beginning
        my_list.insert(0, my_list.pop())
    

if __name__ == "__main__":
    main()
    