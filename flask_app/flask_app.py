# flask_app/app.py
from flask import Flask, render_template, session, request, redirect, jsonify
import random

from core.cards import card_to_dict
from core.game import Interface, ConsoleInterface
from core.poker import (PokerGame,
                        PokerAction,
                        PokerHand,
                        PokerPlayer,
                        AIPokerPlayer,
                        get_players,
                        shift_list_left)

from dotenv import load_dotenv

# class FlaskInterface(Interface):
#     @staticmethod
#     def display_game(g: PokerGame):
#         player_dict_list = []
#         for player in g.players:
#             player_dict_list.append(player.to_dict())
#
#         community_cards_dict_list = []
#         if len(g.hands) > 0:
#             for card in g.hands[-1].community_cards:
#                 community_cards_dict_list.append(card_to_dict(card))
#
#         player_options = []
#         for player in g.remaining_players:      # TODO: update this to show all players and indicate if in hand still
#             if isinstance(player, PokerPlayer):
#                 player_options = player.options
#                 break
#
#         return render_template(
#             template_name_or_list='poker_game.html',
#             players=player_dict_list,
#             community_cards=community_cards_dict_list ,
#             player_options=player_options
#         )
#
#     def display_hand(self, hand):
#         pass
#
#     def display_player_hand(self, player, hand):
#         pass
#
#     def get_user_action(self, player):
#         pass
#
#     def display_player(self, winner):
#         pass
#
#     def display_poker_action(self, action):
#         pass

# def poker_game_from_dict(poker_game_dict: dict):
#     players = player_list_from_dict(poker_game_dict["players"])
#     interface = Interface.from_dict(poker_game_dict["interface"])
#
#     poker_game = PokerGame(players, interface)
#     poker_game.starting_players = player_list_from_dict(poker_game_dict["starting_players"])
#     poker_game.remaining_players = player_list_from_dict(poker_game_dict["remaining_players"])
#     poker_game.deck = deck_from_dict(poker_game_dict["deck"])
#     poker_game.hands = hand_list_from_dict(poker_game_dict["hands"])
#     return poker_game

app = Flask(__name__,
            template_folder='./templates',
            static_folder='./static')

app.secret_key = 'my_secret_key_poker_app'

@app.route(rule='/', methods=['GET'])
def index():
    if "game" not in session:
        return redirect(
            location='/home',
            code=302
        )
    else:
        return redirect(
            location='/game',
            code=302
        )

@app.route(rule='/home', methods=['GET'])
def home():
    return render_template(
        template_name_or_list='index.html',
    )

@app.route('/game', methods=['GET'])
def game():
    poker_players = get_players(test=False, num_players=2)
    poker_game = PokerGame(poker_players, ConsoleInterface())

    poker_hand = PokerHand(interface=poker_game.interface,
                           players=poker_game.players,
                           dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
                           deck=poker_game.deck)
    poker_game.hands.append(poker_hand)
    player_queue = poker_hand.setup_hand()
    poker_hand.betting_round(player_queue)
    poker_hand.reveal_flop()

    player_dict_list = []
    for player in poker_game.players:
        player_dict_list.append(player.to_dict())

    community_cards_dict_list = []
    if len(poker_game.hands) > 0:
        for card in poker_game.hands[-1].community_cards:
            community_cards_dict_list.append(card_to_dict(card))

    player_options = []
    for player in poker_game.remaining_players:  # TODO: update this to show all players and indicate if in hand still
        if isinstance(player, PokerPlayer):
            player_options = player.options
            break

    return render_template(
        template_name_or_list='poker_game.html',
        players=player_dict_list,
        community_cards=community_cards_dict_list,
        player_options=player_options
    )

# @app.route('/api/start_game', methods=['POST'])
# def start_game():
#     poker_players = get_players(test=False, num_players=2)
#     poker_game = PokerGame(poker_players, FlaskInterface())
#     # if 'game' not in session:
#     #     poker_players = get_players(test=False, num_players=2)
#     #     poker_game = PokerGame(poker_players, FlaskInterface())
#     #     session['game'] = poker_game.to_dict()
#     #
#     # if not poker_game:
#     #     # poker_game = poker_game_from_dict(poker_game_dict=session['game'])
#     #     pass
#
#     poker_game.interface.display_game(game=poker_game)
#
#     poker_hand = PokerHand(interface=poker_game.interface,
#                            players=poker_game.players,
#                            dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
#                            deck=poker_game.deck)
#     poker_game.hands.append(poker_hand)
#
#     poker_game.remaining_players, session['dealer'] = poker_hand.play_hand()
#
#     player_dict_list = []
#     for player in poker_game.players:
#         player_dict_list.append(player.to_dict())
#
#     community_cards_dict_list = []
#     for card in poker_game.hands[-1].community_cards:
#         community_cards_dict_list.append(card_to_dict(card))
#
#     player_options = []
#     for player in poker_game.remaining_players:
#         if isinstance(player, PokerPlayer):
#             player_options = player.options
#             break
#
#     return render_template(
#         template_name_or_list='html_poker_game.html',
#         players=player_dict_list,
#         community_cards=poker_game.hands[-1].community_cards,
#         player_options=player_options
#     )


@app.route('/api/player_action', methods=['POST'])
def player_action():
    data = request.get_json()
    action = data.get('action')
    poker_game = session['game']
    amount = int(request.form['amount'])
    poker_hand = poker_game.hands[-1]
    hand_state = poker_hand.state
    players_poker_action = PokerAction(player=poker_game.players[0],   # TODO: replace with correct player logic
                                        action=action,
                                        amount=amount,
                                        hand_state=hand_state)
    poker_hand.process_player_action(player=players_poker_action.player,
                                     player_action=players_poker_action)
    session['game'] = poker_game  # Save updated game state back to session

    return poker_game.interface.display_game(g=poker_game)       # TODO: update this to not have to pass the game


@app.route('/messages', methods=['GET'])
def get_messages():
    return jsonify(
        [
            {
                "sender": "Jeff",
                "content": "hello!",
                "timestamp": "11:23 Aug 25 2024",
                "message_type": "user"
            },
            {
                "sender": "Kanye West",
                "content": "the way to the truth is through my hands",
                "timestamp": "11:25 Aug 25 2024",
                "message_type": "ai"
            },
            {
                "sender": "table",
                "content": "The flop has been dealt",
                "timestamp": "11:26 Aug 25 2024",
                "message_type": "table"
            },
            {
                "sender": "Jeff",
                "content": "I'm not sure how to respond to that Kanye, but can you share your dealers number with me?",
                "timestamp": "11:27 Aug 25 2024",
                "message_type": "user"
            }
        ]
    )
    # return jsonify(game['messages'])


@app.route('/messages', methods=['POST'])
def add_message():
    new_message = request.json.get('message')
    if new_message:
        # Add the message to the games messages and trigger actions
        # game['messages'].append(new_message)
        return jsonify({"status": "success"}), 201
    return jsonify({"status": "error"}), 400


if __name__ == '__main__':
    app.run(debug=True)