import { useEffect, useState } from 'react';

interface ElementInfo {
  selector: string;
  computedWidth: string;
  computedHeight: string;
  offsetWidth: number;
  offsetHeight: number;
  display: string;
  position: string;
  padding: string;
  margin: string;
  boxSizing: string;
}

interface CSSDebuggerProps {
  standalone?: boolean;
}

export function CSSDebugger({ standalone = true }: CSSDebuggerProps) {
  const [elementInfo, setElementInfo] = useState<ElementInfo[]>([]);
  const [isMinimized, setIsMinimized] = useState(false);

  useEffect(() => {
    const updateInfo = () => {
      const selectors = [
        '.poker-layout',
        '.poker-layout__main',
        '.poker-layout__table-container',
        '.poker-table',
        '.table-felt',
        '.players-area'
      ];

      const info: ElementInfo[] = [];
      
      // First, check parent chain of poker-layout
      const pokerLayout = document.querySelector('.poker-layout') as HTMLElement;
      if (pokerLayout) {
        console.log('=== Parent Chain Analysis ===');
        let current = pokerLayout;
        let level = 0;
        
        while (current && level < 10) {
          const computed = window.getComputedStyle(current);
          const tagName = current.tagName.toLowerCase();
          const className = current.className || 'no-class';
          const id = current.id || 'no-id';
          
          console.log(`Level ${level}: <${tagName}> class="${className}" id="${id}"`, {
            width: computed.width,
            maxWidth: computed.maxWidth,
            display: computed.display,
            position: computed.position,
            offsetWidth: current.offsetWidth
          });
          
          current = current.parentElement as HTMLElement;
          level++;
        }
      }

      selectors.forEach(selector => {
        const element = document.querySelector(selector) as HTMLElement;
        if (element) {
          const computed = window.getComputedStyle(element);
          info.push({
            selector,
            computedWidth: computed.width,
            computedHeight: computed.height,
            offsetWidth: element.offsetWidth,
            offsetHeight: element.offsetHeight,
            display: computed.display,
            position: computed.position,
            padding: computed.padding,
            margin: computed.margin,
            boxSizing: computed.boxSizing
          });
        }
      });

      // Also log viewport info
      console.log('Viewport:', {
        width: window.innerWidth,
        height: window.innerHeight,
        documentWidth: document.documentElement.clientWidth,
        documentHeight: document.documentElement.clientHeight
      });

      setElementInfo(info);
    };

    // Update on mount and resize
    updateInfo();
    window.addEventListener('resize', updateInfo);
    
    // Update every 2 seconds to catch dynamic changes
    const interval = setInterval(updateInfo, 2000);

    return () => {
      window.removeEventListener('resize', updateInfo);
      clearInterval(interval);
    };
  }, []);

  if (standalone && isMinimized) {
    return (
      <div
        style={{
          position: 'fixed',
          top: '10px',
          right: '10px',
          background: 'rgba(0, 0, 0, 0.9)',
          color: '#0f0',
          padding: '5px 10px',
          fontSize: '12px',
          fontFamily: 'monospace',
          border: '1px solid #0f0',
          zIndex: 99999,
          cursor: 'pointer'
        }}
        onClick={() => setIsMinimized(false)}
      >
        CSS Debug [+]
      </div>
    );
  }

  const containerStyle = standalone ? {
    position: 'fixed' as const,
    top: '10px',
    right: '10px',
    background: 'rgba(0, 0, 0, 0.9)',
    color: '#0f0',
    padding: '10px',
    fontSize: '11px',
    fontFamily: 'monospace',
    maxWidth: '400px',
    maxHeight: '80vh',
    overflow: 'auto',
    border: '1px solid #0f0',
    zIndex: 99999
  } : {
    color: '#0f0',
    padding: '10px',
    fontSize: '11px',
    fontFamily: 'monospace',
    height: '100%',
    overflow: 'auto',
    background: 'rgba(0, 0, 0, 0.5)'
  };

  return (
    <div style={containerStyle}>
      {standalone && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px' }}>
          <strong>CSS Debug Info</strong>
          <button
            onClick={() => setIsMinimized(true)}
            style={{
              background: 'none',
              border: 'none',
              color: '#0f0',
              cursor: 'pointer',
              fontSize: '16px'
            }}
          >
            [-]
          </button>
        </div>
      )}
      
      <div style={{ marginBottom: '10px' }}>
        <strong>Viewport:</strong> {window.innerWidth} x {window.innerHeight}
      </div>

      {elementInfo.map((info, index) => (
        <div key={index} style={{ marginBottom: '10px', borderBottom: '1px solid #333', paddingBottom: '5px' }}>
          <div style={{ color: '#ff0', fontWeight: 'bold' }}>{info.selector}</div>
          <div>Size: {info.computedWidth} x {info.computedHeight}</div>
          <div>Offset: {info.offsetWidth} x {info.offsetHeight}</div>
          <div>Display: {info.display}</div>
          <div>Position: {info.position}</div>
          <div>Padding: {info.padding}</div>
          <div>Box-sizing: {info.boxSizing}</div>
        </div>
      ))}

      <button
        onClick={() => {
          // Add temporary outlines to visualize containers
          const colors = ['red', 'blue', 'green', 'yellow', 'magenta', 'cyan'];
          const selectors = [
            '.poker-layout',
            '.poker-layout__main',
            '.poker-layout__table-container',
            '.poker-table',
            '.table-felt',
            '.players-area'
          ];

          selectors.forEach((selector, index) => {
            const element = document.querySelector(selector) as HTMLElement;
            if (element) {
              element.style.outline = `3px solid ${colors[index]}`;
              element.style.outlineOffset = `${-3 * (index + 1)}px`;
              
              // Remove after 3 seconds
              setTimeout(() => {
                element.style.outline = '';
                element.style.outlineOffset = '';
              }, 3000);
            }
          });
        }}
        style={{
          marginTop: '10px',
          padding: '5px 10px',
          background: '#0f0',
          color: '#000',
          border: 'none',
          cursor: 'pointer',
          width: '100%'
        }}
      >
        Flash Container Outlines (3s)
      </button>

      <button
        onClick={() => {
          console.log('=== CSS Debug Snapshot ===');
          elementInfo.forEach(info => {
            console.log(`${info.selector}:`, info);
          });
          
          // Log computed styles for table-felt
          const tableFelt = document.querySelector('.table-felt') as HTMLElement;
          if (tableFelt) {
            const computed = window.getComputedStyle(tableFelt);
            console.log('Table Felt Full Computed Styles:', {
              width: computed.width,
              height: computed.height,
              maxWidth: computed.maxWidth,
              maxHeight: computed.maxHeight,
              borderRadius: computed.borderRadius,
              transform: computed.transform,
              margin: computed.margin,
              padding: computed.padding
            });
          }
          
          // Analyze parent chain
          const pokerLayout = document.querySelector('.poker-layout') as HTMLElement;
          if (pokerLayout) {
            console.log('\n=== Parent Chain Analysis ===');
            let current = pokerLayout;
            let level = 0;
            
            while (current && level < 10) {
              const computed = window.getComputedStyle(current);
              const rect = current.getBoundingClientRect();
              const tagName = current.tagName.toLowerCase();
              const className = current.className || 'no-class';
              const id = current.id || 'no-id';
              
              console.log(`Level ${level}: <${tagName}> class="${className}" id="${id}"`, {
                computedWidth: computed.width,
                offsetWidth: current.offsetWidth,
                clientWidth: current.clientWidth,
                boundingRect: `${rect.width} x ${rect.height}`,
                display: computed.display,
                position: computed.position,
                overflow: computed.overflow,
                maxWidth: computed.maxWidth,
                flex: computed.flex || 'none'
              });
              
              // Check if this element is constraining width
              if (level > 0 && current.offsetWidth < window.innerWidth * 0.5) {
                console.warn(`⚠️ Potential constraint at level ${level}: width=${current.offsetWidth}px`);
              }
              
              current = current.parentElement as HTMLElement;
              level++;
            }
          }
        }}
        style={{
          marginTop: '5px',
          padding: '5px 10px',
          background: '#00f',
          color: '#fff',
          border: 'none',
          cursor: 'pointer',
          width: '100%'
        }}
      >
        Log to Console
      </button>
    </div>
  );
}