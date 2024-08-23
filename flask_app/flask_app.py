# flask_app/flask_app.py
from flask import Flask, render_template, session, request, redirect, jsonify
from flask_socketio import SocketIO, emit
from flask_session import Session

import random

from core.game import Interface
from core.poker_game import (PokerGame)
from core.poker_hand import PokerHand
from core.poker_action import PokerAction
from core.poker_player import PokerPlayer, AIPokerPlayer
from core.utils import get_players, shift_list_left, obj_to_dict

from dotenv import load_dotenv

class FlaskInterface(Interface):
    @staticmethod
    def display_game(g: PokerGame):
        player_dict_list = []
        for player in g.players:
            player_dict_list.append(player.to_dict())

        community_cards_dict_list = []
        if len(g.hands) > 0:
            for card in g.hands[-1].community_cards:
                community_cards_dict_list.append(card.to_dict())

        player_options = []
        for player in g.remaining_players:      # TODO: update this to show all players and indicate if in hand still
            if isinstance(player, PokerPlayer):
                player_options = player.options
                break

        return render_template(
            template_name_or_list='poker_game.html',
            players=player_dict_list,
            community_cards=community_cards_dict_list ,
            player_options=player_options
        )

    def display_hand(self, hand):
        pass

    def display_player_hand(self, player, hand):
        pass

    def get_user_action(self, player):
        pass

    def display_player(self, winner):
        pass

    def display_poker_action(self, action):
        pass

app = Flask(__name__,
            template_folder='./templates',
            static_folder='./static')
app.config['SECRET_KEY'] = 'my_secret_key_poker_app'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)
socketio = SocketIO(app, manage_session=False)

@app.route(rule='/', methods=['GET'])
def index():
    if "game_state" not in session:
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
        template_name_or_list='home.html',
    )


@app.route('/game', methods=['GET'])
def game():
    return render_template(
        template_name_or_list='poker_game.html',
    )


@app.route('/start-game', methods=['POST'])
def start_game():
    if "game_state" in session:
        game_state = session.get('game_state')
    else:
        game_state = initialize_game_state()
    session['game_state'] = game_state
    socketio.emit('update_game_state', session['game_state'])
    return jsonify(session['game_state'])


@socketio.on('player_action')
def handle_player_action(data):
    game_state = session.get('game_state', initialize_game_state())
    # data = request.get_json()
    action = data.get('action')

    if game_state["current_player"]["type"] == "PokerPlayer":
        game_state = process_player_action(game_state, action)
        session['game_state'] = game_state
        socketio.emit('update_game_state', game_state)
        if game_state["current_player"]["type"] == "AIPokerPlayer":
            handle_ai_turns()


# TODO: update these messages interactions to use socketio for real time back/forth
@app.route('/messages', methods=['GET'])
def get_messages():
    return {'message 1': 'this is the message'}
    # return jsonify(game['messages'])


@app.route('/messages', methods=['POST'])
def add_message():
    new_message = request.json.get('message')
    if new_message:
        game['messages'].append(new_message)
        return jsonify({"status": "success"}), 201
    return jsonify({"status": "error"}), 400


def initialize_game_state():
    poker_players = get_players(test=False, num_players=2)
    poker_game = PokerGame(poker_players)

    poker_hand = PokerHand(players=poker_game.players,
                           dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
                           deck=poker_game.deck)
    poker_game.hands.append(poker_hand)
    poker_hand.setup_hand()
    return jsonify(obj_to_dict(poker_game))


def process_player_action(game_state, action):
    # get poker_hand
    poker_hand_dict = game_state['hands'][-1]
    poker_hand = PokerHand.from_dict(poker_hand_dict)
    player_action = PokerAction.from_dict(action)
    poker_hand.process_player_action(player=game_state['current_player'],
                                     poker_action=player_action)
    game_state['hands'][-1] = poker_hand.to_dict()
    return game_state


if __name__ == '__main__':
    app.run(debug=True)
