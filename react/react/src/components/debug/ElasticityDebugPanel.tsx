import { useState, useEffect, useRef } from 'react';
import { Socket } from 'socket.io-client';
import { config } from '../../config';
import './ElasticityDebugPanel.css';

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

interface ElasticityDebugPanelProps {
  gameId: string | null;
  isOpen: boolean;
  socket?: Socket | null;
}

export function ElasticityDebugPanel({ gameId, isOpen, socket }: ElasticityDebugPanelProps) {
  const [elasticityData, setElasticityData] = useState<ElasticityData>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!gameId || !isOpen) return;

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
  }, [gameId, isOpen, socket]);

  if (!isOpen) return null;

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
    <div className="elasticity-debug-panel">
      <h3>Personality Elasticity Debug</h3>
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
                      <span className="min-label">{trait.min.toFixed(1)}</span>
                      <span className="max-label">{trait.max.toFixed(1)}</span>
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
  );
}