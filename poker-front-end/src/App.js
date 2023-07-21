import logo from './logo.svg';
import './App.css';
import React, { useState, useEffect } from 'react';
import axios from 'axios';

function App() {
  const [gameState, setGameState] = useState(null);
  const [gameOutput, setGameOutput] = useState('');
  const [playerNames, setPlayerNames] = useState('');
  const [playerInput, setPlayerInput] = useState('');
  const [betAmount, setBetAmount] = useState(0);
  const [playerMove, getPlayerMove] = useState('');

  // Fetch game state when the component mounts
  useEffect(() => {
    axios.get('http://localhost:3001/api/game_state')
      .then(response => {
        setGameState(response.data.game_state);
      })
      .catch(error => {
        console.error('Error fetching game state:', error);
      });
    }, []);

  // Function to start a new game
  const startNewGame = () => {
    const players = playerNames.split(',').map(name => name.trim());
    axios.post('http://localhost:3001/api/new_game', { players })
      .then(response => {
        setGameState(response.data.game_state);
        setGameOutput('New game started.');  // Update the game output
      })
      .catch(error => {
        console.error('Error starting new game:', error);
      });
  };

  // Function to make a move
  const makeMove = () => {
    axios.post('http://localhost:3001/api/make_move', { move: playerInput })
      .then(response => {
        setGameState(response.data.game_state);
        setPlayerInput('');  // Clear the player input
      })
      .catch(error => {
        console.error('Error making move:', error);
      });
  };

  // Function to handle changes to the player input
  const handlePlayerInputChange = (event) => {
    setPlayerInput(event.target.value);
  };

  return (
    <div className="App" style={{ display: 'flex', flexDirection: 'row' }}>
      <div style={{ flex: 2, marginRight: '10px' }}>
        {/* Display the game's text output here */}
        <textarea readOnly style={{ width: '100%', height: '90%' }} value={gameOutput} />

        {/* Add a text area for entering player names */}
        <textarea
          value={playerNames}
          onChange={event => setPlayerNames(event.target.value)}
          placeholder="Enter player names, separated by commas"
        />
        
        {/* Input for the player's move */}
        <input type="text" value={playerInput} onChange={handlePlayerInputChange} style={{ width: '100%', marginTop: '10px' }} />
        <button onClick={makeMove} style={{ width: '100%', marginTop: '10px' }}>Make Move</button>
        <input
          type="number"
          value={betAmount}
          onChange={event => setBetAmount(event.target.value)}
          disabled={!(playerMove === 'raise' || playerMove === 'all-in' || playerMove === 'bet')}
        />
      </div>
      <div style={{ flex: 1 }}>
        {/* Display the game state here */}
        <pre>{JSON.stringify(gameState, null, 2)}</pre>
        {/* Button to start a new game */}
        <button onClick={startNewGame} style={{ width: '100%', marginTop: '10px' }}>Start New Game</button>
      </div>
    </div>
    );
}

export default App;
