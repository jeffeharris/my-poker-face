import { useEffect } from 'react';
import { config } from '../../config';
import { PageLayout, PageHeader } from '../shared';
import './PersonalityManager.css';

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
        <div class="personality-manager__item ${currentPersonality === name ? 'active' : ''}"
             data-name="${name}">
          ${name}
        </div>
      `).join('');

      // Add click handlers
      listDiv.querySelectorAll('.personality-manager__item').forEach(item => {
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
        <h2 class="personality-manager__editor-title">Editing: ${name}</h2>

        <div class="personality-manager__form-group">
          <label class="personality-manager__label" for="play_style">Play Style</label>
          <input class="personality-manager__input" type="text" id="play_style" value="${data.play_style || ''}"
                 placeholder="e.g., aggressive and boastful">
        </div>

        <div class="personality-manager__traits-grid">
          <div class="personality-manager__form-group">
            <label class="personality-manager__label" for="default_confidence">Default Confidence</label>
            <input class="personality-manager__input" type="text" id="default_confidence" value="${data.default_confidence || ''}"
                   placeholder="e.g., supreme">
          </div>

          <div class="personality-manager__form-group">
            <label class="personality-manager__label" for="default_attitude">Default Attitude</label>
            <input class="personality-manager__input" type="text" id="default_attitude" value="${data.default_attitude || ''}"
                   placeholder="e.g., domineering">
          </div>
        </div>

        <h3 class="personality-manager__section-title">Personality Traits</h3>
        ${renderTraitSlider('bluff_tendency', 'Bluff Tendency', data.personality_traits?.bluff_tendency || 0.5, elasticityConfig.trait_elasticity?.bluff_tendency || 0.3)}
        ${renderTraitSlider('aggression', 'Aggression', data.personality_traits?.aggression || 0.5, elasticityConfig.trait_elasticity?.aggression || 0.3)}
        ${renderTraitSlider('chattiness', 'Chattiness', data.personality_traits?.chattiness || 0.5, elasticityConfig.trait_elasticity?.chattiness || 0.5)}
        ${renderTraitSlider('emoji_usage', 'Emoji Usage', data.personality_traits?.emoji_usage || 0.3, elasticityConfig.trait_elasticity?.emoji_usage || 0.3)}

        <h3 class="personality-manager__section-title">Elasticity Settings</h3>
        <div class="personality-manager__trait-slider">
          <div class="personality-manager__trait-header">
            <label class="personality-manager__trait-label">Mood Elasticity</label>
            <span class="personality-manager__trait-info">How reactive mood changes are</span>
          </div>
          <div class="personality-manager__slider-container">
            <input class="personality-manager__range" type="range" id="mood_elasticity" min="0" max="100"
                   value="${(elasticityConfig.mood_elasticity || 0.4) * 100}">
            <span class="personality-manager__slider-value" id="mood_elasticity_value">
              ${Math.round((elasticityConfig.mood_elasticity || 0.4) * 100)}%
            </span>
          </div>
        </div>

        <div class="personality-manager__trait-slider">
          <div class="personality-manager__trait-header">
            <label class="personality-manager__trait-label">Recovery Rate</label>
            <span class="personality-manager__trait-info">How fast traits return to baseline</span>
          </div>
          <div class="personality-manager__slider-container">
            <input class="personality-manager__range" type="range" id="recovery_rate" min="0" max="20"
                   value="${(elasticityConfig.recovery_rate || 0.1) * 100}">
            <span class="personality-manager__slider-value" id="recovery_rate_value">
              ${Math.round((elasticityConfig.recovery_rate || 0.1) * 100)}%
            </span>
          </div>
        </div>

        <h3 class="personality-manager__section-title">Verbal Tics</h3>
        <div class="personality-manager__array-input" id="verbal_tics">
          ${(data.verbal_tics || []).map((tic: string, i: number) => `
            <div class="personality-manager__array-item">
              <input class="personality-manager__input" type="text" value="${tic}" data-index="${i}" data-field="verbal_tics">
              <button class="personality-manager__remove-btn" data-field="verbal_tics" data-index="${i}">×</button>
            </div>
          `).join('')}
        </div>
        <button class="personality-manager__add-btn" data-field="verbal_tics">+ Add Verbal Tic</button>

        <h3 class="personality-manager__section-title">Physical Tics</h3>
        <div class="personality-manager__array-input" id="physical_tics">
          ${(data.physical_tics || []).map((tic: string, i: number) => `
            <div class="personality-manager__array-item">
              <input class="personality-manager__input" type="text" value="${tic}" data-index="${i}" data-field="physical_tics">
              <button class="personality-manager__remove-btn" data-field="physical_tics" data-index="${i}">×</button>
            </div>
          `).join('')}
        </div>
        <button class="personality-manager__add-btn" data-field="physical_tics">+ Add Physical Tic</button>

        <div class="personality-manager__button-group">
          <button class="personality-manager__btn personality-manager__btn--primary" id="save-btn">Save Changes</button>
          <button class="personality-manager__btn personality-manager__btn--warning" id="regenerate-btn">Regenerate with AI</button>
          <button class="personality-manager__btn personality-manager__btn--danger" id="delete-btn">Delete Personality</button>
          <button class="personality-manager__btn personality-manager__btn--secondary" id="cancel-btn">Cancel</button>
        </div>
      `;

      // Helper function to render trait slider with elasticity
      function renderTraitSlider(traitId: string, label: string, value: number, elasticity: number) {
        const minValue = Math.max(0, value - elasticity);
        const maxValue = Math.min(1, value + elasticity);

        return `
          <div class="personality-manager__trait-slider">
            <div class="personality-manager__trait-header">
              <label class="personality-manager__trait-label">${label}</label>
              <span class="personality-manager__trait-info">Elasticity: ±${Math.round(elasticity * 100)}%</span>
            </div>
            <div class="personality-manager__slider-container">
              <div class="personality-manager__elasticity-range" style="left: ${minValue * 100}%; width: ${(maxValue - minValue) * 100}%;"></div>
              <input class="personality-manager__range" type="range" id="${traitId}" min="0" max="100"
                     value="${value * 100}">
              <span class="personality-manager__slider-value" id="${traitId}_value">
                ${Math.round(value * 100)}%
              </span>
            </div>
            <div class="personality-manager__elasticity-bounds">
              <span>Min: ${Math.round(minValue * 100)}%</span>
              <span>Max: ${Math.round(maxValue * 100)}%</span>
            </div>
            <div class="personality-manager__form-group" style="margin-top: 10px;">
              <label class="personality-manager__trait-label" for="${traitId}_elasticity" style="font-size: 12px;">Elasticity</label>
              <input class="personality-manager__range" type="range" id="${traitId}_elasticity" min="0" max="100"
                     value="${elasticity * 100}" style="height: 4px;">
              <span class="personality-manager__slider-value" id="${traitId}_elasticity_value" style="font-size: 12px;">
                ${Math.round(elasticity * 100)}%
              </span>
            </div>
          </div>
        `;
      }

      // Add event listeners
      document.querySelectorAll('.personality-manager__range').forEach(slider => {
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

          const rangeDiv = traitSlider.parentElement?.querySelector('.personality-manager__elasticity-range') as HTMLElement;
          if (rangeDiv) {
            rangeDiv.style.left = `${minValue * 100}%`;
            rangeDiv.style.width = `${(maxValue - minValue) * 100}%`;
          }

          const boundsDiv = traitSlider.closest('.personality-manager__trait-slider')?.querySelector('.personality-manager__elasticity-bounds') as HTMLElement;
          if (boundsDiv) {
            boundsDiv.innerHTML = `
              <span>Min: ${Math.round(minValue * 100)}%</span>
              <span>Max: ${Math.round(maxValue * 100)}%</span>
            `;
          }
        }
      }

      document.querySelectorAll('.personality-manager__array-item input').forEach(input => {
        input.addEventListener('change', (e) => {
          const target = e.target as HTMLInputElement;
          const field = target.getAttribute('data-field');
          const index = parseInt(target.getAttribute('data-index') || '0');
          if (field) updateArrayItem(field, index, target.value);
        });
      });

      document.querySelectorAll('.personality-manager__remove-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const target = e.target as HTMLElement;
          const field = target.getAttribute('data-field');
          const index = parseInt(target.getAttribute('data-index') || '0');
          if (field) removeArrayItem(field, index);
        });
      });

      document.querySelectorAll('.personality-manager__add-btn').forEach(btn => {
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
              <div class="personality-manager__no-selection">
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
          <div class="personality-manager__no-selection">
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
      const alertDiv = document.getElementById('personality-alert');
      if (!alertDiv) return;

      alertDiv.className = `personality-manager__alert personality-manager__alert--${type}`;
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
    <PageLayout variant="top" glowColor="sapphire" maxWidth="xl">
      <PageHeader
        title="AI Personality Manager"
        subtitle="Create and customize AI opponent personalities"
        onBack={onBack}
        titleVariant="primary"
      />

        <div id="personality-alert" className="personality-manager__alert"></div>

        <div className="personality-manager__grid">
          <div className="personality-manager__list-panel">
            <button
              className="personality-manager__new-btn"
              onClick={() => (window as any).createNewPersonality?.()}
            >
              + Create New Personality
            </button>
            <div id="personality-list"></div>
          </div>

          <div className="personality-manager__editor-panel">
            <div id="editor-content">
              <div className="personality-manager__no-selection">
                Select a personality to edit or create a new one
              </div>
            </div>
          </div>
        </div>
    </PageLayout>
  );
}
