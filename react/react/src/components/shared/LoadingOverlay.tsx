import './LoadingOverlay.css';

interface LoadingOverlayProps {
  message: string;
  submessage?: string;
}

export function LoadingOverlay({ message, submessage }: LoadingOverlayProps) {
  return (
    <div className="loading-overlay">
      <div className="loading-overlay__content">
        <div className="loading-overlay__cards">
          {['♠', '♥', '♦', '♣'].map((suit, i) => (
            <div key={i} className={`loading-overlay__card suit-${i}`}>{suit}</div>
          ))}
        </div>
        <h3>{message}</h3>
        {submessage && <p>{submessage}</p>}
      </div>
    </div>
  );
}
