from flask import Flask, request, jsonify, session
from poker import PokerGame, create_random_game
from player import Player, AIPlayer
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = 'nXuFwPpLqTA6kj2Vi6eA'
CORS(app)


@app.route('/api/new_game', methods=['POST'])
def new_game():
    # Get the list of player names from the request body
    player_names = request.json.get('players')        
    
    # TODO: check the length of the input against max players allowed
    # Create and start a new game with these players
    game = create_random_game(player_name="Jeff", ai_players=player_names)
    game.run_game()

    # Return a success message and the initial game state
    return jsonify(message="New game started", game_state=game.game_state()), 200


@app.route('/api/make_move', methods=['POST'])
# TODO fix the game thing below by figuring out how to save and pass the game state
def make_move():
    while game.is_current_player_ai():
        ai_move = game.current_player.generate_move(game)
        game.make_move(ai_move)

    # Get the player's move from the request
    player_move = request.json.get('move')
    bet_amount = request.json.get('bet_amount', 0)

    # Get the current game state from the session
    game_state = session.get('game_state')

    # Make the move and update the game state
    player_action, add_to_pot = game_state['current_player'].action(game_state, player_move, bet_amount)

    # Update the game state based on the move
    game_state = update_game_state(game_state, player_action, add_to_pot)  # replace with actual function

    # Store the updated game state in the session
    session['game_state'] = game_state

    # Return the updated game state to the client
    return jsonify(game_state=game_state), 200


@app.route('/api/game_state', methods=['GET'])
def game_state():
    # TODO: Get the current game state
    return jsonify(message="Game state"), 200


@app.route('/set_data')
def set_data():
    session['key'] = 'value'
    return 'Data set'


@app.route('/get_data')
def get_data():
    value = session.get('key')
    return f'Data: {value}'


if __name__ == '__main__':
    app.run(port=3001, debug=True)
    