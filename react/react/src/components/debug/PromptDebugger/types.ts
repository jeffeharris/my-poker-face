// T2-61: this module previously declared its own copies of types that
// also live in admin/DecisionAnalyzer/types.ts. The two definitions
// drifted (DecisionAnalyzer's version is the more complete one).
// Re-export from there so there is exactly one canonical definition.
export type {
  ConversationMessage,
  PromptCapture,
  CaptureStats,
  CaptureListResponse,
  ReplayResponse,
  CaptureFilters,
  DecisionAnalysis,
  InterventionOperation,
  InterventionTrace,
  StrategyPipelineSnapshot,
  DecisionAnalysisStats,
  DebugMode,
  InterrogationMessage,
  InterrogationResponse,
  ProviderInfo,
} from '../../admin/DecisionAnalyzer/types';
