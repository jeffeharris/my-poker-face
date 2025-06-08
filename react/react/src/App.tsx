import { useState, useEffect } from 'react'
import { PokerTable } from './components/PokerTable'
import { CardDemo } from './components/CardDemo'
import { GameSelector } from './components/GameSelector'
import { PlayerNameEntry } from './components/PlayerNameEntry'
import { PersonalityManagerHTML } from './components/PersonalityManagerHTML'
import './App.css'

function App() {
  // Check localStorage for saved state on initial load
  const savedState = localStorage.getItem('pokerGameState');
  const parsedState = savedState ? JSON.parse(savedState) : null;
  
  const [currentView, setCurrentView] = useState<'name-entry' | 'selector' | 'table' | 'cards' | 'personalities'>(
    parsedState?.currentView || 'name-entry'
  )
  const [gameId, setGameId] = useState<string | null>(parsedState?.gameId || null)
  const [playerName, setPlayerName] = useState<string>(parsedState?.playerName || '')
  
  // Save state to localStorage whenever it changes
  useEffect(() => {
    const stateToSave = {
      currentView,
      gameId,
      playerName,
      timestamp: Date.now()
    };
    localStorage.setItem('pokerGameState', JSON.stringify(stateToSave));
  }, [currentView, gameId, playerName]);

  const handleSelectGame = (selectedGameId: string) => {
    setGameId(selectedGameId);
    setCurrentView('table');
  };

  const handleNewGame = () => {
    setGameId(null); // null means create new game
    setCurrentView('table');
  };

  const handleNameSubmit = (name: string) => {
    setPlayerName(name);
    setCurrentView('selector');
  };

  return (
    <>
      {/* Navigation - only show when not on selector or name-entry */}
      {currentView !== 'selector' && currentView !== 'name-entry' && (
        <div style={{ 
          position: 'fixed', 
          top: 10, 
          left: 10, 
          zIndex: 1000,
          display: 'flex',
          gap: '10px'
        }}>
          <button 
            onClick={() => {
              // Clear the saved game when going back to menu
              setGameId(null);
              setCurrentView('selector');
            }}
            style={{
              padding: '8px 16px',
              backgroundColor: '#666',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            ← Back to Menu
          </button>
          <button 
            onClick={() => setCurrentView('table')}
            style={{
              padding: '8px 16px',
              backgroundColor: currentView === 'table' ? '#00ff00' : '#333',
              color: currentView === 'table' ? '#000' : '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            Poker Table
          </button>
          <button 
            onClick={() => setCurrentView('cards')}
            style={{
              padding: '8px 16px',
              backgroundColor: currentView === 'cards' ? '#00ff00' : '#333',
              color: currentView === 'cards' ? '#000' : '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            Card Demo
          </button>
        </div>
      )}

      {/* Views */}
      {currentView === 'name-entry' && (
        <PlayerNameEntry onSubmit={handleNameSubmit} />
      )}
      {currentView === 'selector' && (
        <GameSelector 
          onSelectGame={handleSelectGame} 
          onNewGame={handleNewGame}
          onManagePersonalities={() => setCurrentView('personalities')}
        />
      )}
      {currentView === 'table' && (
        <PokerTable 
          gameId={gameId} 
          playerName={playerName}
          onGameCreated={(newGameId) => setGameId(newGameId)}
        />
      )}
      {currentView === 'cards' && <CardDemo />}
      {currentView === 'personalities' && (
        <PersonalityManagerHTML onBack={() => setCurrentView('selector')} />
      )}
    </>
  )
}

export default App
