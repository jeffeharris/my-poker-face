import './PlayerThinking.css';

interface PlayerThinkingProps {
  playerName: string;
  position: number;
}

export function PlayerThinking({ playerName, position }: PlayerThinkingProps) {
  return (
    <div className={`player-thinking-indicator position-${position}`}>
      <div className="thinking-ring">
        <div className="ring-pulse"></div>
        <div className="ring-pulse ring-delay"></div>
      </div>
      <div className="thinking-text">
        <span className="dots">
          <span className="dot">•</span>
          <span className="dot">•</span>
          <span className="dot">•</span>
        </span>
      </div>
    </div>
  );
}