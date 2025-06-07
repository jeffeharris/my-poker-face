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

### Refactoring Plan

1. **CustomGameConfig**: 
   - `.trait` → `.cgc-personality-trait`
   - `.trait-bar` → `.cgc-trait-bar`
   - `.trait-fill` → `.cgc-trait-fill`

2. **ElasticityDebugPanel/DebugPanel**:
   - `.trait` → `.edp-trait`
   - `.trait-bar` → `.edp-trait-bar`
   - `.trait-bar-container` → `.edp-trait-bar-container`
   - `.anchor-line` → `.edp-anchor-line`
   - `.elasticity-range` → `.edp-elasticity-range`

3. **Future Components**: Always use component-specific prefixes