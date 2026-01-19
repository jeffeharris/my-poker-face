# PersonalityManager Desktop Refactor Plan

## Overview

Refactor `PersonalityManager.tsx` and `PersonalityManager.css` to use the shared `AdminShared.css` styles for a consistent desktop admin experience.

**Files to modify:**
- `react/react/src/components/admin/PersonalityManager.tsx`
- `react/react/src/components/admin/PersonalityManager.css`

**Reference:**
- `react/react/src/components/admin/AdminShared.css` (import and use these classes)

---

## Step 1: Add AdminShared.css Import

In `PersonalityManager.tsx`, add the import:

```tsx
import './AdminShared.css';
import './PersonalityManager.css';
```

---

## Step 2: Replace Alert Toast

### Current (lines ~947-954 in TSX, lines ~13-83 in CSS)
```tsx
<div className={`pm-alert pm-alert--${alert.type}`}>
  <span className="pm-alert__icon">...</span>
  <span className="pm-alert__message">{alert.message}</span>
  <button className="pm-alert__close">×</button>
</div>
```

### Replace with:
```tsx
<div className="admin-toast-container">
  <div className={`admin-alert admin-alert--${alert.type}`}>
    <span className="admin-alert__icon">...</span>
    <span className="admin-alert__content">{alert.message}</span>
    <button className="admin-alert__dismiss" onClick={() => setAlert(null)}>×</button>
  </div>
</div>
```

### CSS to remove:
Delete `.pm-alert`, `.pm-alert--success`, `.pm-alert--error`, `.pm-alert--info`, `.pm-alert__icon`, `.pm-alert__message`, `.pm-alert__close`, and `@keyframes alertSlideIn`

---

## Step 3: Replace Loading State

### Current (lines ~959-961 in TSX, lines ~89-110 in CSS)
```tsx
<div className="pm-loading">
  <div className="pm-loading__spinner" />
</div>
```

### Replace with:
```tsx
<div className="admin-loading">
  <div className="admin-loading__spinner" />
  <span className="admin-loading__text">Loading personalities...</span>
</div>
```

### CSS to remove:
Delete `.pm-loading`, `.pm-loading__spinner`, and `@keyframes spin`

---

## Step 4: Replace Modal/Dialog

### Current ConfirmModal (lines ~228-244 in TSX)
```tsx
<div className="pm-modal-overlay" onClick={onCancel}>
  <div className="pm-modal" onClick={e => e.stopPropagation()}>
    <h3 className="pm-modal__title">{title}</h3>
    <p className="pm-modal__message">{message}</p>
    <div className="pm-modal__actions">
      <button className="pm-modal__btn pm-modal__btn--cancel">Cancel</button>
      <button className="pm-modal__btn pm-modal__btn--confirm">...</button>
    </div>
  </div>
</div>
```

### Replace with:
```tsx
<div className="admin-modal-overlay" onClick={onCancel}>
  <div className="admin-modal" onClick={e => e.stopPropagation()}>
    <div className="admin-modal__header">
      <h3 className="admin-modal__title">{title}</h3>
    </div>
    <div className="admin-modal__body">
      <p>{message}</p>
    </div>
    <div className="admin-modal__footer">
      <button className="admin-btn admin-btn--secondary" onClick={onCancel}>Cancel</button>
      <button className={`admin-btn ${variant === 'danger' ? 'admin-btn--danger' : 'admin-btn--primary'}`}>
        {confirmText}
      </button>
    </div>
  </div>
</div>
```

### CSS to remove:
Delete `.pm-modal-overlay`, `.pm-modal`, `.pm-modal__title`, `.pm-modal__message`, `.pm-modal__actions`, `.pm-modal__btn`, etc.

---

## Step 5: Replace CreateModal

### Current (lines ~285-329 in TSX)
Similar structure to ConfirmModal but with form fields.

### Replace with:
```tsx
<div className="admin-modal-overlay" onClick={onCancel}>
  <div className="admin-modal" onClick={e => e.stopPropagation()}>
    <div className="admin-modal__header">
      <h3 className="admin-modal__title">Create New Personality</h3>
      <button className="admin-modal__close" onClick={onCancel}>×</button>
    </div>
    <div className="admin-modal__body">
      <div className="admin-form-group">
        <label className="admin-label" htmlFor="new-personality-name">Character Name</label>
        <input
          id="new-personality-name"
          className="admin-input"
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="e.g., James Bond, Sherlock Holmes"
        />
        {error && <span className="admin-text-error" style={{fontSize: 'var(--font-size-xs)'}}>{error}</span>}
      </div>
      <div className="admin-flex admin-gap-3" style={{marginTop: 'var(--space-4)'}}>
        <button className="admin-btn admin-btn--primary" style={{flex: 1}} onClick={() => onSubmit(true)}>
          ✨ Generate with AI
        </button>
        <button className="admin-btn admin-btn--secondary" style={{flex: 1}} onClick={() => onSubmit(false)}>
          ✏️ Create Blank
        </button>
      </div>
    </div>
  </div>
</div>
```

---

## Step 6: Replace Empty State

### Current (lines ~1203-1210 in TSX)
```tsx
<div className="pm-empty">
  <div className="pm-empty__icon">🎭</div>
  <h3 className="pm-empty__title">No Character Selected</h3>
  <p className="pm-empty__text">Choose a character above or create a new one</p>
  <button className="pm-empty__create">+ Create New</button>
</div>
```

### Replace with:
```tsx
<div className="admin-empty">
  <div className="admin-empty__icon">🎭</div>
  <h3 className="admin-empty__title">No Character Selected</h3>
  <p className="admin-empty__description">Choose a character above or create a new one</p>
  <button className="admin-btn admin-btn--primary" onClick={...}>+ Create New</button>
</div>
```

### CSS to remove:
Delete `.pm-empty`, `.pm-empty__icon`, `.pm-empty__title`, `.pm-empty__text`, `.pm-empty__create`

---

## Step 7: Replace Action Buttons

### Current (lines ~1161-1199 in TSX)
```tsx
<div className="pm-actions">
  <div className="pm-actions__secondary">
    <button className="pm-actions__btn pm-actions__btn--ghost">Regenerate</button>
    <button className="pm-actions__btn pm-actions__btn--danger">Delete</button>
  </div>
  <div className="pm-actions__primary">
    <button className="pm-actions__btn pm-actions__btn--ghost">Cancel</button>
    <button className="pm-actions__btn pm-actions__btn--save">Save Changes</button>
  </div>
</div>
```

### Replace with:
```tsx
<div className="pm-actions">
  <div className="pm-actions__secondary">
    <button className="admin-btn admin-btn--ghost">✨ Regenerate</button>
    <button className="admin-btn admin-btn--danger">🗑️ Delete</button>
  </div>
  <div className="pm-actions__primary">
    <button className="admin-btn admin-btn--secondary" onClick={handleCancel}>Cancel</button>
    <button className="admin-btn admin-btn--primary" onClick={handleSave}>
      {saving ? <span className="admin-spinner" /> : null}
      Save Changes
    </button>
  </div>
</div>
```

### CSS to keep (layout only):
Keep `.pm-actions`, `.pm-actions__secondary`, `.pm-actions__primary` for layout

### CSS to remove:
Delete `.pm-actions__btn` and all variants

---

## Step 8: Replace Form Fields

### Current (lines ~1015-1044 in TSX)
```tsx
<div className="pm-field">
  <label className="pm-field__label" htmlFor="play_style">Play Style</label>
  <input className="pm-field__input" ... />
</div>
```

### Replace with:
```tsx
<div className="admin-form-group">
  <label className="admin-label" htmlFor="play_style">Play Style</label>
  <input className="admin-input" ... />
</div>
```

### For field rows:
```tsx
<div className="admin-form-row">
  <div className="admin-form-group">
    <label className="admin-label">Confidence</label>
    <input className="admin-input" ... />
  </div>
  <div className="admin-form-group">
    <label className="admin-label">Attitude</label>
    <input className="admin-input" ... />
  </div>
</div>
```

### CSS to remove:
Delete `.pm-field`, `.pm-field__label`, `.pm-field__input`, `.pm-field-row`

---

## Step 9: Replace Help Text

### Current
```tsx
<p className="pm-help-text">How reactive mood changes are to game events</p>
```

### Replace with:
```tsx
<p className="admin-help-text">How reactive mood changes are to game events</p>
```

### CSS to remove:
Delete `.pm-help-text`

---

## Step 10: Update CollapsibleSection Component

### Keep the component but update inner classes:

The `CollapsibleSection` component can stay as is since it's a custom component specific to this panel. Keep `.pm-section`, `.pm-section__header`, etc. in the CSS.

However, update button styling inside sections to use shared classes where appropriate.

---

## Step 11: Update Character Selector Sheet

The bottom sheet (`pm-sheet`) is mobile-specific UI. For desktop, consider:

1. **Option A**: Keep as-is for now (mobile-first approach)
2. **Option B**: Create a dropdown/select for desktop view using media queries

For now, keep the sheet but ensure it works well on desktop by adjusting max-width and positioning.

---

## Step 12: Update TraitSlider Component

The `TraitSlider` component (lines ~97-155 in TSX) is custom and specific to this panel. Keep `.pm-trait`, `.pm-trait__slider`, etc. but ensure colors match the design system:

- Keep gold accent color (`var(--color-gold)`)
- Keep the slider styling
- Just ensure CSS variables are consistent

---

## Step 13: Update ArrayField Component

The `ArrayField` component (lines ~159-209 in TSX) can use shared styles:

### Current:
```tsx
<div className="pm-array">
  <label className="pm-array__label">{label}</label>
  <div className="pm-array__items">...</div>
  <button className="pm-array__add">+ Add</button>
</div>
```

### Update to:
```tsx
<div className="pm-array">
  <label className="admin-label">{label}</label>
  <div className="pm-array__items">
    <div className="pm-array__item">
      <input className="admin-input" ... />
      <button className="admin-btn admin-btn--ghost admin-btn--icon admin-btn--sm">×</button>
    </div>
  </div>
  <button className="admin-btn admin-btn--secondary admin-btn--sm">+ Add</button>
</div>
```

---

## Step 14: Update Avatar Image Manager for Desktop

The `AvatarImageManager` component (lines ~420-621 in TSX, lines ~792-980 in CSS) manages AI-generated character portraits. Update it for better desktop experience.

### Current Structure:
```tsx
<div className="pm-avatar">
  {/* Description textarea */}
  <div className="pm-avatar__description">
    <label className="pm-avatar__desc-label">
      Image Description
      <span className="pm-avatar__desc-hint">Used for AI image generation</span>
    </label>
    <textarea className="pm-avatar__desc-input" ... />
    <button className="pm-avatar__desc-save">Save Description</button>
  </div>

  {/* Emotion image grid */}
  <div className="pm-avatar__grid">
    {images.map(({ emotion, url }) => (
      <div className="pm-avatar__card">
        <div className="pm-avatar__image-wrap">
          {url ? <img /> : <div className="pm-avatar__placeholder">?</div>}
          {regenerating && <div className="pm-avatar__regenerating">...</div>}
        </div>
        <div className="pm-avatar__card-footer">
          <span className="pm-avatar__emotion">{emotion}</span>
          <button className="pm-avatar__refresh">🔄</button>
        </div>
      </div>
    ))}
  </div>

  {/* Generate missing button */}
  <button className="pm-avatar__generate-missing">
    Generate {missingCount} Missing Images
  </button>
</div>
```

### Updates for Desktop:

#### 14.1 Update Description Section
```tsx
<div className="pm-avatar__description">
  <div className="admin-form-group">
    <label className="admin-label" htmlFor="avatar-desc">
      Image Description
      <span className="admin-help-text" style={{display: 'block', marginTop: '2px'}}>
        Used for AI image generation
      </span>
    </label>
    <textarea
      id="avatar-desc"
      className="admin-input admin-textarea"
      value={avatarDescription}
      onChange={(e) => onDescriptionChange(e.target.value)}
      placeholder="Describe this character's appearance for image generation..."
      rows={3}
    />
  </div>
  <button
    type="button"
    className="admin-btn admin-btn--secondary"
    onClick={handleSaveDescription}
    disabled={savingDescription}
  >
    {savingDescription ? <><span className="admin-spinner" /> Saving...</> : 'Save Description'}
  </button>
</div>
```

#### 14.2 Update Grid for Desktop
Add responsive grid that shows more columns on desktop:

**CSS to add/update:**
```css
.pm-avatar__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr); /* Mobile: 2 columns */
  gap: var(--space-3);
}

/* Desktop: 4 columns */
@media (min-width: 768px) {
  .pm-avatar__grid {
    grid-template-columns: repeat(4, 1fr);
    gap: var(--space-4);
  }
}

/* Large desktop: 5 columns */
@media (min-width: 1200px) {
  .pm-avatar__grid {
    grid-template-columns: repeat(5, 1fr);
  }
}
```

#### 14.3 Update Image Cards
```tsx
<div className="pm-avatar__card admin-card admin-card--interactive">
  <div className="pm-avatar__image-wrap">
    {url ? (
      <img src={url} alt={`${personalityName} - ${emotion}`} className="pm-avatar__image" />
    ) : (
      <div className="pm-avatar__placeholder">
        <span>?</span>
      </div>
    )}
    {regenerating === emotion && (
      <div className="pm-avatar__regenerating">
        <div className="admin-loading__spinner admin-loading__spinner--sm" />
      </div>
    )}
  </div>
  <div className="pm-avatar__card-footer">
    <span className="pm-avatar__emotion">{emotion}</span>
    <button
      type="button"
      className="admin-btn admin-btn--ghost admin-btn--icon admin-btn--sm"
      onClick={() => handleRegenerate(emotion)}
      disabled={regenerating !== null}
      title={`Regenerate ${emotion}`}
    >
      <svg>...</svg>
    </button>
  </div>
</div>
```

#### 14.4 Update Generate Missing Button
```tsx
{missingCount > 0 && (
  <button
    type="button"
    className="admin-btn admin-btn--primary"
    onClick={handleGenerateMissing}
    disabled={regenerating !== null}
    style={{marginTop: 'var(--space-4)', width: '100%'}}
  >
    {regenerating === 'all' ? (
      <><span className="admin-spinner" /> Generating...</>
    ) : (
      <>✨ Generate {missingCount} Missing Image{missingCount > 1 ? 's' : ''}</>
    )}
  </button>
)}
```

#### 14.5 CSS to Keep (component-specific):
- `.pm-avatar` - Container layout
- `.pm-avatar__image-wrap` - Image aspect ratio container
- `.pm-avatar__image` - Image styling
- `.pm-avatar__placeholder` - Missing image placeholder
- `.pm-avatar__regenerating` - Overlay during regeneration
- `.pm-avatar__card-footer` - Footer layout
- `.pm-avatar__emotion` - Emotion label

#### 14.6 CSS to Remove:
- `.pm-avatar__loading` - Use `admin-loading`
- `.pm-avatar__spinner` - Use `admin-loading__spinner`
- `.pm-avatar__desc-label` - Use `admin-label`
- `.pm-avatar__desc-hint` - Use `admin-help-text`
- `.pm-avatar__desc-input` - Use `admin-input admin-textarea`
- `.pm-avatar__desc-save` - Use `admin-btn`
- `.pm-avatar__refresh` - Use `admin-btn--ghost admin-btn--icon`
- `.pm-avatar__generate-missing` - Use `admin-btn--primary`

---

## Step 15: Clean Up CSS File

After making all TSX changes, remove these sections from `PersonalityManager.css`:

1. ~~Alert Toast~~ (lines 13-83) - Use AdminShared
2. ~~Loading State~~ (lines 89-110) - Use AdminShared
3. ~~Modal styles~~ - Use AdminShared
4. ~~Empty state~~ - Use AdminShared
5. ~~Button styles~~ (`pm-actions__btn` variants) - Use AdminShared
6. ~~Form field styles~~ (`pm-field`, `pm-field__input`) - Use AdminShared
7. ~~Help text~~ - Use AdminShared
8. ~~Avatar buttons~~ (`pm-avatar__desc-save`, `pm-avatar__refresh`, `pm-avatar__generate-missing`) - Use AdminShared
9. ~~Avatar form elements~~ (`pm-avatar__desc-label`, `pm-avatar__desc-hint`, `pm-avatar__desc-input`) - Use AdminShared
10. ~~Avatar spinners~~ (`pm-avatar__loading`, `pm-avatar__spinner`) - Use AdminShared

**Keep these (component-specific):**
- `.pm-container` - Layout specific
- `.pm-selector-trigger` - Custom selector UI
- `.pm-section` - Collapsible sections
- `.pm-trait` - Slider component
- `.pm-array` - Array field layout (keep layout, remove button styles)
- `.pm-avatar` - Avatar container
- `.pm-avatar__description` - Description section layout
- `.pm-avatar__grid` - Image grid (update for responsive)
- `.pm-avatar__card` - Card container (can combine with `admin-card`)
- `.pm-avatar__image-wrap` - Image aspect ratio
- `.pm-avatar__image` - Image styling
- `.pm-avatar__placeholder` - Missing image placeholder
- `.pm-avatar__regenerating` - Overlay during regeneration
- `.pm-avatar__card-footer` - Footer layout
- `.pm-avatar__emotion` - Emotion label
- `.pm-sheet` - Bottom sheet (mobile)
- `.pm-editor` - Editor layout
- `.pm-actions` - Actions bar layout (not button styles)
- `.pm-fab` - Floating action button

---

## Step 16: Test Checklist

After refactoring, verify:

### Core Functionality
- [ ] Alert toasts appear correctly (success, error, info)
- [ ] Loading spinner displays properly
- [ ] Modals open/close and have correct styling
- [ ] Form inputs have focus states
- [ ] Buttons have hover/active states
- [ ] Empty state displays correctly
- [ ] Collapsible sections work
- [ ] Trait sliders function correctly
- [ ] Array fields (verbal/physical tics) work
- [ ] Character selector sheet works
- [ ] Save/Delete/Cancel actions work

### Avatar Image Manager
- [ ] Avatar description textarea displays correctly
- [ ] Save Description button works
- [ ] Image grid shows correct number of columns (2 mobile, 4 tablet, 5 desktop)
- [ ] Image cards display properly with hover states
- [ ] Placeholder shows for missing images
- [ ] Regenerate button on individual images works
- [ ] Loading spinner shows during regeneration
- [ ] "Generate Missing Images" button appears when images are missing
- [ ] All emotion images load correctly

### Responsive Behavior
- [ ] No visual regressions on mobile (< 768px)
- [ ] No visual regressions on tablet (768px - 1024px)
- [ ] No visual regressions on desktop (> 1024px)
- [ ] Avatar grid adapts to screen width

---

## Summary of Changes

| Component | Before | After |
|-----------|--------|-------|
| Alert | `pm-alert` | `admin-alert` |
| Loading | `pm-loading` | `admin-loading` |
| Modal | `pm-modal` | `admin-modal` |
| Buttons | `pm-actions__btn--*` | `admin-btn--*` |
| Inputs | `pm-field__input` | `admin-input` |
| Labels | `pm-field__label` | `admin-label` |
| Empty | `pm-empty` | `admin-empty` |
| Help text | `pm-help-text` | `admin-help-text` |
| Avatar desc label | `pm-avatar__desc-label` | `admin-label` |
| Avatar desc input | `pm-avatar__desc-input` | `admin-input admin-textarea` |
| Avatar desc hint | `pm-avatar__desc-hint` | `admin-help-text` |
| Avatar save btn | `pm-avatar__desc-save` | `admin-btn--secondary` |
| Avatar refresh btn | `pm-avatar__refresh` | `admin-btn--ghost admin-btn--icon` |
| Avatar generate btn | `pm-avatar__generate-missing` | `admin-btn--primary` |
| Avatar spinner | `pm-avatar__spinner` | `admin-loading__spinner` |

**Estimated CSS reduction:** ~250-350 lines removed from PersonalityManager.css
