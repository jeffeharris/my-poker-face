import { useState, useEffect } from 'react';
import { Socket } from 'socket.io-client';
import { Card } from './Card';
import { config } from '../config';
import './DebugPanel.css';

interface DebugPanelProps {
  gameId: string | null;
  socket: Socket | null;
}

interface TraitData {
  current: number;
  anchor: number;
  elasticity: number;
  pressure: number;
  min: number;
  max: number;
}

interface PlayerElasticity {
  traits: {
    [traitName: string]: TraitData;
  };
  mood: string;
}

interface ElasticityData {
  [playerName: string]: PlayerElasticity;
}

export function DebugPanel({ gameId, socket }: DebugPanelProps) {
  const [activeTab, setActiveTab] = useState<'elasticity' | 'cards'>('elasticity');
  const [elasticityData, setElasticityData] = useState<ElasticityData>({});
  const [loading, setLoading] = useState(false);

  // Sample cards for demo
  const demoCards = [
    'AS', 'KH', '5D', 'QC', 'JH', '10S', '2C', '7D'
  ];

  useEffect(() => {
    if (!gameId || activeTab !== 'elasticity') return;

    const fetchElasticityData = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${config.API_URL}/api/game/${gameId}/elasticity`);
        if (response.ok) {
          const data = await response.json();
          setElasticityData(data);
        }
      } catch (error) {
        console.error('Failed to fetch elasticity data:', error);
      } finally {
        setLoading(false);
      }
    };

    // Fetch immediately
    fetchElasticityData();

    // Set up WebSocket listener if socket is available
    if (socket) {
      const handleElasticityUpdate = (data: ElasticityData) => {
        console.log('Received elasticity update via WebSocket:', data);
        setElasticityData(data);
      };

      socket.on('elasticity_update', handleElasticityUpdate);

      return () => {
        socket.off('elasticity_update', handleElasticityUpdate);
      };
    } else {
      // Fall back to polling if no WebSocket
      const interval = setInterval(fetchElasticityData, 2000);
      return () => clearInterval(interval);
    }
  }, [gameId, activeTab, socket]);

  const getTraitColor = (trait: TraitData) => {
    const deviation = Math.abs(trait.current - trait.anchor);
    if (deviation > trait.elasticity * 0.7) return '#ff4444'; // High deviation
    if (deviation > trait.elasticity * 0.4) return '#ffaa44'; // Medium deviation
    return '#44ff44'; // Low deviation
  };

  const formatTraitName = (name: string) => {
    return name.split('_').map(word => 
      word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
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
      </div>

      <div className="debug-panel__content">
        {activeTab === 'elasticity' && (
          <div className="elasticity-panel-wrapper">
            {loading && <div className="loading">Loading...</div>}
            
            {Object.entries(elasticityData).map(([playerName, playerData]) => (
              <div key={playerName} className="edp-player">
                <h4>{playerName}</h4>
                <div className="edp-mood">Mood: <span className="edp-mood-value">{playerData.mood}</span></div>
                
                <div className="edp-traits">
                  {Object.entries(playerData.traits).map(([traitName, trait]) => {
                    const percentage = ((trait.current - trait.min) / (trait.max - trait.min)) * 100;
                    const anchorPercentage = ((trait.anchor - trait.min) / (trait.max - trait.min)) * 100;
                    
                    return (
                      <div key={traitName} className="edp-trait">
                        <div className="edp-trait-header">
                          <span className="edp-trait-name">{formatTraitName(traitName)}</span>
                          <span className="edp-trait-value" style={{ color: getTraitColor(trait) }}>
                            {trait.current.toFixed(2)}
                          </span>
                        </div>
                        
                        <div className="edp-trait-bar-container">
                          <div className="edp-trait-bar-background">
                            {/* Elasticity range */}
                            <div 
                              className="edp-elasticity-range"
                              style={{
                                left: `${((trait.anchor - trait.elasticity - trait.min) / (trait.max - trait.min)) * 100}%`,
                                width: `${(trait.elasticity * 2 / (trait.max - trait.min)) * 100}%`
                              }}
                            />
                            
                            {/* Anchor line */}
                            <div 
                              className="edp-anchor-line"
                              style={{ left: `${anchorPercentage}%` }}
                            />
                            
                            {/* Current value */}
                            <div 
                              className="edp-trait-bar"
                              style={{ width: `${percentage}%` }}
                            />
                          </div>
                          
                          <div className="edp-trait-labels">
                            <span>{trait.min.toFixed(1)}</span>
                            <span>{trait.max.toFixed(1)}</span>
                          </div>
                        </div>
                        
                        <div className="edp-trait-details">
                          <span>Pressure: {trait.pressure > 0 ? '+' : ''}{trait.pressure.toFixed(2)}</span>
                          <span>Anchor: {trait.anchor.toFixed(2)}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
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