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
    document.getElementById('pot').innerHTML = `Pot: $${gameState.current_pot.total_pot} | Min: ${gameState.small_blind}`;
    document.getElementById('player-options').innerHTML = `${gameState.players[0].options}`;
    document.getElementById('players').innerHTML = ``
}

function updatePlayerState(playerState) {
    let playersContainer = document.getElementById('players');
    playersContainer.innerHTML = ''; // Clear existing content

    playerState.forEach((player, index) => {
        let playerCard = document.createElement('div');
        playerCard.classList.add('player-card');
        playerCard.id = `player-${index + 1}`;

        let playerHeadshot = document.createElement('img');
        playerHeadshot.src = '/static/images/kanye.jpg';
        playerHeadshot.classList.add('player-headshot');
        playerHeadshot.alt = 'Player picture';

        let playerName = document.createElement('h2');
        playerName.textContent = player.name;

        let playerMoney = document.createElement('p');
        playerMoney.textContent = `$${player.money}`;

        let playerCardsContainer = document.createElement('div');
        playerCardsContainer.id = `cards-player-${index + 1}`;
        playerCardsContainer.classList.add('player-cards');

        player.cards.forEach(card => {
            let cardSpan = document.createElement('span');
            cardSpan.classList.add('card');
            if (card.suit_symbol == '♥') cardSpan.classList.add('hearts');
            if (card.suit_symbol == '♦') cardSpan.classList.add('diamonds');
            if (card.suit_symbol == '♣') cardSpan.classList.add('clubs');
            if (card.suit_symbol == '♠') cardSpan.classList.add('spades');
            cardSpan.textContent = `${card.rank} ${card.suit_symbol}`;
            playerCardsContainer.appendChild(cardSpan);
        });

        playerCard.appendChild(playerHeadshot);
        playerCard.appendChild(playerName);
        playerCard.appendChild(playerMoney);
        playerCard.appendChild(playerCardsContainer);

        playersContainer.appendChild(playerCard);
    });
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