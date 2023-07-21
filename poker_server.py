from flask import Flask, request, jsonify
from poker import PokerGame, create_random_game
from player import Player, AIPlayer

app = Flask(__name__)


@app.route('/api/new_game', methods=['POST'])
def new_game():
    # Get the list of player names from the request body
    player_names = request.json.get('players')        
    
    # TODO: check the length of the input against max players allowed
    # Create and start a new game with these players
    game = create_random_game(player_name="Jeff", ai_players=player_names)
    game.run_game()

    # Return a success message and the initial game state
    return jsonify(message="New game started", game_state=game.get_state()), 200


@app.route('/api/make_move', methods=['POST'])
def make_move():
    # TODO: Make a move
    # player_move = request.json.get('move')
    return jsonify(message="Move made"), 200


@app.route('/api/game_state', methods=['GET'])
def game_state():
    # TODO: Get the current game state
    return jsonify(message="Game state"), 200


if __name__ == '__main__':
    app.run(port=3001, debug=True)
    