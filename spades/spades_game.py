# spades_game.py
import json

from flask import Flask, render_template, request, redirect, url_for, session

from old_files.deck import Deck
from core.card import Card
from core.assistants import OpenAILLMAssistant
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())

# TODO: <REFACTOR> integrate the Deck and Card and CardSet classes here
# Define card ranks and suits
# suits = ['Clubs', 'Diamonds', 'Hearts', 'Spades']
# ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

# TODO: <REFACTOR> integrate Player and AIPlayer classes here
player_names = ['Team A Player 1', 'Team B Player 1', 'Team A Player 2', 'Team B Player 2']

prompt = (
    "You are participating in a game of team Spades. In Spades, a trick is won when a player plays the highest card in the leading suit, "
    "or if a Spade (the trump suit) is played. If a player cannot follow suit, they can 'cut' with a Spade, which automatically wins the trick unless a higher Spade is played. "
    "Any Spade played will beat all cards in other suits, regardless of their rank. 'A' is the highest rank and beats all other ranks. **You must always follow the leading suit if you have a card in that suit, even if it will not win.**\n\n"

    "When deciding which card to play, always consider the following:\n"
    "- **If your teammate is currently leading the trick with the highest card, do not play a higher card to 'steal' the win**. Instead, play the lowest card you have that still follows suit, or discard if you cannot follow suit. This conserves your higher cards for future tricks when they may be more valuable.\n"
    "- **Play the lowest card that can still win the trick**. If a lower Spade can win the trick, use it instead of a higher Spade. This allows you to save higher-value Spades (like the Queen or King) for future rounds where they will be more impactful.\n"
    "- If you're playing last (in position), analyze the cards already played. Use this to either play a high card and win the trick or intentionally play a low card to conserve stronger ones for future rounds.\n"

    "You cannot pass on your turn. You must always play a card, even if it won't help you win the trick. **If a lower card will win or if your teammate is winning, play that card and save your higher cards for later.**\n\n"

    "Save your high Spades for crucial late-game tricks when other suits have run out. Conversely, get rid of low cards in suits you're weak in early on to avoid being forced to lose later. "
    "Keeping track of which cards have been played will help you predict opponents' hands and adjust your strategy.\n\n"

    "When a Spade has been played in a trick, it automatically beats all non-Spade cards. Only a higher Spade can win after a Spade is played. "
    "If a non-Spade card has been played but a Spade is in the trick, do not play a non-Spade thinking it will win, as it cannot beat any Spade."
)

assistant = OpenAILLMAssistant(system_message=prompt)

# Deal cards to player_names
def deal_cards():
    deck = Deck()
    deck.shuffle()
    # TODO: <NEXT-STEP> replace "deck" with Deck()
    hands = {name: [] for name in player_names}
    for i, card in enumerate(deck.card_deck.cards):     # TODO: make it easier to access the iterable cards for a Deck
        player = list(hands.keys())[i % 4]
        hands[player].append(card.to_dict())
    # Sort each hand
    for hand in hands.values():
        hand.sort(key=lambda x: (deck.card_deck.SUITS.index(x["suit"]), deck.card_deck.RANKS.index(x["rank"])))
    return hands

# Initialize game state
def initialize_game():
    game_state = {
        'hands': {},  # Will be populated after dealing
        'bids': {},
        'nil_bids': {},
        'failed_nil': [],
        'tricks_won': {'Team A': 0, 'Team B': 0},
        'current_trick': [],
        'current_player': None,
        'trick_number': 1,
        'spades_broken': False,
        'scores': {'Team A': 0, 'Team B': 0},
        'game_over': False,
        'winning_team': None,
        'previous_trick': [],
        'previous_trick_winner': None,
        'teams': {
            'Team A': [player_names[0], player_names[2]],
            'Team B': [player_names[1], player_names[3]]
        },
        'bidding_order': player_names,
        'current_bidder_index': 0,
        'current_bids': {},
        'round_number': 1
    }
    return game_state

def get_game_state():
    game_state = session.get('game_state')
    if not game_state:
        game_state = initialize_game()
        session['game_state'] = game_state
    return game_state

def find_starting_player(hands):
    for player, hand in hands.items():
        for card in hand:
            if card['suit'] == 'Clubs' and card['rank'] == '2':
                return player
    # If 2 of Clubs not found, find the lowest club
    lowest_club = None
    starting_player = None
    for player, hand in hands.items():
        for card in hand:
            if card["suit"] == 'Clubs':
                if (lowest_club is None) or (list(Card.RANK_VALUES.keys()).index(card.rank) < list(Card.RANK_VALUES.keys()).index(lowest_club['rank'])):
                    lowest_club = card
                    starting_player = player
    # If no clubs are dealt, default to 'Player'
    return starting_player if starting_player else player_names[0]

def blind_nil_allowed(player_name, game_state):
    player_team = get_player_team(player_name, game_state)
    opponent_team = get_opponent_team(player_team, game_state)
    player_team_score = game_state['scores'][player_team]
    opponent_team_score = game_state['scores'][opponent_team]
    delta = player_team_score - opponent_team_score
    # if the player's team is down by more than 100, return True
    return delta <= -100


def get_ai_blind_nil_choice(player_name, game_state):
    message = (f"Hi {player_name} it's your turn to bid! Before you see your cards you may choose to go bid Blind Nil. "
               f"Your team is down by more than 100 points. Going Blind Nil is a risky decision as scoring any trick "
               f"after bidding Nil would result in a severe penalty."
               f""
               f"Submit your choice in a JSON response. The example below shows a response for electing to not bid Blind Nil."
               f"EXAMPLE:  {{\n\"reasoning\": <text justification of your choice>\n\"bid_blind_nil\": False,\n}}")

    response = json.loads(assistant.chat(user_content=message, json_format=True))
    debug_print({
        "player_name": player_name,
        "reasoning": response["reasoning"],
        "bid_blind_nil": response["bid_blind_nil"]})
    bid_blind_nil = bool(response["bid_blind_nil"])
    return bid_blind_nil


def debug_print(param):
    print(json.dumps(param, indent=4))


def get_ai_bid(player_name, hand, game_state):
    if blind_nil_allowed(player_name, game_state):
        ai_bids_blind_nil = get_ai_blind_nil_choice(player_name, game_state)
        if ai_bids_blind_nil:
            game_state['nil_bids'][player_name] = 'Blind Nil'
            return 0

    message = (f"Hi {player_name} it's your turn to bid! How many tricks do you think you'll"
               f" win based on the cards in your hand? You may also bid : {hand}?"
               f"Game State: {game_state}"
               f"PLease respond in JSON format with an integer. Use 0 to bid Nil."
               f"EXAMPLE:  {{\n\"reasoning\": <text justification of your bid>\n\"bid\": 3,\n}}")
    response = json.loads(assistant.chat(user_content=message, json_format=True))
    debug_print({
        "player_name": player_name,
        "reasoning": response["reasoning"],
        "bid": response["bid"]})

    bid = response["bid"]

    if bid == 0:
        ['nil_bids'][player_name] = 'Nil'
        return 0
    return bid

# CPU bidding logic (updated for softer bids)
# def get_cpu_bid(player_name, hand, game_state):
#     bid = 0
#
#     # Evaluate the hand for high cards and Spades
#     for card in hand:
#         if card['rank'] == 'A':
#             bid += 1  # Aces are likely to win
#         elif card['rank'] == 'K':
#             bid += 0.8
#         elif card['rank'] == 'Q':
#             bid += 0.5
#         elif card['rank'] == 'J' or card['rank'] == '10':
#             bid += 0.3
#         if card['suit'] == 'Spades' and card['rank'] in ['A', 'K', 'Q']:
#             bid += 0.5  # Count high Spades more aggressively
#
#     # Round down bids to make CPU less aggressive
#     bid = int(bid)
#
#     # CPU logic for Nil or Blind Nil based on score
#     if bid == 0:
#         bid = 1  # Ensure CPU bids at least 1
#         if random.random() < 0.1 and blind_nil_allowed(player_name, game_state) <= -100:  # 10% chance to bid Nil
#             game_state['nil_bids'][player_name] = 'Nil'
#             return 0
#
#     if blind_nil_allowed(player_name, game_state) <= -100:
#         if random.random() < 0.02:  # 2% chance to bid Blind Nil
#             game_state['nil_bids'][player_name] = 'Blind Nil'
#             return 0
#
#     return bid


def get_ai_card_choice(player_name, hand, spades_broken, current_trick):
    message = (f"Hello {player_name}! It's your turn to play a card for this trick.\n"
               f"Current Trick: {current_trick}\n"
               f"Spades Broken: {spades_broken}\n"
               f"Your Hand: {hand}\n"
               f"Please select a card from your hand to play. Your response should be in JSON.\n"
               f"EXAMPLE: {{\n\"reasoning\": <text justification of your choice>\n\"card_played\": {{\"rank\": \"A\", \"suit\": \"Spades\"}}\n}}\n")

    response = json.loads(assistant.chat(user_content=message, json_format=True))
    debug_print({ "message": message, "response": {
        "player_name": player_name,
        "reasoning": response["reasoning"],
        "card_played": response["card_played"]}})

    return response["card_played"]


def ai_play_card(player_name, game_state):
    hand = game_state['hands'][player_name]
    current_trick = game_state['current_trick']
    spades_broken = game_state['spades_broken']

    if not current_trick:
        # if you're the first card to play, check for spades being broken
        if spades_broken:
            cards_you_can_play = hand
        else:
            cards_you_can_play = [card for card in hand if
                                  card['suit'] != 'Spades'] or hand  # if only spades are available
    else:
        # if a card has been played, check to see if you have that suit
        leading_suit = current_trick[0]['card']['suit']
        cards_you_can_play = [card for card in hand if card['suit'] == leading_suit] or hand
    debug_print({
        "player_name": player_name,
        "hand": hand,
        "cards_you_can_play": cards_you_can_play,
    })

    card_to_play = get_ai_card_choice(player_name, cards_you_can_play, spades_broken, current_trick)

    hand.remove(card_to_play)
    game_state['current_trick'].append({'player': player_name, 'card': card_to_play})

    if card_to_play['suit'] == 'Spades' and not spades_broken:
        game_state['spades_broken'] = True

    # Check if CPU has a Nil bid and took a trick
    if player_name in game_state['nil_bids'] and game_state['nil_bids'][player_name] in ['Nil', 'Blind Nil']:
        # Mark that the Nil bid failed
        if player_name not in game_state.get('failed_nil', []):
            game_state.setdefault('failed_nil', []).append(player_name)

# CPU player logic for playing a card (updated for spades breaking)
# def cpu_play_card(player_name, game_state):
#     hand = game_state['hands'][player_name]
#     current_trick = game_state['current_trick']
#     spades_broken = game_state['spades_broken']
#
#     # Implementing basic AI for card selection
#     if current_trick:
#         leading_suit = current_trick[0]['card']['suit']
#         same_suit_cards = [card for card in hand if card['suit'] == leading_suit]
#
#         if same_suit_cards:
#             card_to_play = same_suit_cards[0]  # Play lowest card of leading suit
#         else:
#             # Cannot follow suit
#             spades_cards = [card for card in hand if card['suit'] == 'Spades']
#             if spades_cards:
#                 # Play a Spade to break Spades if they haven't been broken
#                 card_to_play = min(spades_cards, key=lambda x: ranks.index(x['rank']))
#                 if not spades_broken:
#                     game_state['spades_broken'] = True
#             else:
#                 # No Spades; play lowest card
#                 card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
#     else:
#         # First player of the trick
#         if spades_broken:
#             card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
#         else:
#             non_spades = [card for card in hand if card['suit'] != 'Spades']
#             if non_spades:
#                 card_to_play = min(non_spades, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
#             else:
#                 # Only Spades left
#                 card_to_play = min(hand, key=lambda x: (suits.index(x['suit']), ranks.index(x['rank'])))
#
#     hand.remove(card_to_play)
#     game_state['current_trick'].append({'player': player_name, 'card': card_to_play})
#
#     if card_to_play['suit'] == 'Spades' and not spades_broken:
#         game_state['spades_broken'] = True
#
#     # Check if CPU has a Nil bid and took a trick
#     if player_name in game_state['nil_bids'] and game_state['nil_bids'][player_name] in ['Nil', 'Blind Nil']:
#         # Mark that the Nil bid failed
#         if player_name not in game_state.get('failed_nil', []):
#             game_state.setdefault('failed_nil', []).append(player_name)


# Determine the winner of a trick
def determine_winner(trick):
    leading_suit = trick[0]['card']['suit']
    winning_play = trick[0]
    for play in trick[1:]:
        card = play['card']
        if card["suit"] == winning_play['card']['suit']:
            if list(Card.RANK_VALUES.keys()).index(card["rank"]) > list(Card.RANK_VALUES.keys()).index(winning_play['card']['rank']):
                winning_play = play
        elif card['suit'] == 'Spades':
            if winning_play['card']['suit'] != 'Spades':
                winning_play = play
            elif list(Card.RANK_VALUES.keys()).index(card["rank"]) > list(Card.RANK_VALUES.keys()).index(winning_play['card']['rank']):
                winning_play = play
    return winning_play['player']

# Validate player's card selection
def validate_play(player_name, selected_card, game_state):
    player_hand = game_state['hands'][player_name]
    current_trick = game_state['current_trick']
    spades_broken = game_state['spades_broken']

    if player_name != game_state['current_player']:
        return "It's not your turn."

    if not current_trick:
        # First play of the trick
        if selected_card['suit'] == 'Spades' and not spades_broken:
            if all(card['suit'] == 'Spades' for card in player_hand):
                return None  # Only spades left, can play
            else:
                return "Cannot lead with Spades until they are broken."
    else:
        leading_suit = current_trick[0]['card']['suit']
        if selected_card['suit'] == leading_suit:
            return None  # Correct suit
        elif any(card['suit'] == leading_suit for card in player_hand):
            return f"You must follow suit: {leading_suit}."
        else:
            # Player cannot follow suit; they can play any card, including spades
            return None  # Valid play

    return None  # Valid play

# Calculate scores at the end of the round
def calculate_scores(game_state):
    # Check for Shooting the Moon (Boston)
    for team, players in game_state['teams'].items():
        if game_state['tricks_won'][team] == 13:
            # Team took all tricks
            game_state['scores'][team] += 250  # Boston bonus
            game_state['scores'][get_opponent_team(team, game_state)] -= 250
            return

    for team, players in game_state['teams'].items():
        bid = 0
        tricks = game_state['tricks_won'][team]

        # Calculate bids and penalties for Nil bids
        for player in players:
            player_bid = game_state['bids'][player]
            if player_bid == 0 and player in game_state['nil_bids']:
                # Handle Nil or Blind Nil
                if player in game_state.get('failed_nil', []):
                    # Nil bid failed
                    penalty = -100 if game_state['nil_bids'][player] == 'Nil' else -200
                    game_state['scores'][team] += penalty
                else:
                    # Nil bid succeeded
                    bonus = 100 if game_state['nil_bids'][player] == 'Nil' else 200
                    game_state['scores'][team] += bonus
            else:
                # Regular bid
                bid += player_bid

        # Calculate team score for regular bids
        if tricks >= bid:
            game_state['scores'][team] += 10 * bid + (tricks - bid)
        else:
            game_state['scores'][team] -= 10 * bid

def get_opponent_team(team, game_state):
    return 'Team A' if team == 'Team B' else 'Team B'

@app.route('/')
def index():
    # Initialize game state if not already done
    if 'game_state' not in session or session['game_state'].get('game_over', False):
        session['game_state'] = initialize_game()
    return redirect(url_for('start_game'))

@app.route('/start_game', methods=['GET', 'POST'])
def start_game():
    game_state = get_game_state()
    if request.method == 'POST':
        blind_nil_choice = request.form.get('blind_nil_choice')
        if blind_nil_choice == 'Yes':
            # Check if Blind Nil is allowed (team behind by 100 points)
            if blind_nil_allowed(player_names[0], game_state):
                game_state['bids'][player_names[0]] = 0
                game_state['nil_bids'][player_names[0]] = 'Blind Nil'
                # Deal cards without showing them
                game_state['hands'] = deal_cards()
                # CPUs make their bids in order
                process_bids(game_state)
                # Determine starting player
                game_state['current_player'] = find_starting_player(game_state['hands'])
                session['game_state'] = game_state
                return redirect(url_for('play_hand'))
            else:
                error = "Blind Nil can only be bid when your team is behind by 100 points."
                return render_template('start_game.html', game_state=game_state, error=error)
        else:
            # Proceed to regular bidding
            return redirect(url_for('bidding'))
    return render_template('start_game.html', game_state=game_state)

def process_bids(game_state):
    # Process bidding in order
    bidding_order = game_state['bidding_order']
    for bidder in bidding_order:
        if bidder == player_names[0] and player_names[0] in game_state['bids']:
            continue  # Player has already bid (in case of Blind Nil)
        elif bidder != player_names[0]:
            # CPU makes bid
            hand = game_state['hands'][bidder]
            bid = get_ai_bid(bidder, hand, game_state)
            game_state['bids'][bidder] = bid
            game_state['current_bids'][bidder] = bid  # Record current bid for display

@app.route('/bidding', methods=['GET', 'POST'])
def bidding():
    game_state = get_game_state()
    if 'hands' not in game_state or not game_state['hands']:
        # Deal cards
        game_state['hands'] = deal_cards()
        # Determine starting player
        game_state['current_player'] = find_starting_player(game_state['hands'])
        session['game_state'] = game_state

    if request.method == 'POST':
        bid_input = request.form['bid']
        if bid_input.lower() == 'nil':
            game_state['bids'][player_names[0]] = 0
            game_state['nil_bids'][player_names[0]] = 'Nil'
        else:
            try:
                bid = int(bid_input)
                if 0 <= bid <= 13:
                    game_state['bids'][player_names[0]] = bid
                else:
                    error = "Bid must be between 0 and 13."
                    return render_template('bidding.html', error=error, game_state=game_state)
            except ValueError:
                error = "Please enter a valid integer or 'Nil'."
                return render_template('bidding.html', error=error, game_state=game_state)

        # Process remaining bids
        process_bids(game_state)

        session['game_state'] = game_state
        return redirect(url_for('play_hand'))

    # Prepare data for bidding template
    bidder = game_state['bidding_order'][game_state['current_bidder_index']]
    bids_so_far = {player: game_state['bids'].get(player, None) for player in
                   game_state['bidding_order'][:game_state['current_bidder_index']]}
    game_state['current_bids'] = bids_so_far
    return render_template('bidding.html', game_state=game_state, bidder=bidder)

def reset_game_state_for_new_round(game_state):
    game_state.update({
        'hands': {},
        'bids': {},
        'nil_bids': {},
        'failed_nil': [],
        'tricks_won': {'Team A': 0, 'Team B': 0},
        'current_trick': [],
        'current_player': None,
        'trick_number': 1,
        'spades_broken': False,
        'round_over': False,
        'previous_trick': [],
        'previous_trick_winner': None,
        'current_bids': {},
        'current_bidder_index': 0,
        'bidding_order': player_names,
        'round_number': game_state['round_number'] + 1,
    })


@app.route('/play_hand', methods=['GET', 'POST'])
def play_hand():
    game_state = get_game_state()
    if not game_state:
        return redirect(url_for('index'))

    if game_state.get('round_over', False):
        # Check for game over condition
        winning_score = 500
        for team, score in game_state['scores'].items():
            if score >= winning_score:
                game_state['game_over'] = True
                game_state['winning_team'] = team
                session['game_state'] = game_state
                return redirect(url_for('game_over'))
        # Reset for next round
        reset_game_state_for_new_round(game_state)
        session['game_state'] = game_state
        return redirect(url_for('start_game'))

    error = None

    if request.method == 'POST':
        current_player = game_state['current_player']

        if current_player == player_names[0]:
            # Player's turn
            card_index = int(request.form['card_index'])
            player_hand = game_state['hands'][player_names[0]]
            selected_card = player_hand[card_index]

            # Validate play
            error = validate_play(player_names[0], selected_card, game_state)
            if error:
                return render_template('play_hand.html', game_state=game_state, error=error)

            # Play the card
            player_hand.pop(card_index)
            game_state['current_trick'].append({'player': player_names[0], 'card': selected_card})

            if selected_card['suit'] == 'Spades' and not game_state['spades_broken']:
                game_state['spades_broken'] = True

            # Move to next player
            game_state['current_player'] = get_next_player(current_player)

        else:
            # CPU's turn
            ai_play_card(current_player, game_state)
            # Move to next player
            game_state['current_player'] = get_next_player(current_player)

        # Check if trick is complete
        if len(game_state['current_trick']) == 4:
            # Determine winner of the trick
            winner = determine_winner(game_state['current_trick'])
            team = get_player_team(winner, game_state)
            game_state['tricks_won'][team] += 1
            game_state['previous_trick_winner'] = winner
            game_state['previous_trick'] = list(game_state['current_trick'])

            # Check for Nil bid failures
            for play in game_state['current_trick']:
                player = play['player']
                if player in game_state['nil_bids'] and game_state['nil_bids'][player] in ['Nil', 'Blind Nil']:
                    if winner == player and player not in game_state.get('failed_nil', []):
                        game_state.setdefault('failed_nil', []).append(player)

            # Reset current trick
            game_state['current_trick'] = []
            game_state['current_player'] = winner  # Winner starts next trick
            game_state['trick_number'] += 1

            # Check if the round is over
            if game_state['trick_number'] > 13:
                game_state['round_over'] = True
                calculate_scores(game_state)
                session['game_state'] = game_state
                return redirect(url_for('play_hand'))

        session['game_state'] = game_state

        # If next player is not 'Player', process AI moves
        if game_state['current_player'] != player_names[0] and not game_state.get('round_over', False):
            return redirect(url_for('play_hand'))

    # Render the play hand template
    return render_template('play_hand.html', game_state=game_state, error=error)

def get_next_player(current_player):
    player_order = player_names
    index = player_order.index(current_player)
    next_index = (index + 1) % 4
    return player_order[next_index]

def get_player_team(player, game_state):
    for team, players in game_state['teams'].items():
        if player in players:
            return team
    return None

@app.route('/game_over')
def game_over():
    game_state = get_game_state()
    return render_template('game_over.html', game_state=game_state)

if __name__ == '__main__':
    app.run(debug=True)
