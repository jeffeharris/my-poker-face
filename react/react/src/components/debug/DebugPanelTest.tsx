import { useEffect, useState } from 'react';

export function DebugPanelTest() {
  const [html, setHtml] = useState('');

  useEffect(() => {
    // Wait a bit for the debug panel to render
    setTimeout(() => {
      const debugPanel = document.querySelector('.debug-panel__content');
      if (debugPanel) {
        setHtml(debugPanel.innerHTML);
      }
    }, 1000);
  }, []);

  return (
    <div style={{ padding: '20px', background: '#000', color: '#0f0', fontFamily: 'monospace' }}>
      <h2>Debug Panel HTML Output:</h2>
      <pre style={{ 
        whiteSpace: 'pre-wrap', 
        background: '#111', 
        padding: '10px',
        border: '1px solid #333',
        maxHeight: '400px',
        overflow: 'auto'
      }}>
        {html || 'Waiting for debug panel to render...'}
      </pre>
      
      <h2>Computed Styles:</h2>
      <button onClick={() => {
        const elements = {
          'trait-bar-background': document.querySelector('.trait-bar-background'),
          'anchor-line': document.querySelector('.anchor-line'),
          'elasticity-range': document.querySelector('.elasticity-range'),
          'trait-bar': document.querySelector('.trait-bar')
        };
        
        Object.entries(elements).forEach(([name, el]) => {
          if (el) {
            const styles = window.getComputedStyle(el);
            console.log(`=== ${name} ===`);
            console.log('position:', styles.position);
            console.log('background:', styles.background);
            console.log('z-index:', styles.zIndex);
            console.log('width:', styles.width);
            console.log('height:', styles.height);
            console.log('left:', styles.left);
            console.log('---');
          }
        });
      }}>
        Log Computed Styles to Console
      </button>
    </div>
  );
}