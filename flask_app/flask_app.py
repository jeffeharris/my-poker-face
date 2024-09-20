# flask_app/flask_app.py
from flask import Flask, render_template, session, request, redirect, jsonify
from flask_socketio import SocketIO
from flask_session import Session

from poker.poker_game import (PokerGame)
from poker.poker_hand import PokerHand
from poker.poker_action import PokerAction
from poker.utils import get_ai_players, obj_to_dict

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
    poker_players = get_ai_players(num_players=2)
    # poker_game = PokerGame(poker_players, ConsoleInterface())
    poker_game = PokerGame()
    poker_game.round_manager.add_players(poker_players)
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
    socketio.emit('update_game_state', game_state)
    return jsonify(game_state)


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


# TODO: <FEATURE> update these messages interactions to use socketio for real time back/forth
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
        game['messages'].append(new_message)
        return jsonify({"status": "success"}), 201
    return jsonify({"status": "error"}), 400


def initialize_game_state():
    human_player_names = ["Jeff"]
    ai_player_names = get_ai_players(num_players=2)

    poker_game = PokerGame()
    poker_game.round_manager.add_players(human_player_names, ai=False)
    poker_game.round_manager.add_players(ai_player_names, ai=True)
    poker_game.round_manager.initialize_players()
    poker_game.round_manager.deck.shuffle()

    ph = PokerHand()
    ph.pots[0].initialize_pot([p.name for p in poker_game.round_manager.remaining_players])
    # Start a loop here
    poker_game.hands.append(ph)
    poker_game.round_manager.setup_hand(ph.pots[0], ph.current_phase)
    return poker_game.game_state


def process_player_action(game_state, action):
    # get ph
    poker_hand_dict = game_state['hands'][-1]
    poker_hand = PokerHand.from_dict(poker_hand_dict)
    player_action = PokerAction.from_dict(action)
    poker_hand.process_player_action(player=game_state['current_player'],
                                     poker_action=player_action)
    game_state['hands'][-1] = poker_hand.to_dict()
    return game_state


if __name__ == '__main__':
    app.run(debug=True)
