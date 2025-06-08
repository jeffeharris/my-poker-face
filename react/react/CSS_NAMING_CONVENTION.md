# CSS Naming Convention

## Component-Scoped Classes

Each component should prefix its classes with a unique identifier to avoid conflicts.

### Current Issues
- `CustomGameConfig` and `ElasticityDebugPanel` both use `.trait`, `.trait-bar`
- Generic class names cause style conflicts

### Proposed Convention

Use BEM-style naming with component prefixes:

```css
/* Component: CustomGameConfig */
.cgc-trait {}
.cgc-trait__bar {}
.cgc-trait__fill {}
.cgc-trait__label {}

/* Component: ElasticityDebugPanel */
.edp-trait {}
.edp-trait__header {}
.edp-trait__bar-container {}
.edp-trait__bar {}
.edp-trait__anchor-line {}
.edp-trait__elasticity-range {}

/* Component: DebugPanel */
.dbp-trait {}
.dbp-trait__header {}
.dbp-trait__visualization {}
```

### Benefits
1. No naming conflicts
2. Clear component ownership
3. Easier to find styles in DevTools
4. Self-documenting

### Completed Refactoring

1. **CustomGameConfig** (prefix: `cgc-`): 
   - `.trait` → `.cgc-personality-trait`
   - `.trait-bar` → `.cgc-trait-bar`
   - `.trait-fill` → `.cgc-trait-fill`

2. **ElasticityDebugPanel/DebugPanel** (prefix: `edp-`):
   - `.player-elasticity` → `.edp-player`
   - `.mood` → `.edp-mood`
   - `.mood-value` → `.edp-mood-value`
   - `.trait` → `.edp-trait`
   - `.trait-header` → `.edp-trait-header`
   - `.trait-name` → `.edp-trait-name`
   - `.trait-value` → `.edp-trait-value`
   - `.trait-bar` → `.edp-trait-bar`
   - `.trait-bar-container` → `.edp-trait-bar-container`
   - `.trait-bar-background` → `.edp-trait-bar-background`
   - `.anchor-line` → `.edp-anchor-line`
   - `.elasticity-range` → `.edp-elasticity-range`
   - `.trait-labels` → `.edp-trait-labels`
   - `.trait-details` → `.edp-trait-details`

3. **Future Components**: Always use component-specific prefixes

### Testing Approach

To verify the refactoring worked:
1. Check that new class names render with correct styles
2. Verify old class names no longer exist in the DOM
3. Confirm no style conflicts between components
4. Test both components are displayed correctly