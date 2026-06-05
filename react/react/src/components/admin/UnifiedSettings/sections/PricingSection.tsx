import { PricingManager } from '../../PricingManager';

/** Pricing settings — delegates entirely to the standalone PricingManager. */
export function PricingSection() {
  return <PricingManager embedded />;
}
