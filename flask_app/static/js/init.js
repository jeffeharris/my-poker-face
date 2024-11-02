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
});

socket.on('player_turn_start', function(data) {
    console.log('Player turn:', data);
    const playerOptionsComponent = document.getElementById('player-options');
    updatePlayerOptions(data['current_player_options'], data['cost_to_call']);
    playerOptionsComponent.classList.add('player-options', 'content');
    playerOptionsComponent.classList.remove('bet-slider-container-collapsed');
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

function updatePlayerOptions(playerOptions, costToCall) {
    const playerOptionsContainer = document.getElementById('player-options');
    playerOptionsContainer.innerHTML = ''
    console.log('Player options:', playerOptions);
    // For each option, create the associated button
    playerOptions.forEach(option => {
        const button = document.createElement('button');
        button.id = `${option}-button`;
        if (option === 'call') {
            button.textContent = `${option} $${costToCall}`;
        } else {
            button.textContent = option;
        }
        if (option === 'raise') {
            const betSliderContainer = document.createElement('div');
            betSliderContainer.id = 'bet-slider-container';
            betSliderContainer.classList.add('bet-slider-container','bet-slider-container-collapsed');

            const betSlider = document.createElement('input');
            betSlider.id = 'bet-slider';
            betSlider.type = 'range';
            betSlider.min = "50";
            betSlider.max = "10000";
            betSlider.step = "25";
            betSlider.value = "200";
            betSlider.classList.add('bet-slider');

            const betAmount = document.createElement('input');
            betAmount.id = 'bet-amount';
            betAmount.type = 'number';
            betAmount.min = "50";
            betAmount.max = "10000";
            betAmount.step = "25";
            betAmount.value = "200";
            betAmount.classList.add('bet-amount');

            const buttonDiv = document.createElement('div');
            buttonDiv.classList.add('bet-slider-button-container');

            const submitRaiseButton = document.createElement('button');
            submitRaiseButton.id = 'bet-submit-button';
            submitRaiseButton.textContent = 'Submit';
            submitRaiseButton.classList.add('bet-submit-button');

            const doubleBetButton = document.createElement('button');
            doubleBetButton.id = 'double-bet-amount-button';
            doubleBetButton.textContent = 'X2';

            buttonDiv.appendChild(submitRaiseButton);
            buttonDiv.appendChild(doubleBetButton);

            betSliderContainer.appendChild(betSlider);
            betSliderContainer.appendChild(betAmount);
            betSliderContainer.appendChild(buttonDiv);
            playerOptionsContainer.appendChild(betSliderContainer);
        }
        button.classList.add('player-option');
        playerOptionsContainer.appendChild(button);
    })
}

// Function to update the game state on the UI
function updateGameState(data) {
    const gameState = data['game_state'];
    const gameStatePlayers = gameState['players'];
    const communityCards = Array.from(gameState['community_cards']);
    const currentPot = gameState['pot'];
    const currentPlayer = gameStatePlayers[gameState['current_player_idx']];
    const playerOptions = gameState['current_player_options'];
    const costToCall = currentPot['highest_bet'] - currentPlayer['bet'];

    updateCommunityCards(communityCards);
    updatePot(currentPot)
    updatePlayerInfo(gameStatePlayers, gameStatePlayers[gameState['current_player_idx']]['name']);
    updatePlayerOptions(playerOptions, costToCall);
}

// Function to handle player actions
function playerAction(action, amount = 0) {
    console.log(`Player action selected: ${action}`);
    const data = {
        game_id: gameId,
        action: action,
        amount: amount
    }
    socket.emit('player_action', data);

    const playerOptionsComponent = document.getElementById('player-options');
    playerOptionsComponent.classList.remove('player-options', 'content');
    playerOptionsComponent.classList.add('bet-slider-container-collapsed');
}


function updatePlayerInfo(playerState, currentPlayerName) {
    playerState.forEach(player => {
        const playerCard = document.getElementById(`player-${player.name}`);
        const playerStack = document.getElementById(`${player.name}-stack`);
        playerStack.textContent = `$${player.stack}`;

        if (player.name === currentPlayerName) {
            playerCard.classList.add('player-card--current-player');
            playerCard.classList.remove('player-card');
        } else {
            playerCard.classList.add('player-card');
            playerCard.classList.remove('player-card--current-player')
        }

        if (player['has_acted']) {
            playerCard.classList.add('player-card--has-acted');
            playerCard.classList.remove('player-card');
        } else {
            playerCard.classList.add('player-card');
            playerCard.classList.remove('player-card--has-acted');
        }

        if (player['is_folded']) {
            const playerCardsContainer = document.getElementById(`cards-player-${player.name}`);
            playerCardsContainer.classList.add('player-cards--is-folded');
        } else {
            const playerCardsContainer = document.getElementById(`cards-player-${player.name}`);
            playerCardsContainer.classList.remove('player-cards--is-folded');
        }

        if (player['is_human']) {
            const playerCardsContainer = document.getElementById(`cards-player-${player.name}`);
            const playerHand = player['hand'];

            playerCardsContainer.classList.remove('ai-cards');

            // Assuming playerHand.length and playerCardsContainer.children.length are the same
            Array.from(playerCardsContainer.children).forEach((cardElement, index) => {
                const card = playerHand[index];
                const cardSpan = cardElement;

                // Reset classes and text content
                cardSpan.className = 'card';
                cardSpan.textContent = '';

                // Add suit-specific class
                if (card.suit === 'Hearts') cardSpan.classList.add('hearts');
                if (card.suit === 'Diamonds') cardSpan.classList.add('diamonds');
                if (card.suit === 'Clubs') cardSpan.classList.add('clubs');
                if (card.suit === 'Spades') cardSpan.classList.add('spades');

                // Set the card content
                cardSpan.textContent = `${card.rank} ${getSuitSymbol(card['suit'])}`;
            });
        }
    })
}

// Function to update the player state on the UI
function renderPlayerCards(playerState, currentPlayerName) {
    let playersContainer = document.getElementById('players');
    playersContainer.innerHTML = ''; // Clear existing content

    playerState.forEach(player => {
        let playerCard = document.createElement('div');
        playerCard.id = `player-${player.name}`;
        if (player.name === currentPlayerName) {
            playerCard.classList.add('player-card--current-player');
        } else if (player['has_acted']) {
            playerCard.classList.add('player-card--has-acted');
        } else {
            playerCard.classList.add('player-card');
        }

        let playerHeadshot = document.createElement('img');
        playerHeadshot.src = '/static/images/kanye.jpg';
        playerHeadshot.classList.add('player-headshot');
        playerHeadshot.alt = 'Player picture';

        let playerName = document.createElement('h2');
        playerName.textContent = player.name;

        let playerMoney = document.createElement('p');
        playerMoney.textContent = `$${player.stack}`;

        let playerCardsContainer = document.createElement('div');
        playerCardsContainer.id = `cards-player-${player.name}`;
        if (player['is_folded']) {
            playerCardsContainer.classList.add('player-cards--is-folded');
        } else {
            playerCardsContainer.classList.add('player-cards');
        }

        player.hand.forEach(card => {
            let cardSpan = document.createElement('span');
            if (player['is_human']) {
                cardSpan.classList.add('card');
                if (card.suit === 'Hearts') cardSpan.classList.add('hearts');
                if (card.suit === 'Diamonds') cardSpan.classList.add('diamonds');
                if (card.suit === 'Clubs') cardSpan.classList.add('clubs');
                if (card.suit === 'Spades') cardSpan.classList.add('spades');
                cardSpan.textContent = `${card.rank} ${getSuitSymbol(card['suit'])}`;
            } else {
                cardSpan.classList.add('ai-card');
            }
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
        console.log("Event target: " + event.target.id);
        if (event.target && event.target.id === 'begin-round-button') {
            console.log("Begin Round button clicked.");
            const modal = document.getElementById('game-initialized-modal');
            if (modal) {
                modal.style.display = 'none';
            }
            socket.emit('progress_game', gameId)
        } else if (event.target && event.target.id === 'quit-button') {
            console.log("Quit button clicked.");
            const modal = document.getElementById('game-initialized-modal');
            if (modal) {
                modal.style.display = 'none';
            }
            window.location.href = '/';
        } else if (event.target && ['fold-button', 'check-button', 'call-button', 'all_in-button'].includes(event.target.id)) {
            console.log("Player action button clicked.");
            // Remove '-button' from the target.id string
            const action = event.target.id.slice(0, -7);
            playerAction(action);
        }
        else if (event.target && event.target.id === 'raise-button') {
            // Event listener for Raise
            console.log("Raise button clicked");
            // Betting Elements
            const raiseButton = document.getElementById('raise-button');
            const betSliderContainer = document.getElementById('bet-slider-container');
            const betSlider = document.getElementById('bet-slider');
            const betAmount = document.getElementById('bet-amount');
            const submitRaiseButton = document.getElementById('bet-submit-button');
            const doubleBetButton = document.getElementById('double-bet-amount-button')

            betSliderContainer.classList.toggle('bet-slider-container-expanded');

            const buttonRect = raiseButton.getBoundingClientRect();
            const centerX = buttonRect.left + (buttonRect.width / 2);
            const containerWidth = betSliderContainer.offsetWidth;

            betSliderContainer.style.left = `${(centerX - (containerWidth / 2))*.75}px`;

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
                if (betAmount.value * 2 < betAmount.max) {
                    betAmount.value = betAmount.value * 2;
                    betSlider.value = betAmount.value;
                } else {
                    betAmount.value = betAmount.max;
                    betSlider.value = betAmount.value;
                }
            })

            // Event listener for submitting a raise
            submitRaiseButton.addEventListener('click', async () => {
                console.log('Bet submit button clicked');
                playerAction('raise', betAmount.value);

                // Collapse the slider container after submission
                betSliderContainer.classList.toggle('bet-slider-container-collapsed');
                setTimeout(() => {
                    betSliderContainer.style.display = 'none';
                }, 500);
            });
        }
    })
});
