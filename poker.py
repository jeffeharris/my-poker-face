from collections import Counter
from player import *

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
            if player.folded:
                add_string = " and they have folded"
            else:
                add_string = ""
            position = f"{player.name} has ${player.money}{add_string}" f".\n"
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

        start_player = self.determine_start_player()

        index = self.players.index(start_player)  # Set index at the start_player
        round_queue = self.players.copy()   # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)     # Move to the start_player
        self.betting_round(round_queue)

        self.reveal_flop()
        start_player = self.determine_start_player()
        index = self.players.index(start_player)
        round_queue = self.players.copy()   # Copy list of all players that started the hand, could include folded
        shift_list_left(round_queue, index)     # Move to the start_player
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
    
    def betting_round(self, round_queue, first_round: bool = True):     # betting_round takes in a list of Players in order of their turns
        next_round_queue = round_queue.copy()   # Make a copy of the round queue to set up a queue for the next round in case we need it
       
        if not first_round:     # all 4 players in the queue should bet in the first round, after any raise the entire queue is sent but the raiser is removed from the turn queue as they don't get a
            last_raiser = round_queue.pop()    # Remove the last raiser from the betting round. last_raiser is currently unused, keeping it in case we need it later
        
        for player in round_queue:
            # Before each player action, several checks and updates are performed
            self.set_remaining_players()            # Update the hands remaining players list
            if len(self.remaining_players) <= 1:    # Round ends if there are no players to bet
                return False
            shift_list_left(next_round_queue)       # next_round_queue is initialized for the betting round above and shifted here for every player
            self.set_current_player(player)         # Update the games current player proprerty
            self.determine_player_options()         # Set action options for the current player

            # Once checks and updates above are complete, we can get the action from the player and decide what to do with it
            if player.folded:
                next_round_queue.remove(player)
                # TODO: do other things when the player has folded, let them interact with the table etc.
            else:
                # TODO: update the cost_to_call calculation or how the data is received or sent to the AI
                # TODO: the issue seems to come from the AI not sending the right number when calling because
                # TODO: we send them bad info on what the bet/cost to call is
                action, add_to_pot = player.action(self.game_state)
                self.last_action = action

                # No checks are performed here on input. Relying on "determine_player_options" to work as expected
                if action == "bet":
                    player.money -= add_to_pot
                    player.total_bet_this_hand += add_to_pot
                    self.pot += add_to_pot
                    self.current_bet = player.total_bet_this_hand
                    return self.betting_round(next_round_queue, first_round=False)
                
                elif action == "raise":
                    player.money -= add_to_pot
                    player.total_bet_this_hand += add_to_pot
                    self.pot += add_to_pot
                    self.current_bet = player.total_bet_this_hand
                    return self.betting_round(next_round_queue, first_round=False)
                
                elif action == "all-in":
                    player.money -= add_to_pot
                    player.total_bet_this_hand += add_to_pot
                    self.pot += add_to_pot
                    raising = add_to_pot > self.current_bet
                    if raising:
                        self.current_bet = player.total_bet_this_hand
                        return self.betting_round(next_round_queue, first_round=False)
                    
                elif action == "call":
                    player.money -= add_to_pot
                    player.total_bet_this_hand += add_to_pot
                    self.pot += add_to_pot
                    
                elif action == "fold":
                    player.folded = True
                    
                elif action == "check":
                    pass
                
                else:
                    print("Invalid Action")
        
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
        print(f"The winner is {winner.name}! They win the pot of {self.pot}")

        # Reset game for next round
        winner.money += self.pot
        self.pot = 0
        self.community_cards = []
        self.current_round = "preflop"  # TODO: move this to the initialization of the round
        self.rotate_dealer()
        self.reset_deck()

        # Check if the game should continue
        self.players = [player for player in self.starting_players if player.money > 0]
        if len(self.players) == 1:
            print(f"{self.players[0].name} is the last player remaining and wins the game!")
            return
        elif len(self.players) == 0:
            print("You... you all lost. Somehow you all have no money.")
            return

        # Reset players
        for player in self.players:
            player.cards = []
            player.folded = False
            player.total_bet_this_hand = 0
            if isinstance(player, AIPlayer):
                player.memory = ConversationBufferMemory(return_messages=True, ai_prefix=player.name, human_prefix="Narrator")
                # TODO: this is not working, we're trying to reset memory for the hand so that we don't hit the limit
    
    def determine_start_player(self):
        start_player = None
        if self.current_round == "preflop":
            # Player after big blind starts
            start_player = self.players[(self.dealer_position + 3) % len(self.players)]
        else:
            # Find the first player after the dealer who hasn't folded
            for j in range(1, len(self.players)+1):
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

    # TODO: change this to accept a player and retrun the options as a list of strings
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
            # TODO: check not being removed when it should be
            if players_cost_to_call > 0:
                player_options.remove('check')
            # TODO: call not being removed when it should be
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
        randos = celebrities[0:(2-len(definites))]
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
    