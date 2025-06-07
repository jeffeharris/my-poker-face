import { useState, useEffect } from 'react'
import { PokerTable } from './components/PokerTable'
import { GameSelector } from './components/GameSelector'
import { PlayerNameEntry } from './components/PlayerNameEntry'
import { PersonalityManagerHTML } from './components/PersonalityManagerHTML'
import { GameMenu } from './components/GameMenu'
import { ThemedGameSelector } from './components/ThemedGameSelector'
import { CustomGameConfig } from './components/CustomGameConfig'
import { ElasticityDemo } from './components/ElasticityDemo'
import { config } from './config'
import './App.css'

type ViewType = 'name-entry' | 'game-menu' | 'selector' | 'table' | 'personalities' | 'themed-game' | 'custom-game' | 'elasticity-demo'

interface Theme {
  id: string;
  name: string;
  description: string;
  icon: string;
  personalities?: string[];
}

function App() {
  const [currentView, setCurrentView] = useState<ViewType>('name-entry')
  const [gameId, setGameId] = useState<string | null>(null)
  const [playerName, setPlayerName] = useState<string>('')
  const [savedGamesCount, setSavedGamesCount] = useState(0)

  useEffect(() => {
    fetchSavedGamesCount();
  }, []);

  const fetchSavedGamesCount = async () => {
    try {
      const response = await fetch(`${config.API_URL}/games`);
      const data = await response.json();
      setSavedGamesCount(data.games?.length || 0);
    } catch (error) {
      console.error('Failed to fetch saved games:', error);
    }
  };

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
    setCurrentView('game-menu');
  };

  const handleQuickPlay = async () => {
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ playerName }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setGameId(data.game_id);
        setCurrentView('table');
      }
    } catch (error) {
      console.error('Failed to create game:', error);
    }
  };

  const handleCustomGame = () => {
    setCurrentView('custom-game');
  };

  const handleThemedGame = () => {
    setCurrentView('themed-game');
  };

  const handleContinueGame = () => {
    setCurrentView('selector');
  };

  const handleStartCustomGame = async (selectedPersonalities: string[]) => {
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          playerName,
          personalities: selectedPersonalities 
        }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setGameId(data.game_id);
        setCurrentView('table');
      }
    } catch (error) {
      console.error('Failed to create custom game:', error);
    }
  };

  const handleSelectTheme = async (theme: Theme) => {
    if (!theme.personalities) return;
    
    try {
      const response = await fetch(`${config.API_URL}/api/new-game`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          playerName,
          personalities: theme.personalities 
        }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setGameId(data.game_id);
        setCurrentView('table');
      }
    } catch (error) {
      console.error('Failed to create themed game:', error);
    }
  };

  return (
    <>
      {/* Temporary button for elasticity demo */}
      <button 
        onClick={() => setCurrentView('elasticity-demo')}
        style={{
          position: 'fixed',
          bottom: 10,
          right: 10,
          zIndex: 9999,
          padding: '10px',
          backgroundColor: '#4caf50',
          color: 'white',
          border: 'none',
          borderRadius: '5px',
          cursor: 'pointer'
        }}
      >
        Test Elasticity Demo
      </button>

      {/* Navigation - only show when in table view */}
      {currentView === 'table' && (
        <div style={{ 
          position: 'fixed', 
          top: 10, 
          left: 10, 
          zIndex: 1000,
          display: 'flex',
          gap: '10px'
        }}>
          <button 
            onClick={() => setCurrentView('game-menu')}
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
        </div>
      )}

      {/* Views */}
      {currentView === 'name-entry' && (
        <PlayerNameEntry onSubmit={handleNameSubmit} />
      )}
      {currentView === 'game-menu' && (
        <GameMenu 
          playerName={playerName}
          onQuickPlay={handleQuickPlay}
          onCustomGame={handleCustomGame}
          onThemedGame={handleThemedGame}
          onContinueGame={handleContinueGame}
          savedGamesCount={savedGamesCount}
        />
      )}
      {currentView === 'selector' && (
        <GameSelector 
          onSelectGame={handleSelectGame} 
          onNewGame={handleNewGame}
          onManagePersonalities={() => setCurrentView('personalities')}
        />
      )}
      {currentView === 'custom-game' && (
        <CustomGameConfig 
          onStartGame={handleStartCustomGame}
          onBack={() => setCurrentView('game-menu')}
        />
      )}
      {currentView === 'themed-game' && (
        <ThemedGameSelector 
          onSelectTheme={handleSelectTheme}
          onBack={() => setCurrentView('game-menu')}
        />
      )}
      {currentView === 'table' && <PokerTable gameId={gameId} playerName={playerName} />}
      {currentView === 'personalities' && (
        <PersonalityManagerHTML onBack={() => setCurrentView('selector')} />
      )}
      {currentView === 'elasticity-demo' && <ElasticityDemo />}
      
      {/* Debug info overlay */}
      {currentView === 'table' && (
        <div style={{
          position: 'fixed',
          bottom: 60,
          right: 10,
          background: 'rgba(0,0,0,0.9)',
          color: '#0f0',
          padding: '10px',
          fontSize: '12px',
          fontFamily: 'monospace',
          maxWidth: '300px',
          border: '1px solid #0f0',
          zIndex: 10000
        }}>
          <div>Debug Info:</div>
          <button onClick={() => {
            const panel = document.querySelector('.debug-panel__content');
            if (panel) {
              const elasticityContent = panel.querySelector('.elasticity-panel-wrapper');
              console.log('Debug Panel HTML:', panel.innerHTML);
              console.log('Has elasticity wrapper:', !!elasticityContent);
              console.log('Elasticity content:', elasticityContent?.innerHTML);
              
              // Check specific elements
              const elements = {
                'edp-player': panel.querySelectorAll('.edp-player').length,
                'edp-trait': panel.querySelectorAll('.edp-trait').length,
                'edp-anchor-line': panel.querySelectorAll('.edp-anchor-line').length,
                'edp-trait-bar': panel.querySelectorAll('.edp-trait-bar').length,
                'edp-elasticity-range': panel.querySelectorAll('.edp-elasticity-range').length
              };
              console.log('Element counts:', elements);
            }
          }}>
            Inspect Debug Panel
          </button>
          <button onClick={() => {
            // Test 1: Check elasticity panel styles
            console.log('=== TESTING ELASTICITY PANEL STYLES ===');
            const edpBar = document.querySelector('.edp-trait-bar');
            const edpAnchor = document.querySelector('.edp-anchor-line');
            
            if (edpBar) {
              const styles = window.getComputedStyle(edpBar);
              console.log('✓ .edp-trait-bar found');
              console.log('  - background:', styles.background);
              console.log('  - height:', styles.height);
              console.log('  - Expected: gradient background, ~20px height');
            } else {
              console.log('✗ .edp-trait-bar NOT FOUND');
            }
            
            if (edpAnchor) {
              const styles = window.getComputedStyle(edpAnchor);
              console.log('✓ .edp-anchor-line found');
              console.log('  - background:', styles.background);
              console.log('  - Expected: yellow (#ffff00)');
            } else {
              console.log('✗ .edp-anchor-line NOT FOUND');
            }
            
            // Test 2: Check for old class names (should not exist)
            console.log('\n=== CHECKING FOR OLD CLASS NAMES ===');
            const oldClasses = ['.trait-bar', '.anchor-line', '.trait', '.player-elasticity'];
            oldClasses.forEach(cls => {
              const found = document.querySelector(cls);
              if (found) {
                console.log(`✗ OLD CLASS ${cls} STILL EXISTS!`);
              } else {
                console.log(`✓ Old class ${cls} removed`);
              }
            });
            
            // Test 3: Verify no CSS conflicts
            console.log('\n=== CHECKING FOR CSS CONFLICTS ===');
            const cgcBar = document.querySelector('.cgc-trait-bar');
            if (cgcBar && edpBar) {
              const cgcStyles = window.getComputedStyle(cgcBar);
              const edpStyles = window.getComputedStyle(edpBar);
              console.log('CustomGameConfig bar height:', cgcStyles.height);
              console.log('ElasticityPanel bar height:', edpStyles.height);
              console.log('✓ Both components have separate styles');
            }
          }}>
            Test CSS Changes
          </button>
        </div>
      )}
    </>
  )
}

export default App
