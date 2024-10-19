let socket = io();

// // Function to initialize the game
// function loadGamePage() {
//     fetch('/game', { method: 'GET' })
//         .then(response => response.json())
//         .then(data => {
//             updateGameState(data);
//         });
// }

// // Listen for game state updates from the server
// socket.on('update_game_state', function(gameState) {
//     updateGameState(gameState);
// });

// // Function to update the UI based on the game state
// function updateGameState(gameState) {
//     document.getElementById('community-cards').innerHTML = JSON.stringify(gameState['community_cards']);
//     document.getElementById('pot').innerHTML = `Pot: $${gameState['pot']['total']} | Min: ${gameState['highest_bet']}`;
//     document.getElementById('player-options').innerHTML = `${gameState['current_player_options']}`;
//     updatePlayerState(gameState['players']);
// }

function playerAction(action) {
    const playerOptionsContainer = document.getElementById('player-options');
    playerOptionsContainer.hidden = true;       // TODO: does this do anything?

    fetch('/action', {
       method: 'POST',
       headers: {
           'Content-Type': 'application/json'
       },
       body: JSON.stringify({ action: action })
   })
   .then(response => response.json())
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

// function updatePlayerState(playerState) {
//     let playersContainer = document.getElementById('players');
//     playersContainer.innerHTML = ''; // Clear existing content
//
//     playerState.forEach((player, index) => {
//         let playerCard = document.createElement('div');
//         playerCard.classList.add('player-card');
//         playerCard.id = `player-${index + 1}`;
//
//         let playerHeadshot = document.createElement('img');
//         playerHeadshot.src = '/static/images/kanye.jpg';
//         playerHeadshot.classList.add('player-headshot');
//         playerHeadshot.alt = 'Player picture';
//
//         let playerName = document.createElement('h2');
//         playerName.textContent = player.name;
//
//         let playerMoney = document.createElement('p');
//         playerMoney.textContent = `$${player.stack}`;
//
//         let playerCardsContainer = document.createElement('div');
//         playerCardsContainer.id = `cards-player-${index + 1}`;
//         playerCardsContainer.classList.add('player-cards');
//
//         player.hand.forEach(card => {
//             let cardSpan = document.createElement('span');
//             cardSpan.classList.add('card');
//             // if (card.suit_symbol == '♥') cardSpan.classList.add('hearts');
//             // if (card.suit_symbol == '♦') cardSpan.classList.add('diamonds');
//             // if (card.suit_symbol == '♣') cardSpan.classList.add('clubs');
//             // if (card.suit_symbol == '♠') cardSpan.classList.add('spades');
//             if (card.suit == 'Hearts') cardSpan.classList.add('hearts');
//             if (card.suit == 'Diamonds') cardSpan.classList.add('diamonds');
//             if (card.suit == 'Clubs') cardSpan.classList.add('clubs');
//             if (card.suit == 'Spades') cardSpan.classList.add('spades');
//             cardSpan.textContent = `${card.rank} ${card.suit}`;
//             playerCardsContainer.appendChild(cardSpan);
//         });
//
//         playerCard.appendChild(playerHeadshot);
//         playerCard.appendChild(playerName);
//         playerCard.appendChild(playerMoney);
//         playerCard.appendChild(playerCardsContainer);
//
//         playersContainer.appendChild(playerCard);
//     });
// }

// // Function to send user move to the server
// function sendUserMove(move) {
//    socket.emit('user_move', { move: move });
// }

// Initialize game when the page loads
window.onload = function() {
    document.getElementById('bet-amount').value = 100;
    document.getElementById('bet-slider').value = 100
    // loadGamePage();
}

document.addEventListener('DOMContentLoaded', () => {
    const raiseButton = document.getElementById('raise-button');
    const betSliderContainer = document.getElementById('bet-slider-container');
    const betSlider = document.getElementById('bet-slider');
    const betAmount = document.getElementById('bet-amount');
    const submitRaiseButton = document.getElementById('bet-submit-button');

    raiseButton.addEventListener('click', () => {
        console.log('Raise button clicked');

        // Toggle the expanded state
        betSliderContainer.classList.toggle('bet-slider-container-expanded');

        // Get the button's position and dimensions
        const buttonRect = raiseButton.getBoundingClientRect();

        // Calculate the center position
        const centerX = buttonRect.left + (buttonRect.width / 2);

        // Get the width of the bet slider container
        const containerWidth = betSliderContainer.offsetWidth;

        // Set the position of the bet slider container to be centered over the raise button
        betSliderContainer.style.left = `${centerX - (containerWidth * 2)}px`;
    });

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

    submitRaiseButton.addEventListener('click', async () => {
        console.log('Bet submit button clicked');
        const amount = betAmount.value;

        try {
            const response = await fetch('/action', {
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
            // Handle the successful response here.
        } catch (error) {
            console.error('Error:', error);
            // Handle the error here.
        }

        // Collapse the slider container after submission
        betSliderContainer.classList.add('bet-slider-container-collapsed');
        setTimeout(() => {
            betSliderContainer.style.display = 'none';
        }, 500); // Match the timeframe to the transition duration
    });
});

