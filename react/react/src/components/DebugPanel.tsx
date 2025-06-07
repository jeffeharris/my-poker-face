import { useState, useEffect } from 'react';
import { Socket } from 'socket.io-client';
import { Card } from './Card';
import { config } from '../config';
import './DebugPanel.css';

interface DebugPanelProps {
  gameId: string | null;
  socket: Socket | null;
}

interface ElasticityData {
  [playerName: string]: {
    traits: {
      [traitName: string]: {
        current: number;
        anchor: number;
        elasticity: number;
        pressure: number;
        min: number;
        max: number;
      };
    };
    mood: string;
  };
}

export function DebugPanel({ gameId, socket }: DebugPanelProps) {
  const [activeTab, setActiveTab] = useState<'elasticity' | 'cards'>('elasticity');
  const [elasticityData, setElasticityData] = useState<ElasticityData>({});
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [loading, setLoading] = useState(false);

  // Sample cards for demo
  const demoCards = [
    'AS', 'KH', '5D', 'QC', 'JH', '10S', '2C', '7D'
  ];

  // Fetch initial elasticity data and listen for updates
  useEffect(() => {
    if (!gameId || activeTab !== 'elasticity') return;

    const fetchElasticityData = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${config.API_URL}/api/game/${gameId}/elasticity`);
        if (response.ok) {
          const data = await response.json();
          console.log('Elasticity data received:', data);
          setElasticityData(data);
        }
      } catch (error) {
        console.error('Failed to fetch elasticity data:', error);
      } finally {
        setLoading(false);
      }
    };

    // Fetch initial data
    fetchElasticityData();
    
    // Set up auto-refresh if enabled
    let interval: NodeJS.Timeout | null = null;
    if (autoRefresh) {
      interval = setInterval(fetchElasticityData, 3000);
    }

    // Also listen for WebSocket updates
    const handleElasticityUpdate = (data: ElasticityData) => {
      setElasticityData(data);
    };

    if (socket) {
      socket.on('elasticity_update', handleElasticityUpdate);
    }

    return () => {
      if (interval) clearInterval(interval);
      if (socket) {
        socket.off('elasticity_update', handleElasticityUpdate);
      }
    };
  }, [gameId, socket, activeTab, autoRefresh]);

  const formatTraitName = (trait: string) => {
    const formatted = trait.split('_').map(word => 
      word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
    
    // Add icons for different traits
    const icons: { [key: string]: string } = {
      'Bluff Tendency': '🎭',
      'Aggression': '🔥',
      'Chattiness': '💬',
      'Emoji Usage': '😊'
    };
    
    return `${icons[formatted] || '📊'} ${formatted}`;
  };

  const getTraitColor = (current: number, anchor: number) => {
    const diff = Math.abs(current - anchor);
    if (diff < 0.1) return '#4caf50';
    if (diff < 0.2) return '#ff9800';
    return '#f44336';
  };


  return (
    <div className="debug-panel">
      <div className="debug-panel__header">
        <div className="debug-panel__tabs">
          <button 
            className={`debug-tab ${activeTab === 'elasticity' ? 'active' : ''}`}
            onClick={() => setActiveTab('elasticity')}
          >
            Personality Elasticity
          </button>
          <button 
            className={`debug-tab ${activeTab === 'cards' ? 'active' : ''}`}
            onClick={() => setActiveTab('cards')}
          >
            Card Demo
          </button>
        </div>
        
        <div className="debug-panel__controls">
          <label className="auto-refresh">
            <input 
              type="checkbox" 
              checked={autoRefresh} 
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
        </div>
      </div>

      <div className="debug-panel__content">
        {activeTab === 'elasticity' && (
          <>
            {Object.keys(elasticityData).length > 0 && (
              <div style={{
                textAlign: 'center',
                marginBottom: '20px',
                padding: '12px',
                background: 'linear-gradient(90deg, rgba(76, 175, 80, 0.1) 0%, rgba(33, 150, 243, 0.1) 100%)',
                borderRadius: '8px',
                border: '1px solid rgba(76, 175, 80, 0.2)'
              }}>
                <h3 style={{
                  margin: 0,
                  fontSize: '18px',
                  background: 'linear-gradient(135deg, #4caf50, #2196f3)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  fontWeight: 700
                }}>
                  AI Personality Dynamics
                </h3>
                <p style={{
                  margin: '4px 0 0 0',
                  fontSize: '12px',
                  color: 'rgba(255, 255, 255, 0.6)'
                }}>
                  Real-time personality trait adjustments based on game events
                </p>
              </div>
            )}
            <div className="elasticity-grid">
            {Object.keys(elasticityData).length === 0 ? (
              <div style={{
                gridColumn: '1 / -1',
                textAlign: 'center',
                color: 'rgba(255, 255, 255, 0.5)',
                padding: '40px',
                fontSize: '14px'
              }}>
                {!gameId ? (
                  <div>
                    <p>No game active</p>
                    <p style={{ fontSize: '12px', marginTop: '8px' }}>
                      Start a game to see personality elasticity data
                    </p>
                  </div>
                ) : (
                  <div>
                    <p>Loading elasticity data...</p>
                    <p style={{ fontSize: '12px', marginTop: '8px' }}>
                      Personality traits will appear here as the game progresses
                    </p>
                  </div>
                )}
              </div>
            ) : (
              Object.entries(elasticityData).map(([playerName, data]) => (
                <div key={playerName} className="player-elasticity">
                  <h4>{playerName}</h4>
                  <div className="mood">
                    {(() => {
                      const moodIcons: { [key: string]: string } = {
                        'excited': '🎉',
                        'confident': '😎',
                        'frustrated': '😤',
                        'tilted': '🤯',
                        'focused': '🎯',
                        'relaxed': '😌',
                        'neutral': '😐',
                        'aggressive': '👊',
                        'cautious': '🤔'
                      };
                      const icon = moodIcons[data.mood.toLowerCase()] || '🎭';
                      return `${icon} ${data.mood.charAt(0).toUpperCase() + data.mood.slice(1)}`;
                    })()}
                  </div>
                  
                  <div className="traits">
                    {Object.entries(data.traits).map(([traitName, trait]) => {
                      const percentage = ((trait.current - trait.min) / (trait.max - trait.min)) * 100;
                      const anchorPercentage = ((trait.anchor - trait.min) / (trait.max - trait.min)) * 100;
                      
                      return (
                        <div key={traitName} className="trait">
                          <div className="trait-header">
                            <span className="trait-name">{formatTraitName(traitName)}</span>
                            <span className="trait-value" style={{ color: getTraitColor(trait.current, trait.anchor) }}>
                              {trait.current.toFixed(2)}
                            </span>
                          </div>
                          
                          <div className="trait-bar-container">
                            <div className="trait-bar-background">
                              {/* Elasticity range */}
                              <div 
                                className="elasticity-range"
                                style={{
                                  left: `${((trait.anchor - trait.elasticity - trait.min) / (trait.max - trait.min)) * 100}%`,
                                  width: `${(trait.elasticity * 2 / (trait.max - trait.min)) * 100}%`
                                }}
                              />
                              
                              {/* Anchor line */}
                              <div 
                                className="anchor-line"
                                style={{ left: `${anchorPercentage}%` }}
                              />
                              
                              {/* Current value */}
                              <div 
                                className="trait-bar"
                                style={{ width: `${percentage}%` }}
                              />
                            </div>
                            
                            <div className="trait-labels">
                              <span className="min-label">{trait.min.toFixed(1)}</span>
                              <span className="max-label">{trait.max.toFixed(1)}</span>
                            </div>
                          </div>
                          
                          <div className="trait-details">
                            <span>Pressure: {trait.pressure > 0 ? '+' : ''}{trait.pressure.toFixed(2)}</span>
                            <span>Anchor: {trait.anchor.toFixed(2)}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
            </div>
          </>
        )}

        {activeTab === 'cards' && (
          <div className="card-demo-content">
            <div className="demo-section">
              <h4>Card Sizes</h4>
              <div className="size-demo">
                <div className="size-group">
                  <span>Small:</span>
                  <Card card="AS" size="small" />
                  <Card card="KH" size="small" />
                </div>
                <div className="size-group">
                  <span>Medium:</span>
                  <Card card="QD" size="medium" />
                  <Card card="JC" size="medium" />
                </div>
                <div className="size-group">
                  <span>Large:</span>
                  <Card card="10H" size="large" />
                  <Card card="9S" size="large" />
                </div>
              </div>
            </div>

            <div className="demo-section">
              <h4>Card States</h4>
              <div className="state-demo">
                <div className="state-group">
                  <span>Face Down:</span>
                  <Card card="XX" faceDown={true} size="medium" />
                </div>
                <div className="state-group">
                  <span>Highlighted:</span>
                  <Card card="AH" size="medium" className="highlighted" />
                </div>
              </div>
            </div>

            <div className="demo-section">
              <h4>All Suits</h4>
              <div className="suits-demo">
                <Card card="AS" size="small" />
                <Card card="KH" size="small" />
                <Card card="QD" size="small" />
                <Card card="JC" size="small" />
              </div>
            </div>

            <div className="demo-section">
              <h4>Sample Hand</h4>
              <div className="hand-demo">
                {demoCards.slice(0, 5).map((card, i) => (
                  <Card key={i} card={card} size="medium" />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}