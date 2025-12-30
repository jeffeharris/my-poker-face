import { useState } from 'react';
import './ElasticityDemo.css';

interface TraitData {
  current: number;
  anchor: number;
  elasticity: number;
  pressure: number;
  min: number;
  max: number;
}

interface PlayerData {
  mood: string;
  traits: Record<string, TraitData>;
}

type MockDataType = Record<string, PlayerData>;

// Standalone demo component with mock data
export function ElasticityDemo() {
  const [mockData, setMockData] = useState<MockDataType>({
    "A Mime": {
      mood: "Mysterious",
      traits: {
        aggression: {
          current: 0.50,
          anchor: 0.50,
          elasticity: 0.2,
          pressure: 0.0,
          min: 0.1,
          max: 0.9
        },
        bluff_tendency: {
          current: 0.90,
          anchor: 0.90,
          elasticity: 0.05,
          pressure: 0.0,
          min: 0.5,
          max: 1.0
        },
        chattiness: {
          current: 0.0,
          anchor: 0.0,
          elasticity: 0.0,
          pressure: 0.0,
          min: 0.0,
          max: 0.0
        },
        emoji_usage: {
          current: 0.70,
          anchor: 0.70,
          elasticity: 0.2,
          pressure: 0.0,
          min: 0.3,
          max: 1.0
        }
      }
    },
    "Gordon Ramsay": {
      mood: "Intense",
      traits: {
        aggression: {
          current: 0.85,
          anchor: 0.80,
          elasticity: 0.15,
          pressure: 0.05,
          min: 0.5,
          max: 1.0
        },
        bluff_tendency: {
          current: 0.40,
          anchor: 0.40,
          elasticity: 0.3,
          pressure: 0.0,
          min: 0.0,
          max: 1.0
        },
        chattiness: {
          current: 0.95,
          anchor: 0.90,
          elasticity: 0.1,
          pressure: 0.08,
          min: 0.6,
          max: 1.0
        },
        emoji_usage: {
          current: 0.20,
          anchor: 0.20,
          elasticity: 0.1,
          pressure: 0.0,
          min: 0.0,
          max: 0.5
        }
      }
    }
  });

  const formatTraitName = (name: string) => {
    return name.split('_').map(word => 
      word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
  };

  const getTraitColor = (trait: TraitData) => {
    const deviation = Math.abs(trait.current - trait.anchor);
    if (deviation > trait.elasticity * 0.7) return '#ff4444';
    if (deviation > trait.elasticity * 0.4) return '#ffaa44';
    return '#44ff44';
  };

  // Function to simulate pressure changes
  const applyRandomPressure = () => {
    const newData = { ...mockData };
    Object.keys(newData).forEach(playerName => {
      const player = newData[playerName];
      Object.keys(player.traits).forEach(traitName => {
        const trait = player.traits[traitName];
        if (trait.elasticity > 0) {
          // Random pressure between -0.1 and 0.1
          const pressure = (Math.random() - 0.5) * 0.2;
          trait.pressure += pressure;
          
          // Apply pressure to current value
          const change = pressure * trait.elasticity;
          trait.current = Math.max(trait.min, Math.min(trait.max, trait.anchor + change));
        }
      });
    });
    setMockData(newData);
  };

  const resetToAnchor = () => {
    const newData = { ...mockData };
    Object.keys(newData).forEach(playerName => {
      const player = newData[playerName];
      Object.keys(player.traits).forEach(traitName => {
        const trait = player.traits[traitName];
        trait.current = trait.anchor;
        trait.pressure = 0;
      });
    });
    setMockData(newData);
  };

  return (
    <div className="elasticity-demo">
      <div className="demo-header">
        <h2>Elasticity Visualization Demo</h2>
        <div className="demo-controls">
          <button onClick={applyRandomPressure}>Apply Random Pressure</button>
          <button onClick={resetToAnchor}>Reset to Anchor</button>
        </div>
      </div>

      <div className="demo-content">
        {Object.entries(mockData).map(([playerName, playerData]) => (
          <div key={playerName} className="demo-player">
            <h4>{playerName}</h4>
            <div className="demo-mood">Mood: <span className="demo-mood-value">{playerData.mood}</span></div>
            
            <div className="demo-traits">
              {Object.entries(playerData.traits).map(([traitName, trait]) => {
                const percentage = ((trait.current - trait.min) / (trait.max - trait.min)) * 100;
                const anchorPercentage = ((trait.anchor - trait.min) / (trait.max - trait.min)) * 100;
                
                return (
                  <div key={traitName} className="demo-trait">
                    <div className="demo-trait-header">
                      <span className="demo-trait-name">{formatTraitName(traitName)}</span>
                      <span className="demo-trait-value" style={{ color: getTraitColor(trait) }}>
                        {trait.current.toFixed(2)}
                      </span>
                    </div>
                    
                    <div className="demo-trait-bar-container">
                      <div className="demo-trait-bar-background">
                        {/* Elasticity range */}
                        <div 
                          className="demo-elasticity-range"
                          style={{
                            left: `${((trait.anchor - trait.elasticity - trait.min) / (trait.max - trait.min)) * 100}%`,
                            width: `${(trait.elasticity * 2 / (trait.max - trait.min)) * 100}%`
                          }}
                        />
                        
                        {/* Anchor line */}
                        <div 
                          className="demo-anchor-line"
                          style={{ left: `${anchorPercentage}%` }}
                        />
                        
                        {/* Current value */}
                        <div 
                          className="demo-trait-bar"
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                      
                      <div className="demo-trait-labels">
                        <span>{trait.min.toFixed(1)}</span>
                        <span>{trait.max.toFixed(1)}</span>
                      </div>
                    </div>
                    
                    <div className="demo-trait-details">
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
    </div>
  );
}