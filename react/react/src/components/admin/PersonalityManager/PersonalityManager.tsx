import { useState, useEffect, useCallback, useMemo } from 'react';
import { User, Brain, MessageCircle, Image as ImageIcon, Coins, Target } from 'lucide-react';
import { config } from '../../../config';
import { PageLayout, PageHeader } from '../../shared';
import { useViewport } from '../../../hooks/useViewport';
import { CollapsibleSection } from '../shared/CollapsibleSection';
import '../AdminShared.css';
import './PersonalityManager.css';

import type {
  PersonalityData,
  PersonalityAnchors,
  AlertState,
  ModalState,
  SpotTendency,
} from './types';
import { getDefaultAnchors, classifyArchetype } from './personalityUtils';
import { MenuIcon } from './icons';
import { TraitSlider } from './TraitSlider';
import { ArrayInput } from './ArrayInput';
import { MasterList } from './MasterList';
import { CharacterSelector } from './CharacterSelector';
import { ConfirmModal } from './ConfirmModal';
import { CreateModal } from './CreateModal';
import { AvatarImageManager } from './AvatarImageManager';
import { SkillTierSelect } from './SkillTierSelect';
import { SpotTendenciesEditor } from './SpotTendenciesEditor';
import { BankrollKnobsSection } from './sections/BankrollKnobsSection';
import { StakingProfileSection } from './sections/StakingProfileSection';
import { StakerSideProfileSection } from './sections/StakerSideProfileSection';

interface PersonalityManagerProps {
  onBack?: () => void;
  embedded?: boolean;
}

export function PersonalityManager({ onBack, embedded = false }: PersonalityManagerProps) {
  // Responsive breakpoints
  const { isDesktop, isTablet, isMobile } = useViewport();

  // Core state
  const [personalities, setPersonalities] = useState<Record<string, PersonalityData>>({});
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [formData, setFormData] = useState<PersonalityData | null>(null);
  const [originalData, setOriginalData] = useState<PersonalityData | null>(null);

  // Categories and metadata
  const [categories, setCategories] = useState<Record<string, string[]>>({
    standard: [],
    mine: [],
  });
  const [personalityMeta, setPersonalityMeta] = useState<
    Record<string, { visibility?: string; owner_id?: string }>
  >({});
  const [isAdmin, setIsAdmin] = useState(false);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  // UI state
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [modal, setModal] = useState<ModalState>({ type: null });
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [masterSearch, setMasterSearch] = useState('');
  const [masterPanelOpen, setMasterPanelOpen] = useState(false);

  // Accordion state
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    basic: true,
    anchors: false,
    strategy: false,
    tics: false,
    avatar: false,
    bankroll: false,
    staking: false,
    stakerProfile: false,
  });

  // Load personalities on mount
  useEffect(() => {
    loadPersonalities();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-dismiss alerts
  useEffect(() => {
    if (alert) {
      const timer = setTimeout(() => setAlert(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [alert]);

  const loadPersonalities = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personalities`, {
        credentials: 'include',
      });
      const data = await response.json();
      if (data.success) {
        setPersonalities(data.personalities);
        if (data.categories) setCategories(data.categories);
        if (data.metadata) setPersonalityMeta(data.metadata);
        if (data.is_admin !== undefined) setIsAdmin(data.is_admin);
        if (data.user_id) setCurrentUserId(data.user_id);
      } else {
        showAlert('error', 'Failed to load personalities: ' + data.error);
      }
    } catch {
      showAlert('error', 'Error loading personalities');
    } finally {
      setLoading(false);
    }
  };

  const showAlert = (type: AlertState['type'], message: string) => {
    setAlert({ type, message });
  };

  const toggleSection = (section: string) => {
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  };

  const selectPersonality = useCallback(
    (name: string) => {
      const data = personalities[name];
      if (data) {
        setSelectedName(name);
        setFormData({ ...data });
        setOriginalData({ ...data });
        // Open basic section by default
        setOpenSections((prev) => ({ ...prev, basic: true }));
      }
    },
    [personalities]
  );

  const updateFormData = useCallback((updates: Partial<PersonalityData>) => {
    setFormData((prev) => (prev ? { ...prev, ...updates } : null));
  }, []);

  const updateAnchor = useCallback((field: keyof PersonalityAnchors, value: number) => {
    setFormData((prev) => {
      if (!prev) return null;
      return {
        ...prev,
        anchors: {
          ...getDefaultAnchors(),
          ...prev.anchors,
          [field]: value,
        },
      };
    });
  }, []);

  const handleVisibilityChange = async (newVisibility: string) => {
    if (!selectedName) return;
    try {
      const response = await fetch(
        `${config.API_URL}/api/personality/${encodeURIComponent(selectedName)}/visibility`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ visibility: newVisibility }),
        }
      );
      const data = await response.json();
      if (data.success) {
        showAlert('success', `${selectedName} is now ${newVisibility}`);
        setPersonalityMeta((prev) => ({
          ...prev,
          [selectedName]: { ...prev[selectedName], visibility: newVisibility },
        }));
        // Re-categorize: move personality between categories
        setCategories((prev) => {
          const updated: Record<string, string[]> = {};
          for (const [key, names] of Object.entries(prev)) {
            updated[key] = names.filter((n) => n !== selectedName);
          }
          const targetCategory =
            newVisibility === 'disabled'
              ? 'disabled'
              : newVisibility === 'private'
                ? 'mine'
                : 'standard';
          if (!updated[targetCategory]) updated[targetCategory] = [];
          updated[targetCategory].push(selectedName);
          return updated;
        });
      } else {
        showAlert('error', data.error || 'Failed to update visibility');
      }
    } catch {
      showAlert('error', 'Error updating visibility');
    }
  };

  const handleSave = async () => {
    if (!selectedName || !formData) return;

    setSaving(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(formData),
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Saved ${selectedName} successfully`);
        setPersonalities((prev) => ({ ...prev, [selectedName]: formData }));
        setOriginalData({ ...formData });
      } else {
        showAlert('error', 'Failed to save: ' + data.error);
      }
    } catch {
      showAlert('error', 'Error saving personality');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedName) return;

    setSaving(true);
    try {
      const response = await fetch(`${config.API_URL}/api/personality/${selectedName}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Deleted ${selectedName}`);
        setPersonalities((prev) => {
          const next = { ...prev };
          delete next[selectedName];
          return next;
        });
        setSelectedName(null);
        setFormData(null);
        setOriginalData(null);
      } else {
        showAlert('error', 'Failed to delete: ' + data.error);
      }
    } catch {
      showAlert('error', 'Error deleting personality');
    } finally {
      setSaving(false);
      setModal({ type: null });
    }
  };

  const handleRegenerate = async () => {
    if (!selectedName) return;

    setSaving(true);
    showAlert('info', `Regenerating personality for ${selectedName}...`);

    try {
      const response = await fetch(`${config.API_URL}/api/generate_personality`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ name: selectedName, force: true }),
      });
      const data = await response.json();

      if (data.success) {
        showAlert('success', `Regenerated ${selectedName} with AI`);
        setPersonalities((prev) => ({ ...prev, [selectedName]: data.personality }));
        setFormData({ ...data.personality });
        setOriginalData({ ...data.personality });
      } else {
        showAlert('error', 'Regeneration failed: ' + (data.message || data.error));
      }
    } catch {
      showAlert('error', 'Error regenerating personality');
    } finally {
      setSaving(false);
      setModal({ type: null });
    }
  };

  const handleCreateManual = (name: string) => {
    const newPersonality: PersonalityData = {
      play_style: 'balanced',
      default_confidence: 'confident',
      default_attitude: 'focused',
      anchors: getDefaultAnchors(),
      verbal_tics: [],
      physical_tics: [],
    };

    setPersonalities((prev) => ({ ...prev, [name]: newPersonality }));
    setSelectedName(name);
    setFormData({ ...newPersonality });
    setOriginalData({ ...newPersonality });
    setModal({ type: null });
    showAlert('success', `Created ${name}. Don't forget to save!`);
  };

  const handleCreateWithAI = async (name: string) => {
    setSaving(true);
    showAlert('info', `Generating personality for ${name}...`);

    try {
      const response = await fetch(`${config.API_URL}/api/generate_personality`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ name }),
      });
      const data = await response.json();

      if (data.success) {
        setPersonalities((prev) => ({ ...prev, [name]: data.personality }));
        setSelectedName(name);
        setFormData({ ...data.personality });
        setOriginalData({ ...data.personality });
        showAlert('success', `AI generated ${name}! Review and save.`);
      } else {
        showAlert('error', 'Generation failed: ' + (data.message || data.error));
        handleCreateManual(name);
      }
    } catch {
      showAlert('error', 'Error generating personality');
      handleCreateManual(name);
    } finally {
      setSaving(false);
      setModal({ type: null });
    }
  };

  const handleSaveAvatarDescription = async () => {
    if (!selectedName || !formData) return;

    try {
      const response = await fetch(
        `${config.API_URL}/api/personality/${selectedName}/avatar-description`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ avatar_description: formData.avatar_description || '' }),
        }
      );
      const data = await response.json();

      if (data.success) {
        showAlert('success', 'Avatar description saved');
      } else {
        showAlert('error', 'Failed to save description');
      }
    } catch {
      showAlert('error', 'Error saving description');
    }
  };

  const handleCancel = () => {
    if (originalData) {
      setFormData({ ...originalData });
    } else {
      setSelectedName(null);
      setFormData(null);
    }
  };

  const hasChanges =
    formData && originalData && JSON.stringify(formData) !== JSON.stringify(originalData);

  // Build grouped character list: mine first, then standard, then disabled (admin only)
  const characterGroups = useMemo(() => {
    const groups: { label: string; names: string[] }[] = [];
    const mine = (categories.mine || []).slice().sort();
    const standard = (categories.standard || []).slice().sort();
    const disabled = (categories.disabled || []).slice().sort();
    if (mine.length > 0) groups.push({ label: 'My Characters', names: mine });
    if (standard.length > 0) groups.push({ label: 'Standard', names: standard });
    if (disabled.length > 0) groups.push({ label: 'Disabled', names: disabled });
    return groups;
  }, [categories]);

  const characterNames = useMemo(() => characterGroups.flatMap((g) => g.names), [characterGroups]);

  // Safely merge API data with defaults to handle missing/partial data
  const anchors: PersonalityAnchors = {
    ...getDefaultAnchors(),
    ...(formData?.anchors || {}),
  };

  const archetype = classifyArchetype(anchors.baseline_looseness, anchors.baseline_aggression);

  // Editor sections (scrollable content)
  const editorSections =
    selectedName && formData ? (
      <div className="pm-sections">
        {/* Basic Info */}
        <CollapsibleSection
          title="Basic Info"
          icon={<User size={20} />}
          isOpen={openSections.basic}
          onToggle={() => toggleSection('basic')}
        >
          <div className="admin-form-group">
            <label className="admin-label" htmlFor="play_style">
              Play Style
            </label>
            <input
              id="play_style"
              type="text"
              className="admin-input"
              value={formData.play_style || ''}
              onChange={(e) => updateFormData({ play_style: e.target.value })}
              placeholder="e.g., aggressive and boastful"
            />
          </div>
          <div className="admin-form-row">
            <div className="admin-form-group">
              <label className="admin-label" htmlFor="confidence">
                Confidence
              </label>
              <input
                id="confidence"
                type="text"
                className="admin-input"
                value={formData.default_confidence || ''}
                onChange={(e) => updateFormData({ default_confidence: e.target.value })}
                placeholder="e.g., supreme"
              />
            </div>
            <div className="admin-form-group">
              <label className="admin-label" htmlFor="attitude">
                Attitude
              </label>
              <input
                id="attitude"
                type="text"
                className="admin-input"
                value={formData.default_attitude || ''}
                onChange={(e) => updateFormData({ default_attitude: e.target.value })}
                placeholder="e.g., domineering"
              />
            </div>
          </div>
        </CollapsibleSection>

        {/* Psychology Anchors */}
        <CollapsibleSection
          title="Psychology Anchors"
          icon={<Brain size={20} />}
          isOpen={openSections.anchors}
          onToggle={() => toggleSection('anchors')}
          badge={archetype.label}
        >
          <p className="admin-help-text" style={{ marginTop: 0, marginBottom: 'var(--space-4)' }}>
            These anchors control poker behavior and archetype classification.
          </p>

          <h4 className="pm-anchor-group-title">Play Style</h4>
          <TraitSlider
            id="baseline_looseness"
            label="Tight → Loose"
            value={anchors.baseline_looseness}
            elasticity={0}
            onChange={(v) => updateAnchor('baseline_looseness', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Hand range width — how many hands to play</p>
          <TraitSlider
            id="baseline_aggression"
            label="Passive → Aggressive"
            value={anchors.baseline_aggression}
            elasticity={0}
            onChange={(v) => updateAnchor('baseline_aggression', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Bet/raise frequency</p>

          <h4 className="pm-anchor-group-title">Psychology</h4>
          <TraitSlider
            id="ego"
            label="Stable → Fragile"
            value={anchors.ego}
            elasticity={0}
            onChange={(v) => updateAnchor('ego', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Confidence brittleness after losses</p>
          <TraitSlider
            id="poise"
            label="Volatile → Composed"
            value={anchors.poise}
            elasticity={0}
            onChange={(v) => updateAnchor('poise', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Composure resistance to tilt</p>
          <TraitSlider
            id="expressiveness"
            label="Poker Face → Open Book"
            value={anchors.expressiveness}
            elasticity={0}
            onChange={(v) => updateAnchor('expressiveness', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Emotional transparency in chat</p>
          <TraitSlider
            id="self_belief"
            label="Self-Doubt → Bravado"
            value={anchors.self_belief}
            elasticity={0}
            onChange={(v) => updateAnchor('self_belief', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">Unshakable self-belief — bravado and delusion vs. doubt</p>

          <h4 className="pm-anchor-group-title">Behavior</h4>
          <TraitSlider
            id="risk_identity"
            label="Risk-Averse → Risk-Seeking"
            value={anchors.risk_identity}
            elasticity={0}
            onChange={(v) => updateAnchor('risk_identity', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <TraitSlider
            id="adaptation_bias"
            label="Static → Adaptive"
            value={anchors.adaptation_bias}
            elasticity={0}
            onChange={(v) => updateAnchor('adaptation_bias', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <TraitSlider
            id="baseline_energy"
            label="Reserved → Animated"
            value={anchors.baseline_energy}
            elasticity={0}
            onChange={(v) => updateAnchor('baseline_energy', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <TraitSlider
            id="recovery_rate"
            label="Slow Recovery → Fast Recovery"
            value={anchors.recovery_rate}
            elasticity={0}
            onChange={(v) => updateAnchor('recovery_rate', v)}
            onElasticityChange={() => {}}
            showElasticity={false}
          />
          <p className="admin-help-text">How quickly mood returns to baseline</p>
        </CollapsibleSection>

        {/* Strategy & Tells: skill tier + exploitable spot tendencies */}
        <CollapsibleSection
          title="Strategy & Tells"
          icon={<Target size={20} />}
          isOpen={openSections.strategy}
          onToggle={() => toggleSection('strategy')}
          badge={`${formData.spot_tendencies?.length || 0 || ''}`}
        >
          <SkillTierSelect
            value={formData.skill ?? ''}
            adaptationBias={anchors.adaptation_bias}
            onChange={(value) => updateFormData({ skill: value })}
          />
          <h4 className="pm-anchor-group-title">Spot Tendencies</h4>
          <SpotTendenciesEditor
            value={(formData.spot_tendencies as SpotTendency[]) || []}
            onChange={(next) => updateFormData({ spot_tendencies: next })}
          />
        </CollapsibleSection>

        {/* Verbal & Physical Tics */}
        <CollapsibleSection
          title="Quirks & Tics"
          icon={<MessageCircle size={20} />}
          isOpen={openSections.tics}
          onToggle={() => toggleSection('tics')}
          badge={`${(formData.verbal_tics?.length || 0) + (formData.physical_tics?.length || 0)}`}
        >
          <ArrayInput
            label="Verbal Tics"
            items={formData.verbal_tics || []}
            onChange={(items) => updateFormData({ verbal_tics: items })}
            placeholder="e.g., Says 'you know' frequently"
          />
          <ArrayInput
            label="Physical Tics"
            items={formData.physical_tics || []}
            onChange={(items) => updateFormData({ physical_tics: items })}
            placeholder="e.g., Taps chips when nervous"
          />
        </CollapsibleSection>

        {/* Avatar Images */}
        <CollapsibleSection
          title="Avatar Images"
          icon={<ImageIcon size={20} />}
          isOpen={openSections.avatar}
          onToggle={() => toggleSection('avatar')}
        >
          <AvatarImageManager
            personalityName={selectedName}
            avatarDescription={formData.avatar_description || ''}
            onDescriptionChange={(desc) => updateFormData({ avatar_description: desc })}
            onDescriptionSave={handleSaveAvatarDescription}
          />
        </CollapsibleSection>

        {/* Cash-mode Bankroll Knobs (admin only) */}
        {isAdmin && (
          <CollapsibleSection
            title="Bankroll Knobs"
            icon={<Coins size={20} />}
            isOpen={openSections.bankroll}
            onToggle={() => toggleSection('bankroll')}
          >
            <BankrollKnobsSection personalityName={selectedName} showAlert={showAlert} />
          </CollapsibleSection>
        )}

        {/* Staking Profile — Borrower side (admin only): do they
            accept stakes, and at what trust threshold. */}
        {isAdmin && (
          <CollapsibleSection
            title="Staking Profile — Borrower"
            icon={<Coins size={20} />}
            isOpen={openSections.staking}
            onToggle={() => toggleSection('staking')}
          >
            <StakingProfileSection personalityName={selectedName} showAlert={showAlert} />
          </CollapsibleSection>
        )}

        {/* Staking Profile — Staker side (admin only): what loan
            terms they offer when OTHERS ask them for a stake-up. */}
        {isAdmin && (
          <CollapsibleSection
            title="Staking Profile — Staker"
            icon={<Coins size={20} />}
            isOpen={openSections.stakerProfile}
            onToggle={() => toggleSection('stakerProfile')}
          >
            <StakerSideProfileSection personalityName={selectedName} showAlert={showAlert} />
          </CollapsibleSection>
        )}
      </div>
    ) : null;

  // Action bar (fixed at bottom)
  const actionBar =
    selectedName && formData ? (
      <div className={isMobile ? 'pm-actions' : 'admin-detail__footer'}>
        <div className={isMobile ? 'pm-actions__secondary' : 'admin-detail__footer-secondary'}>
          <button
            type="button"
            className="admin-btn admin-btn--secondary"
            onClick={() => setModal({ type: 'regenerate' })}
            disabled={saving}
          >
            ✨ AI Regen
          </button>
          <button
            type="button"
            className="admin-btn admin-btn--danger"
            onClick={() => setModal({ type: 'delete' })}
            disabled={saving}
          >
            Delete
          </button>
        </div>
        <div className={isMobile ? 'pm-actions__primary' : 'admin-detail__footer-primary'}>
          {hasChanges && (
            <button
              type="button"
              className="admin-btn admin-btn--secondary"
              onClick={handleCancel}
              disabled={saving}
            >
              Cancel
            </button>
          )}
          <button
            type="button"
            className="admin-btn admin-btn--primary"
            onClick={handleSave}
            disabled={saving || !hasChanges}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
    ) : null;

  // Empty state content
  const emptyContent = (
    <div className={isTablet ? 'admin-detail__empty' : 'admin-empty'}>
      <div
        className={isTablet ? 'admin-detail__empty-icon' : 'admin-empty__icon'}
        style={{ fontSize: '64px', opacity: 0.5 }}
      >
        🎭
      </div>
      <h3 className={isTablet ? 'admin-detail__empty-title' : 'admin-empty__title'}>
        No Character Selected
      </h3>
      <p className={isTablet ? 'admin-detail__empty-description' : 'admin-empty__description'}>
        {isTablet
          ? 'Select a character from the list or create a new one'
          : 'Choose a character above or create a new one'}
      </p>
      <button
        type="button"
        className="admin-btn admin-btn--primary admin-btn--lg"
        onClick={() => setModal({ type: 'create' })}
      >
        Create New Character
      </button>
    </div>
  );

  const content = (
    <>
      {/* Alert Toast */}
      {alert && (
        <div className="admin-toast-container">
          <div className={`admin-alert admin-alert--${alert.type}`}>
            <span className="admin-alert__icon">
              {alert.type === 'success' && '✓'}
              {alert.type === 'error' && '✕'}
              {alert.type === 'info' && 'ℹ'}
            </span>
            <span className="admin-alert__content">{alert.message}</span>
            <button className="admin-alert__dismiss" onClick={() => setAlert(null)}>
              ×
            </button>
          </div>
        </div>
      )}

      {/* Loading State */}
      {loading ? (
        <div className="admin-loading">
          <div className="admin-loading__spinner" />
          <span className="admin-loading__text">Loading personalities...</span>
        </div>
      ) : !isMobile ? (
        /* ==========================================
           TABLET & DESKTOP: Master-Detail Layout
           ========================================== */
        <div className="admin-master-detail">
          {/* Master Panel (sidebar) */}
          <aside
            className={`admin-master ${masterPanelOpen || isDesktop ? 'admin-master--open' : ''}`}
          >
            <MasterList
              characters={characterNames}
              groups={characterGroups}
              selected={selectedName}
              onSelect={(name) => {
                selectPersonality(name);
                if (!isDesktop) setMasterPanelOpen(false);
              }}
              onCreate={() => {
                setMasterPanelOpen(false);
                setModal({ type: 'create' });
              }}
              search={masterSearch}
              onSearchChange={setMasterSearch}
              personalityMeta={personalityMeta}
            />
          </aside>

          {/* Detail Panel */}
          <main className="admin-detail">
            {/* Tablet toggle button (hidden on desktop) */}
            {!isDesktop && (
              <button
                type="button"
                className="admin-master-toggle"
                onClick={() => setMasterPanelOpen(!masterPanelOpen)}
              >
                <MenuIcon />
                <span>{selectedName || 'Select Character'}</span>
              </button>
            )}

            {/* Detail header when character selected */}
            {selectedName &&
              formData &&
              (() => {
                const meta = personalityMeta[selectedName];
                const currentVis = meta?.visibility || 'public';
                const isOwner = !!currentUserId && meta?.owner_id === currentUserId;
                const canChangeVisibility = isAdmin || isOwner;
                // PRH-27: publishing is admin-only. A non-admin owner can keep
                // their personality private (or un-publish a legacy public one)
                // but can't make it public — the server rejects it too.
                const visibilityOptions: readonly ('public' | 'private' | 'disabled')[] = isAdmin
                  ? ['public', 'private', 'disabled']
                  : ['private'];
                return (
                  <div className="admin-detail__header">
                    <div>
                      <h2 className="admin-detail__title">{selectedName}</h2>
                      <p className="admin-detail__subtitle">
                        {formData.play_style || 'No play style defined'}
                      </p>
                    </div>
                    {canChangeVisibility && (
                      <div className="pm-visibility-toggle">
                        {visibilityOptions.map((vis) => (
                          <button
                            key={vis}
                            type="button"
                            className={`pm-visibility-toggle__btn pm-visibility-toggle__btn--${vis} ${currentVis === vis ? 'pm-visibility-toggle__btn--active' : ''}`}
                            onClick={() => handleVisibilityChange(vis)}
                            disabled={currentVis === vis}
                          >
                            {vis === 'public'
                              ? 'Public'
                              : vis === 'private'
                                ? 'Private'
                                : 'Disabled'}
                          </button>
                        ))}
                      </div>
                    )}
                    {!canChangeVisibility && currentVis !== 'public' && (
                      <span className={`pm-visibility-badge pm-visibility-badge--${currentVis}`}>
                        {currentVis}
                      </span>
                    )}
                  </div>
                );
              })()}

            {/* Detail content (scrollable) */}
            <div className="admin-detail__content">{editorSections || emptyContent}</div>

            {/* Action bar (fixed at bottom) */}
            {actionBar}
          </main>

          {/* Backdrop for tablet sidebar */}
          {!isDesktop && masterPanelOpen && (
            <div
              className="pm-sheet-backdrop pm-sheet-backdrop--visible"
              onClick={() => setMasterPanelOpen(false)}
            />
          )}
        </div>
      ) : (
        /* ==========================================
           MOBILE: Original Bottom Sheet Layout
           ========================================== */
        <div className={`pm-container${embedded ? ' pm-container--embedded' : ''}`}>
          {/* Character Selector Trigger */}
          <button
            type="button"
            className="pm-selector-trigger"
            onClick={() => setSelectorOpen(true)}
          >
            {selectedName ? (
              <>
                <span className="pm-selector-trigger__avatar">{selectedName.charAt(0)}</span>
                <span className="pm-selector-trigger__name">{selectedName}</span>
                <span className="pm-selector-trigger__change">Change</span>
              </>
            ) : (
              <>
                <span className="pm-selector-trigger__icon">
                  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                    <circle cx="10" cy="6" r="4" stroke="currentColor" strokeWidth="1.5" />
                    <path
                      d="M3 18C3 14.134 6.134 11 10 11C13.866 11 17 14.134 17 18"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                    />
                  </svg>
                </span>
                <span className="pm-selector-trigger__placeholder">Select a character to edit</span>
              </>
            )}
            <svg
              className="pm-selector-trigger__chevron"
              width="20"
              height="20"
              viewBox="0 0 20 20"
              fill="none"
            >
              <path
                d="M5 7.5L10 12.5L15 7.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>

          {/* Character Selector Bottom Sheet */}
          <CharacterSelector
            characters={characterNames}
            groups={characterGroups}
            selected={selectedName}
            onSelect={selectPersonality}
            onCreate={() => {
              setSelectorOpen(false);
              setModal({ type: 'create' });
            }}
            isOpen={selectorOpen}
            onClose={() => setSelectorOpen(false)}
            personalityMeta={personalityMeta}
          />

          {/* Editor or Empty State */}
          {selectedName && formData ? (
            <div className="pm-editor">
              {editorSections}
              {actionBar}
            </div>
          ) : (
            emptyContent
          )}

          {/* Floating Create Button */}
          <button
            type="button"
            className="pm-fab"
            onClick={() => setModal({ type: 'create' })}
            aria-label="Create new character"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 5V19M5 12H19"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      )}

      {/* Modals */}
      {modal.type === 'delete' && (
        <ConfirmModal
          title="Delete Character"
          message={`Are you sure you want to delete "${selectedName}"? This action cannot be undone.`}
          confirmLabel="Delete"
          confirmVariant="danger"
          onConfirm={handleDelete}
          onCancel={() => setModal({ type: null })}
          isLoading={saving}
        />
      )}

      {modal.type === 'regenerate' && (
        <ConfirmModal
          title="Regenerate with AI"
          message={`This will replace "${selectedName}" with a new AI-generated personality. Your current changes will be lost.`}
          confirmLabel="Regenerate"
          confirmVariant="warning"
          onConfirm={handleRegenerate}
          onCancel={() => setModal({ type: null })}
          isLoading={saving}
        />
      )}

      {modal.type === 'create' && (
        <CreateModal
          existingNames={characterNames}
          onCreateManual={handleCreateManual}
          onCreateWithAI={handleCreateWithAI}
          onCancel={() => setModal({ type: null })}
          isLoading={saving}
        />
      )}
    </>
  );

  // If embedded, return content directly without PageLayout wrapper
  if (embedded) {
    return content;
  }

  // Otherwise wrap in PageLayout
  return (
    <PageLayout variant="top" glowColor="gold" maxWidth="lg">
      <PageHeader
        title="Character Manager"
        subtitle="Create and customize AI opponents"
        onBack={onBack}
        titleVariant="primary"
      />
      {content}
    </PageLayout>
  );
}
