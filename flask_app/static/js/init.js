// Initialize socket connection
var socket = io('http://localhost:5000', {
    transports: ['websocket'],
    debug: true
});
// Verify gameId has been set and join the room
if(gameId === undefined){
    window.location.href = '/';
}
else {
    socket.emit('join_game', gameId);
}

// Listen for game state updates from the server
socket.on('update_game_state', function(data) {
    // let gameState = data['game_state'];
    console.log('Game state update received:', data);
    updateGameState(data);
});

// Listen for AI action completion and reload the game page
socket.on('ai_action_complete', function(action) {
    console.log('AI action complete:', action);
    window.location.reload(); // Reload the current page
});

function updateCommunityCards(communityCards) {
    const container = document.getElementById('community-cards');
    container.innerHTML = communityCards.map(card => `
        <span class="card large ${card['suit'].toLowerCase()}">
            ${card['rank']} ${getSuitSymbol(card['suit'])}
        </span>
    `).join('');
}

function getSuitSymbol(suit) {
    switch (suit) {
        case 'Hearts':
            return '♥';
        case 'Diamonds':
            return '♦';
        case 'Clubs':
            return '♣';
        case 'Spades':
            return '♠';
        default:
            return '';
    }
}

function updatePot(potData) {
    const potTotal = potData['total']
    const highestBet = potData['highest_bet']
    document.getElementById('pot').innerHTML = `Pot: $${potTotal} | Min: $${highestBet}`;
}

// Function to update the game state on the UI
/*

*/
function updateGameState(data) {
    const gameState = data['game_state'];
    const gameStatePlayers = gameState['players'];
    const communityCards = Array.from(gameState['community_cards']);
    const currentPot = gameState['pot'];

    updateCommunityCards(communityCards);
    updatePot(currentPot)
    updatePlayerState(gameStatePlayers);
}

// Function to handle player actions
function playerAction(action) {
    console.log(`Player action selected: ${action}`);

    fetch(`/action/${gameId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ action: action })
    })
    .then(response => {
        const contentType = response.headers.get("content-type");
        if (contentType && contentType.indexOf("application/json") !== -1) {
            return response.json();
        } else {
            throw new Error("Expected JSON response but got HTML or other content type");
        }
    })
    .then(data => {
        if (data.redirect) {
            window.location.href = data.redirect; // Navigate to updated game view
        } else {
            console.error('Server error:', data.error);
        }
    })
    .catch(error => {
        console.error('Network error:', error);
    });
}

// Function to update the player state on the UI
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
        playerMoney.textContent = `$${player.stack}`;

        let playerCardsContainer = document.createElement('div');
        playerCardsContainer.id = `cards-player-${index + 1}`;
        playerCardsContainer.classList.add('player-cards');

        player.hand.forEach(card => {
            let cardSpan = document.createElement('span');
            cardSpan.classList.add('card');
            if (card.suit === 'Hearts') cardSpan.classList.add('hearts');
            if (card.suit === 'Diamonds') cardSpan.classList.add('diamonds');
            if (card.suit === 'Clubs') cardSpan.classList.add('clubs');
            if (card.suit === 'Spades') cardSpan.classList.add('spades');
            cardSpan.textContent = `${card.rank} ${getSuitSymbol(card['suit'])}`;
            playerCardsContainer.appendChild(cardSpan);
        });

        playerCard.appendChild(playerHeadshot);
        playerCard.appendChild(playerName);
        playerCard.appendChild(playerMoney);
        playerCard.appendChild(playerCardsContainer);

        playersContainer.appendChild(playerCard);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM fully loaded and parsed');
    // ELEMENTS THAT MAY NOT EXIST ON THE PAGE YET //
    document.body.addEventListener('click', (event) => {
        if (event.target && event.target.id === 'begin-round-button') {
            console.log("Begin Round button clicked.");
            const modal = document.getElementById('game-initialized-modal');
            if (modal) {
                modal.style.display = 'none';
            }
            fetch(`/game/${gameId}`, { method: 'GET' })
                .then(response => {
                    const contentType = response.headers.get("content-type");
                    if (contentType && contentType.indexOf("application/json") !== -1) {
                        return response.json();
                    } else {
                        throw new Error("Expected JSON response but got HTML or other content type");
                    }
                })
                .then(data => {
                    updateGameState(data);
                })
                .catch(error => {
                    console.error('Network error:', error);
                });
        } else if (event.target && event.target.id === 'quit-button') {
            console.log("Quit button clicked.");
            const modal = document.getElementById('game-initialized-modal');
            if (modal) {
                modal.style.display = 'none';
            }
            window.location.href = '/';
        } else if (event.target && event.target.id === 'raise-button') {
            console.log("Raise button clicked");
            // Betting Elements
            const raiseButton = document.getElementById('raise-button');
            const betSliderContainer = document.getElementById('bet-slider-container');
            const betSlider = document.getElementById('bet-slider');
            const betAmount = document.getElementById('bet-amount');
            const submitRaiseButton = document.getElementById('bet-submit-button');
            const doubleBetButton = document.getElementById('double-bet-amount-button')

            // Event listener for Raise
            console.log('Raise button clicked');
            betSliderContainer.classList.toggle('bet-slider-container-expanded');

            const buttonRect = raiseButton.getBoundingClientRect();
            const centerX = buttonRect.left + (buttonRect.width / 2);
            const containerWidth = betSliderContainer.offsetWidth;

            betSliderContainer.style.left = `${centerX - (containerWidth * 2)}px`;

            // Event listener for slider and input synchronization
            betSlider.addEventListener('input', () => {
                betAmount.value = betSlider.value;
            });

            betAmount.addEventListener('input', () => {
                if (betAmount.value >= betSlider.min && betAmount.value <= betSlider.max) {
                    betSlider.value = betAmount.value;
                } else if (betAmount.value < betSlider.min) {
                    betAmount.value = betSlider.min;
                } else {
                    betAmount.value = betSlider.max;
                }
            });

            doubleBetButton.addEventListener('click', () => {
                betAmount.value = betAmount.value * 2;
            })

            // Event listener for submitting a raise
            submitRaiseButton.addEventListener('click', async () => {
                console.log('Bet submit button clicked');
                const amount = betAmount.value;

                try {
                    const response = await fetch(`/action/${gameId}`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ action: 'raise', amount: amount })
                    });

                    if (!response.ok) {
                        throw new Error('Network response was not ok');
                    }

                    const result = await response.json();
                    console.log('Success:', result);

                    // Reload the game page to force the next action. Not ideal implementation.
                    fetch(`/game/${gameId}`, { method: 'GET' })
                    .then(response => {
                        const contentType = response.headers.get("content-type");
                        if (contentType && contentType.indexOf("application/json") !== -1) {
                            return response.json();
                        } else {
                            throw new Error("Expected JSON response but got HTML or other content type");
                        }
                    })
                    .then(data => {
                        updateGameState(data);
                    })
                    .catch(error => {
                        console.error('Network error:', error);
                    });

                } catch (error) {
                    console.error('Error:', error);
                }

                // Collapse the slider container after submission
                betSliderContainer.classList.add('bet-slider-container-collapsed');
                setTimeout(() => {
                    betSliderContainer.style.display = 'none';
                }, 500);
            });
        }
    })
});
