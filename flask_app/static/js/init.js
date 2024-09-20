let socket = io();

// Function to initialize the game
function startGame() {
    fetch('/start-game', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            updateGameState(data);
        });
}

/* TODO: update this to reference actual elements or switch to Jinja */
// Function to update the UI based on the game state
function updateGameState(gameState) {
    document.getElementById('community-cards').innerHTML = JSON.stringify(gameState.community_cards);
    document.getElementById('pot').innerHTML = `Pot: ${gameState.current_pot}`;
}

// Listen for game state updates from the server
socket.on('update_game_state', function(gameState) {
    updateGameState(gameState);
});

// Function to send user move to the server
function sendUserMove(move) {
   socket.emit('user_move', { move: move });
}

// Initialize game when the page loads
window.onload = function() {
   startGame();
}