import { useEffect } from 'react';
import { config } from '../config';

interface PersonalityManagerHTMLProps {
  onBack: () => void;
}

export function PersonalityManagerHTML({ onBack }: PersonalityManagerHTMLProps) {
  useEffect(() => {
    // Add the back button functionality
    const handleBack = () => onBack();
    (window as any).handleBackToMenu = handleBack;
    
    // Initialize the personality manager
    initializePersonalityManager();
    
    return () => {
      delete (window as any).handleBackToMenu;
    };
  }, [onBack]);

  const initializePersonalityManager = () => {
    let currentPersonality: string | null = null;
    let personalities: Record<string, any> = {};
    let arrayData: Record<string, string[]> = {};

    // Load personalities on mount
    loadPersonalities();

    async function loadPersonalities() {
      try {
        const response = await fetch(`${config.API_URL}/api/personalities`);
        const data = await response.json();
        
        if (data.success) {
          personalities = data.personalities;
          displayPersonalityList();
        } else {
          showAlert('error', 'Failed to load personalities: ' + data.error);
        }
      } catch (error: any) {
        showAlert('error', 'Error loading personalities: ' + error.message);
      }
    }

    function displayPersonalityList() {
      const listDiv = document.getElementById('personality-list');
      if (!listDiv) return;
      
      const names = Object.keys(personalities).sort();
      
      listDiv.innerHTML = names.map(name => `
        <div class="personality-item ${currentPersonality === name ? 'active' : ''}" 
             data-name="${name}">
          ${name}
        </div>
      `).join('');

      // Add click handlers
      listDiv.querySelectorAll('.personality-item').forEach(item => {
        item.addEventListener('click', () => {
          const name = item.getAttribute('data-name');
          if (name) selectPersonality(name);
        });
      });
    }

    function selectPersonality(name: string) {
      currentPersonality = name;
      displayPersonalityList();
      displayEditor(name, personalities[name]);
    }

    function displayEditor(name: string, data: any) {
      const editorDiv = document.getElementById('editor-content');
      if (!editorDiv) return;
      
      // Get elasticity config or use defaults
      const elasticityConfig = data.elasticity_config || {
        trait_elasticity: {
          bluff_tendency: 0.3,
          aggression: 0.3,
          chattiness: 0.5,
          emoji_usage: 0.3
        },
        mood_elasticity: 0.4,
        recovery_rate: 0.1
      };
      
      editorDiv.innerHTML = `
        <h2>Editing: ${name}</h2>
        
        <div class="form-group">
          <label for="play_style">Play Style</label>
          <input type="text" id="play_style" value="${data.play_style || ''}" 
                 placeholder="e.g., aggressive and boastful">
        </div>
        
        <div class="traits-grid">
          <div class="form-group">
            <label for="default_confidence">Default Confidence</label>
            <input type="text" id="default_confidence" value="${data.default_confidence || ''}" 
                   placeholder="e.g., supreme">
          </div>
          
          <div class="form-group">
            <label for="default_attitude">Default Attitude</label>
            <input type="text" id="default_attitude" value="${data.default_attitude || ''}" 
                   placeholder="e.g., domineering">
          </div>
        </div>
        
        <h3>Personality Traits</h3>
        ${renderTraitSlider('bluff_tendency', 'Bluff Tendency', data.personality_traits?.bluff_tendency || 0.5, elasticityConfig.trait_elasticity?.bluff_tendency || 0.3)}
        ${renderTraitSlider('aggression', 'Aggression', data.personality_traits?.aggression || 0.5, elasticityConfig.trait_elasticity?.aggression || 0.3)}
        ${renderTraitSlider('chattiness', 'Chattiness', data.personality_traits?.chattiness || 0.5, elasticityConfig.trait_elasticity?.chattiness || 0.5)}
        ${renderTraitSlider('emoji_usage', 'Emoji Usage', data.personality_traits?.emoji_usage || 0.3, elasticityConfig.trait_elasticity?.emoji_usage || 0.3)}
        
        <h3>Elasticity Settings</h3>
        <div class="trait-slider">
          <div class="trait-header">
            <label>Mood Elasticity</label>
            <span class="trait-info">How reactive mood changes are</span>
          </div>
          <div class="slider-container">
            <input type="range" id="mood_elasticity" min="0" max="100" 
                   value="${(elasticityConfig.mood_elasticity || 0.4) * 100}">
            <span class="slider-value" id="mood_elasticity_value">
              ${Math.round((elasticityConfig.mood_elasticity || 0.4) * 100)}%
            </span>
          </div>
        </div>
        
        <div class="trait-slider">
          <div class="trait-header">
            <label>Recovery Rate</label>
            <span class="trait-info">How fast traits return to baseline</span>
          </div>
          <div class="slider-container">
            <input type="range" id="recovery_rate" min="0" max="20" 
                   value="${(elasticityConfig.recovery_rate || 0.1) * 100}">
            <span class="slider-value" id="recovery_rate_value">
              ${Math.round((elasticityConfig.recovery_rate || 0.1) * 100)}%
            </span>
          </div>
        </div>
        
        <h3>Verbal Tics</h3>
        <div class="array-input" id="verbal_tics">
          ${(data.verbal_tics || []).map((tic: string, i: number) => `
            <div class="array-item">
              <input type="text" value="${tic}" data-index="${i}" data-field="verbal_tics">
              <button class="remove-array-item" data-field="verbal_tics" data-index="${i}">×</button>
            </div>
          `).join('')}
        </div>
        <button class="add-item-btn" data-field="verbal_tics">+ Add Verbal Tic</button>
        
        <h3>Physical Tics</h3>
        <div class="array-input" id="physical_tics">
          ${(data.physical_tics || []).map((tic: string, i: number) => `
            <div class="array-item">
              <input type="text" value="${tic}" data-index="${i}" data-field="physical_tics">
              <button class="remove-array-item" data-field="physical_tics" data-index="${i}">×</button>
            </div>
          `).join('')}
        </div>
        <button class="add-item-btn" data-field="physical_tics">+ Add Physical Tic</button>
        
        <div class="button-group">
          <button class="btn btn-primary" id="save-btn">Save Changes</button>
          <button class="btn btn-warning" id="regenerate-btn">Regenerate with AI</button>
          <button class="btn btn-danger" id="delete-btn">Delete Personality</button>
          <button class="btn btn-secondary" id="cancel-btn">Cancel</button>
        </div>
      `;

      // Helper function to render trait slider with elasticity
      function renderTraitSlider(traitId: string, label: string, value: number, elasticity: number) {
        const minValue = Math.max(0, value - elasticity);
        const maxValue = Math.min(1, value + elasticity);
        
        return `
          <div class="trait-slider">
            <div class="trait-header">
              <label>${label}</label>
              <span class="trait-info">Elasticity: ±${Math.round(elasticity * 100)}%</span>
            </div>
            <div class="slider-container">
              <div class="elasticity-range" style="left: ${minValue * 100}%; width: ${(maxValue - minValue) * 100}%;"></div>
              <input type="range" id="${traitId}" min="0" max="100" 
                     value="${value * 100}">
              <span class="slider-value" id="${traitId}_value">
                ${Math.round(value * 100)}%
              </span>
            </div>
            <div class="elasticity-bounds">
              <span>Min: ${Math.round(minValue * 100)}%</span>
              <span>Max: ${Math.round(maxValue * 100)}%</span>
            </div>
            <div class="form-group" style="margin-top: 10px;">
              <label for="${traitId}_elasticity" style="font-size: 12px;">Elasticity</label>
              <input type="range" id="${traitId}_elasticity" min="0" max="100" 
                     value="${elasticity * 100}" style="height: 4px;">
              <span class="slider-value" id="${traitId}_elasticity_value" style="font-size: 12px;">
                ${Math.round(elasticity * 100)}%
              </span>
            </div>
          </div>
        `;
      }

      // Add event listeners
      document.querySelectorAll('input[type="range"]').forEach(slider => {
        slider.addEventListener('input', (e) => {
          const target = e.target as HTMLInputElement;
          const trait = target.id;
          const valueSpan = document.getElementById(trait + '_value');
          if (valueSpan) valueSpan.textContent = target.value + '%';
          
          // Update elasticity range visualization if it's a trait slider
          if (!trait.endsWith('_elasticity') && trait !== 'mood_elasticity' && trait !== 'recovery_rate') {
            updateElasticityRange(trait);
          }
        });
      });
      
      // Function to update elasticity range visualization
      function updateElasticityRange(traitId: string) {
        const traitSlider = document.getElementById(traitId) as HTMLInputElement;
        const elasticitySlider = document.getElementById(traitId + '_elasticity') as HTMLInputElement;
        
        if (traitSlider && elasticitySlider) {
          const value = parseFloat(traitSlider.value) / 100;
          const elasticity = parseFloat(elasticitySlider.value) / 100;
          
          const minValue = Math.max(0, value - elasticity);
          const maxValue = Math.min(1, value + elasticity);
          
          const rangeDiv = traitSlider.parentElement?.querySelector('.elasticity-range') as HTMLElement;
          if (rangeDiv) {
            rangeDiv.style.left = `${minValue * 100}%`;
            rangeDiv.style.width = `${(maxValue - minValue) * 100}%`;
          }
          
          const boundsDiv = traitSlider.closest('.trait-slider')?.querySelector('.elasticity-bounds') as HTMLElement;
          if (boundsDiv) {
            boundsDiv.innerHTML = `
              <span>Min: ${Math.round(minValue * 100)}%</span>
              <span>Max: ${Math.round(maxValue * 100)}%</span>
            `;
          }
        }
      }

      document.querySelectorAll('.array-item input').forEach(input => {
        input.addEventListener('change', (e) => {
          const target = e.target as HTMLInputElement;
          const field = target.getAttribute('data-field');
          const index = parseInt(target.getAttribute('data-index') || '0');
          if (field) updateArrayItem(field, index, target.value);
        });
      });

      document.querySelectorAll('.remove-array-item').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const target = e.target as HTMLElement;
          const field = target.getAttribute('data-field');
          const index = parseInt(target.getAttribute('data-index') || '0');
          if (field) removeArrayItem(field, index);
        });
      });

      document.querySelectorAll('.add-item-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const target = e.target as HTMLElement;
          const field = target.getAttribute('data-field');
          if (field) addArrayItem(field);
        });
      });

      document.getElementById('save-btn')?.addEventListener('click', savePersonality);
      document.getElementById('regenerate-btn')?.addEventListener('click', regeneratePersonality);
      document.getElementById('delete-btn')?.addEventListener('click', deletePersonality);
      document.getElementById('cancel-btn')?.addEventListener('click', cancelEdit);
    }

    function updateArrayItem(arrayName: string, index: number, value: string) {
      if (!currentPersonality) return;
      if (!arrayData[arrayName]) {
        arrayData[arrayName] = personalities[currentPersonality][arrayName] || [];
      }
      arrayData[arrayName][index] = value;
    }

    function removeArrayItem(arrayName: string, index: number) {
      if (!currentPersonality) return;
      if (!arrayData[arrayName]) {
        arrayData[arrayName] = [...(personalities[currentPersonality][arrayName] || [])];
      }
      arrayData[arrayName].splice(index, 1);
      selectPersonality(currentPersonality);
    }

    function addArrayItem(arrayName: string) {
      if (!currentPersonality) return;
      if (!arrayData[arrayName]) {
        arrayData[arrayName] = [...(personalities[currentPersonality][arrayName] || [])];
      }
      arrayData[arrayName].push('');
      personalities[currentPersonality][arrayName] = arrayData[arrayName];
      selectPersonality(currentPersonality);
    }

    function getArrayValues(arrayName: string): string[] {
      const container = document.getElementById(arrayName);
      if (!container) return [];
      const inputs = container.querySelectorAll('input');
      return Array.from(inputs).map(input => input.value).filter(v => v);
    }

    async function savePersonality() {
      if (!currentPersonality) return;
      
      const updatedData = {
        play_style: (document.getElementById('play_style') as HTMLInputElement)?.value || '',
        default_confidence: (document.getElementById('default_confidence') as HTMLInputElement)?.value || '',
        default_attitude: (document.getElementById('default_attitude') as HTMLInputElement)?.value || '',
        personality_traits: {
          bluff_tendency: parseInt((document.getElementById('bluff_tendency') as HTMLInputElement)?.value || '50') / 100,
          aggression: parseInt((document.getElementById('aggression') as HTMLInputElement)?.value || '50') / 100,
          chattiness: parseInt((document.getElementById('chattiness') as HTMLInputElement)?.value || '50') / 100,
          emoji_usage: parseInt((document.getElementById('emoji_usage') as HTMLInputElement)?.value || '30') / 100
        },
        elasticity_config: {
          trait_elasticity: {
            bluff_tendency: parseInt((document.getElementById('bluff_tendency_elasticity') as HTMLInputElement)?.value || '30') / 100,
            aggression: parseInt((document.getElementById('aggression_elasticity') as HTMLInputElement)?.value || '30') / 100,
            chattiness: parseInt((document.getElementById('chattiness_elasticity') as HTMLInputElement)?.value || '50') / 100,
            emoji_usage: parseInt((document.getElementById('emoji_usage_elasticity') as HTMLInputElement)?.value || '30') / 100
          },
          mood_elasticity: parseInt((document.getElementById('mood_elasticity') as HTMLInputElement)?.value || '40') / 100,
          recovery_rate: parseInt((document.getElementById('recovery_rate') as HTMLInputElement)?.value || '10') / 100
        },
        verbal_tics: getArrayValues('verbal_tics'),
        physical_tics: getArrayValues('physical_tics')
      };
      
      try {
        const response = await fetch(`${config.API_URL}/api/personality/${currentPersonality}`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(updatedData)
        });
        
        const data = await response.json();
        
        if (data.success) {
          showAlert('success', data.message);
          personalities[currentPersonality] = updatedData;
          arrayData = {};
        } else {
          showAlert('error', 'Failed to save: ' + data.error);
        }
      } catch (error: any) {
        showAlert('error', 'Error saving personality: ' + error.message);
      }
    }

    async function deletePersonality() {
      if (!currentPersonality) return;
      
      if (!confirm(`Are you sure you want to delete ${currentPersonality}? This cannot be undone.`)) {
        return;
      }
      
      try {
        const response = await fetch(`${config.API_URL}/api/personality/${currentPersonality}`, {
          method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
          showAlert('success', data.message);
          delete personalities[currentPersonality];
          currentPersonality = null;
          displayPersonalityList();
          const editorContent = document.getElementById('editor-content');
          if (editorContent) {
            editorContent.innerHTML = `
              <div class="no-selection">
                Select a personality to edit or create a new one
              </div>
            `;
          }
        } else {
          showAlert('error', 'Failed to delete: ' + data.error);
        }
      } catch (error: any) {
        showAlert('error', 'Error deleting personality: ' + error.message);
      }
    }

    async function regeneratePersonality() {
      if (!currentPersonality) return;
      
      if (!confirm(`Are you sure you want to regenerate the personality for "${currentPersonality}"? This will replace the current personality with a new AI-generated one.`)) {
        return;
      }
      
      showAlert('info', `Regenerating personality for ${currentPersonality}...`);
      
      try {
        const response = await fetch(`${config.API_URL}/api/generate_personality`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ 
            name: currentPersonality,
            force: true
          })
        });
        
        const data = await response.json();
        
        if (data.success) {
          personalities[currentPersonality] = data.personality;
          selectPersonality(currentPersonality);
          showAlert('success', `Successfully regenerated personality for ${currentPersonality}!`);
        } else {
          showAlert('error', 'Regeneration failed: ' + (data.message || data.error));
        }
      } catch (error: any) {
        showAlert('error', 'Network error: ' + error.message);
      }
    }

    function cancelEdit() {
      currentPersonality = null;
      arrayData = {};
      displayPersonalityList();
      const editorContent = document.getElementById('editor-content');
      if (editorContent) {
        editorContent.innerHTML = `
          <div class="no-selection">
            Select a personality to edit or create a new one
          </div>
        `;
      }
    }

    async function createNewPersonality() {
      const name = prompt('Enter name for new personality:');
      if (!name) return;
      
      if (personalities[name]) {
        showAlert('error', 'Personality already exists!');
        return;
      }
      
      if (confirm(`Would you like AI to generate a personality for "${name}"?\n\nClick OK to use AI generation, or Cancel to create manually.`)) {
        generateWithAI(name);
      } else {
        const newPersonality = {
          play_style: "balanced",
          default_confidence: "confident",
          default_attitude: "focused",
          personality_traits: {
            bluff_tendency: 0.5,
            aggression: 0.5,
            chattiness: 0.5,
            emoji_usage: 0.3
          },
          verbal_tics: [],
          physical_tics: []
        };
        
        personalities[name] = newPersonality;
        displayPersonalityList();
        selectPersonality(name);
      }
    }

    async function generateWithAI(name: string) {
      showAlert('info', `Generating personality for ${name}...`);
      
      try {
        const response = await fetch(`${config.API_URL}/api/generate_personality`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name })
        });
        
        const data = await response.json();
        
        if (data.success) {
          personalities[name] = data.personality;
          displayPersonalityList();
          selectPersonality(name);
          showAlert('success', `AI generated personality for ${name}! Review and save if you're happy with it.`);
        } else {
          showAlert('error', 'Generation failed: ' + (data.message || data.error));
          createManualPersonality(name);
        }
      } catch (error: any) {
        showAlert('error', 'Network error: ' + error.message);
        createManualPersonality(name);
      }
    }

    function createManualPersonality(name: string) {
      const newPersonality = {
        play_style: "balanced",
        default_confidence: "confident",
        default_attitude: "focused",
        personality_traits: {
          bluff_tendency: 0.5,
          aggression: 0.5,
          chattiness: 0.5,
          emoji_usage: 0.3
        },
        verbal_tics: [],
        physical_tics: []
      };
      
      personalities[name] = newPersonality;
      displayPersonalityList();
      selectPersonality(name);
    }

    function showAlert(type: string, message: string) {
      const alertDiv = document.getElementById('alert');
      if (!alertDiv) return;
      
      alertDiv.className = `alert alert-${type}`;
      alertDiv.textContent = message;
      alertDiv.style.display = 'block';
      
      setTimeout(() => {
        alertDiv.style.display = 'none';
      }, 5000);
    }

    // Make createNewPersonality available globally
    (window as any).createNewPersonality = createNewPersonality;
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0a0a0a', padding: '20px' }}>
      <style dangerouslySetInnerHTML={{ __html: `
        body {
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        }
        .container {
          background: rgba(20, 20, 20, 0.95);
          padding: 30px;
          border-radius: 15px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.5);
          border: 1px solid rgba(61, 139, 55, 0.3);
          max-width: 1400px;
          margin: 0 auto;
          position: relative;
        }
        .back-button {
          position: absolute;
          top: 20px;
          left: 20px;
          padding: 10px 20px;
          background: rgba(61, 139, 55, 0.8);
          color: white;
          border: none;
          border-radius: 6px;
          cursor: pointer;
          font-size: 14px;
          font-weight: bold;
          transition: all 0.3s;
        }
        .back-button:hover {
          background: #5fb956;
          transform: translateX(-5px);
        }
        h1 {
          color: #5fb956;
          text-align: center;
          margin-bottom: 30px;
          text-shadow: 0 2px 10px rgba(95, 185, 86, 0.4);
        }
        h2 {
          color: #5fb956;
          border-bottom: 2px solid #3D8B37;
          padding-bottom: 10px;
          margin-bottom: 20px;
        }
        h3 {
          color: #5fb956;
          margin-top: 25px;
          margin-bottom: 15px;
        }
        .personality-grid {
          display: grid;
          grid-template-columns: 350px 1fr;
          gap: 20px;
          min-height: 600px;
        }
        .personality-list {
          background: rgba(34, 34, 34, 0.9);
          border-radius: 10px;
          padding: 20px;
          overflow-y: auto;
          max-height: 700px;
          border: 1px solid #3D8B37;
        }
        .personality-item {
          padding: 12px 15px;
          margin-bottom: 8px;
          background: rgba(255, 255, 255, 0.1);
          border: 1px solid #444;
          border-radius: 6px;
          cursor: pointer;
          transition: all 0.3s;
          color: #fff;
        }
        .personality-item:hover {
          background: rgba(61, 139, 55, 0.3);
          border-color: #3D8B37;
          transform: translateX(5px);
        }
        .personality-item.active {
          background: rgba(61, 139, 55, 0.5);
          color: white;
          border-color: #5fb956;
          box-shadow: 0 0 10px rgba(61, 139, 55, 0.5);
        }
        .editor-panel {
          background: rgba(34, 34, 34, 0.9);
          border-radius: 10px;
          padding: 25px;
          border: 1px solid #3D8B37;
        }
        .form-group {
          margin-bottom: 20px;
        }
        label {
          display: block;
          margin-bottom: 8px;
          font-weight: bold;
          color: #5fb956;
          font-size: 14px;
        }
        input[type="text"], textarea, select {
          width: 100%;
          padding: 10px 12px;
          border: 1px solid #444;
          border-radius: 6px;
          font-size: 14px;
          box-sizing: border-box;
          background: rgba(255, 255, 255, 0.1);
          color: #fff;
          transition: all 0.3s;
        }
        input[type="text"]:focus, textarea:focus {
          background: rgba(255, 255, 255, 0.15);
          border-color: #3D8B37;
          outline: none;
          box-shadow: 0 0 5px rgba(61, 139, 55, 0.5);
        }
        textarea {
          min-height: 60px;
          resize: vertical;
        }
        .trait-slider {
          margin-bottom: 25px;
          background: rgba(0, 0, 0, 0.3);
          padding: 15px;
          border-radius: 8px;
          border: 1px solid rgba(61, 139, 55, 0.2);
        }
        .trait-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 10px;
        }
        .trait-info {
          font-size: 12px;
          color: #888;
        }
        .elasticity-bounds {
          display: flex;
          justify-content: space-between;
          margin-top: 5px;
          font-size: 11px;
          color: #666;
        }
        .slider-container {
          display: flex;
          align-items: center;
          gap: 15px;
          position: relative;
        }
        input[type="range"] {
          flex: 1;
          height: 6px;
          -webkit-appearance: none;
          appearance: none;
          background: #444;
          outline: none;
          opacity: 0.8;
          transition: opacity 0.2s;
          border-radius: 3px;
        }
        input[type="range"]:hover {
          opacity: 1;
        }
        input[type="range"]::-webkit-slider-thumb {
          -webkit-appearance: none;
          appearance: none;
          width: 20px;
          height: 20px;
          background: #3D8B37;
          cursor: pointer;
          border-radius: 50%;
          box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }
        input[type="range"]::-moz-range-thumb {
          width: 20px;
          height: 20px;
          background: #3D8B37;
          cursor: pointer;
          border-radius: 50%;
          box-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }
        .slider-value {
          width: 60px;
          text-align: center;
          font-weight: bold;
          color: #5fb956;
          font-size: 16px;
        }
        .elasticity-range {
          position: absolute;
          top: -2px;
          height: 10px;
          background: rgba(61, 139, 55, 0.2);
          border: 1px solid rgba(61, 139, 55, 0.4);
          border-radius: 3px;
          pointer-events: none;
        }
        .button-group {
          display: flex;
          gap: 10px;
          margin-top: 20px;
        }
        .btn {
          padding: 12px 24px;
          border: none;
          border-radius: 6px;
          cursor: pointer;
          font-size: 14px;
          font-weight: bold;
          transition: all 0.3s;
        }
        .btn-primary {
          background: #3D8B37;
          color: white;
          border: 1px solid #3D8B37;
        }
        .btn-primary:hover {
          background: #5fb956;
          transform: translateY(-2px);
          box-shadow: 0 4px 10px rgba(61, 139, 55, 0.5);
        }
        .btn-success {
          background: #3D8B37;
          color: white;
          border: 1px solid #3D8B37;
        }
        .btn-success:hover {
          background: #5fb956;
          box-shadow: 0 4px 10px rgba(61, 139, 55, 0.5);
        }
        .btn-danger {
          background: #dc3545;
          color: white;
          border: 1px solid #dc3545;
        }
        .btn-danger:hover {
          background: #c82333;
          transform: translateY(-2px);
        }
        .btn-secondary {
          background: #444;
          color: white;
          border: 1px solid #666;
        }
        .btn-secondary:hover {
          background: #666;
        }
        .btn-warning {
          background: #f0ad4e;
          color: #000;
          border: 1px solid #f0ad4e;
        }
        .btn-warning:hover {
          background: #ec971f;
          transform: translateY(-2px);
        }
        .alert {
          padding: 15px;
          margin-bottom: 20px;
          border-radius: 4px;
          display: none;
        }
        .alert-success {
          background: #d4edda;
          color: #155724;
          border: 1px solid #c3e6cb;
        }
        .alert-error {
          background: #f8d7da;
          color: #721c24;
          border: 1px solid #f5c6cb;
        }
        .alert-info {
          background: #d1ecf1;
          color: #0c5460;
          border: 1px solid #bee5eb;
        }
        .traits-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 20px;
        }
        .array-input {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .array-item {
          display: flex;
          gap: 5px;
          align-items: center;
        }
        .array-item input {
          flex: 1;
        }
        .array-item button {
          padding: 5px 10px;
          background: #dc3545;
          color: white;
          border: none;
          border-radius: 3px;
          cursor: pointer;
        }
        .add-item-btn {
          padding: 5px 15px;
          background: #28a745;
          color: white;
          border: none;
          border-radius: 3px;
          cursor: pointer;
          margin-top: 5px;
        }
        .no-selection {
          text-align: center;
          color: #999;
          padding: 40px;
        }
        .new-personality-btn {
          width: 100%;
          margin-bottom: 15px;
        }
      ` }} />
      
      <div className="container">
        <button className="back-button" onClick={onBack}>← Back to Menu</button>
        <h1>🎰 AI Poker Personality Manager 🎰</h1>
        
        <div id="alert" className="alert"></div>
        
        <div className="personality-grid">
          <div className="personality-list">
            <button className="btn btn-success new-personality-btn" onClick={() => (window as any).createNewPersonality?.()}>
              + Create New Personality
            </button>
            <div id="personality-list"></div>
          </div>
          
          <div className="editor-panel">
            <div id="editor-content">
              <div className="no-selection">
                Select a personality to edit or create a new one
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}