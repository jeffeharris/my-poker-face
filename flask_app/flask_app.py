# flask_app/app.py
from flask import Flask, render_template
import random
# from core.game import FlaskInterface
from core.poker import (PokerGame,
                        PokerAction,
                        PokerHand,
                        PokerPlayer,
                        AIPokerPlayer,
                        get_players,
                        shift_list_left)

from dotenv import load_dotenv

app = Flask(__name__)


@app.route('/')
def home():
    pg= False
    if 'game' not in session:
        poker_players = get_players(test=False, num_players=2)
        poker_game = PokerGame(poker_players, FlaskInterface())
        session['game'] = poker_game.to_dict()

    if not poker_game:
        poker_game = poker_game_from_dict(poker_game_dict=session['game'])
    poker_hand = PokerHand(interface=poker_game.interface,
                           players=poker_game.players,
                           dealer=poker_game.players[random.randint(0, len(poker_game.players) - 1)],
                           deck=poker_game.deck)
    poker_game.hands.append(poker_hand)
    player_names = []
    for player in poker_game.players:
        player_names.append(player.name)
    # FlaskInterface.display_game(poker_game)
    return render_template(
        template_name_or_list='index.html',
        players=player_names
    )


@app.route('/player_action', methods=['POST'])
def bet():
    poker_game = session['game']
    action = request.form['action']
    amount = int(request.form['amount'])
    poker_hand = poker_game.hands[-1]
    hand_state = poker_hand.state
    player_action = PokerAction(player=poker_game.players[0],
                                action=action,
                                amount=amount,
                                hand_state=hand_state)
    poker_hand.process_player_action(player=player_action.player,
                                     player_action=player_action)
    session['game'] = poker_game  # Save updated game state back to session
    player_state = {}
    for player in poker_game.players:
        player_state[player.name] = player.money
    return render_template(
        'index.html',
        players=player_state
    )


if __name__ == '__main__':
    app.run(debug=True)