import { useState } from 'react'
import { PokerTable } from './components/PokerTable'
import { CardDemo } from './components/CardDemo'
import { GameSelector } from './components/GameSelector'
import './App.css'

function App() {
  const [currentView, setCurrentView] = useState<'selector' | 'table' | 'cards'>('selector')
  const [gameId, setGameId] = useState<string | null>(null)

  const handleSelectGame = (selectedGameId: string) => {
    setGameId(selectedGameId);
    setCurrentView('table');
  };

  const handleNewGame = () => {
    setGameId(null); // null means create new game
    setCurrentView('table');
  };

  return (
    <>
      {/* Navigation - only show when not on selector */}
      {currentView !== 'selector' && (
        <div style={{ 
          position: 'fixed', 
          top: 10, 
          left: 10, 
          zIndex: 1000,
          display: 'flex',
          gap: '10px'
        }}>
          <button 
            onClick={() => setCurrentView('selector')}
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
      {currentView === 'selector' && (
        <GameSelector 
          onSelectGame={handleSelectGame} 
          onNewGame={handleNewGame} 
        />
      )}
      {currentView === 'table' && <PokerTable gameId={gameId} />}
      {currentView === 'cards' && <CardDemo />}
    </>
  )
}

export default App
