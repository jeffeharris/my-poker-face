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
    return jsonify(message="New game started", game_state=game.get_state()), 200


@app.route('/api/make_move', methods=['POST'])
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
    
#     The game currently operates in a synchronous manner, running all the way through from start to finish. In a web-based context, this would need to change to a more asynchronous approach, where each player's turn is a separate event that triggers the next piece of game logic.
# 
# Here's a high-level outline of the changes you'd need to make:
# 
#         Start a new hand: Modify the play_hand method to only deal the hole cards and post the blinds, then determine the first player whose turn it is. If this player is an AI player, make the AI player's move, then determine the next player whose turn it is. Repeat this until it's a human player's turn, then stop and return the current game state.
# 
# Handle a player's move: Create a new method (let's call it handle_move) that takes as arguments the player making the move and the move they're making. This method should process the player's move, then determine the next player whose turn it is. If this player is an AI player, make the AI player's move, then determine the next player whose turn it is. Repeat this until it's a human player's turn, then stop and return the current game state.
# 
# End a betting round: Modify the betting_round method to end the betting round once all players have had their turn, then determine what to do next. If it's time to reveal the flop, turn, or river, do that, then determine the next player whose turn it is. Repeat the logic from the handle_move method until it's a human player's turn, then stop and return the current game state.
# 
# End a hand: Modify the end_hand method to determine the winner and end the hand once all betting rounds are complete. This method should return the final game state, including the winner and the final state of the pot.
# 
#                                                                                                                                                                                                                       You'll need to modify your Flask server to call these methods when it receives a request from the front-end. For example, when the /api/new_game endpoint is hit, it should call play_hand. When the /api/make_move endpoint is hit, it should call handle_move.
