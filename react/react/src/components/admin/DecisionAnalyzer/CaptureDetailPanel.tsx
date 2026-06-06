import type {
  ConversationMessage,
  DebugMode,
  DecisionAnalysis,
  InterrogationMessage,
  PromptCapture,
  ProviderInfo,
  ReplayResponse,
} from './types';
import type { CaptureContext } from './utils';
import { formatCardsCanonical, formatPotOdds, getActionColor } from './utils';
import { DecisionAnalysisSection } from './DecisionAnalysisSection';
import { PsychologySection } from './PsychologySection';
import { CaptureView } from './CaptureView';
import { ReplayEditor } from './ReplayEditor';
import { InterrogationChat } from './InterrogationChat';
import { PipelineTracePanel } from './PipelineTracePanel';

interface CaptureDetailPanelProps {
  // 'desktop' renders the detail-header (player + phase); 'mobile' omits it
  // because the mobile layout shows that info in its top detail bar instead.
  variant: 'mobile' | 'desktop';
  capture: PromptCapture | null;
  analysis: DecisionAnalysis | null;
  ctx: CaptureContext;
  mode: DebugMode;
  onModeChange: (mode: DebugMode) => void;
  onSelectCapture: (captureId: number) => void;

  // Replay editor state (forwarded to <ReplayEditor>)
  modifiedSystemPrompt: string;
  onSystemPromptChange: (value: string) => void;
  modifiedUserMessage: string;
  onUserMessageChange: (value: string) => void;
  modifiedConversationHistory: ConversationMessage[];
  onUpdateHistoryMessage: (index: number, field: 'role' | 'content', value: string) => void;
  onRemoveHistoryMessage: (index: number) => void;
  onAddHistoryMessage: () => void;
  useHistory: boolean;
  onUseHistoryChange: (value: boolean) => void;
  replayProvider: string;
  onReplayProviderChange: (provider: string) => void;
  replayModel: string;
  onReplayModelChange: (model: string) => void;
  replayReasoningEffort: string;
  onReplayReasoningEffortChange: (effort: string) => void;
  onReplay: () => void;
  replaying: boolean;
  replayResult: ReplayResponse | null;

  // Shared provider/model config
  providers: ProviderInfo[];
  getModelsForProvider: (provider: string) => string[];
  reasoningLevels: string[];

  // Interrogation state (forwarded to <InterrogationChat>)
  interrogationMessages: InterrogationMessage[];
  onInterrogationMessagesUpdate: (messages: InterrogationMessage[]) => void;
  interrogationSessionId: string | null;
  onInterrogationSessionIdUpdate: (sessionId: string | null) => void;
  interrogateProvider: string;
  onInterrogateProviderChange: (provider: string) => void;
  interrogateModel: string;
  onInterrogateModelChange: (model: string) => void;
  interrogateReasoningEffort: string;
  onInterrogateReasoningEffortChange: (effort: string) => void;
}

// Render the parsed prompt-config (TieredBot decisions only).
function renderPromptConfig(capture: PromptCapture) {
  if (!capture.prompt_config_json) return null;

  let configDisplay: string;
  try {
    const parsed = JSON.parse(capture.prompt_config_json);
    configDisplay = JSON.stringify(parsed, null, 2);
  } catch {
    configDisplay = capture.prompt_config_json;
  }

  return (
    <details className="prompt-config-section">
      <summary>Prompt Config</summary>
      <pre>{configDisplay}</pre>
    </details>
  );
}

// The capture detail panel — shared verbatim between the desktop side-by-side
// layout and the mobile full-width detail view. The only platform difference
// is the desktop-only detail-header (gated by `variant`).
export function CaptureDetailPanel({
  variant,
  capture,
  analysis,
  ctx,
  mode,
  onModeChange,
  onSelectCapture,
  modifiedSystemPrompt,
  onSystemPromptChange,
  modifiedUserMessage,
  onUserMessageChange,
  modifiedConversationHistory,
  onUpdateHistoryMessage,
  onRemoveHistoryMessage,
  onAddHistoryMessage,
  useHistory,
  onUseHistoryChange,
  replayProvider,
  onReplayProviderChange,
  replayModel,
  onReplayModelChange,
  replayReasoningEffort,
  onReplayReasoningEffortChange,
  onReplay,
  replaying,
  replayResult,
  providers,
  getModelsForProvider,
  reasoningLevels,
  interrogationMessages,
  onInterrogationMessagesUpdate,
  interrogationSessionId,
  onInterrogationSessionIdUpdate,
  interrogateProvider,
  onInterrogateProviderChange,
  interrogateModel,
  onInterrogateModelChange,
  interrogateReasoningEffort,
  onInterrogateReasoningEffortChange,
}: CaptureDetailPanelProps) {
  const wrapperClass =
    variant === 'mobile' ? 'capture-detail capture-detail--mobile-fullwidth' : 'capture-detail';

  if (!capture) {
    return (
      <div className={wrapperClass}>
        <div className="no-selection">Select a capture from the list to view details</div>
      </div>
    );
  }

  // A decision that skipped the LLM (solver / TieredBot) is served as a stub
  // capture with empty prompt/response fields. For those we hide the prompt,
  // replay, and interrogate UI (none of it applies) and show a short note —
  // the meaningful data is the decision-analysis / pipeline panels above.
  const hasLlmCall = Boolean(capture.system_prompt || capture.user_message || capture.ai_response);

  return (
    <div className={wrapperClass}>
      {variant === 'desktop' && (
        <div className="detail-header">
          <h3>
            {capture.player_name} - {ctx.phase || '-'}
          </h3>
          <span className={`detail-action ${getActionColor(ctx.action_taken)}`}>
            {ctx.action_taken?.toUpperCase()}
            {ctx.raise_amount ? ` $${ctx.raise_amount}` : ''}
          </span>
        </div>
      )}

      <div className="detail-context">
        <div className="context-item">
          <label>Hand:</label>
          <span>{formatCardsCanonical(ctx.player_hand) || '-'}</span>
        </div>
        <div className="context-item">
          <label>Board:</label>
          <span>{formatCardsCanonical(ctx.community_cards) || '-'}</span>
        </div>
        <div className="context-item">
          <label>Pot:</label>
          <span>{ctx.pot_total != null ? `$${ctx.pot_total}` : '-'}</span>
        </div>
        <div className="context-item">
          <label>Cost to Call:</label>
          <span>{ctx.cost_to_call != null ? `$${ctx.cost_to_call}` : '-'}</span>
        </div>
        <div className="context-item highlight">
          <label>Pot Odds:</label>
          <span>{formatPotOdds(ctx.pot_odds)}</span>
        </div>
        <div className="context-item">
          <label>Stack:</label>
          <span>{ctx.player_stack != null ? `$${ctx.player_stack}` : '-'}</span>
        </div>
      </div>

      {/* Error/Correction Info */}
      {(capture.error_type || capture.parent_id) && (
        <div className="error-info-panel">
          {capture.error_type && (
            <div className="error-info-item">
              <label>Error Type:</label>
              <span className="error-type-value">{capture.error_type.replace(/_/g, ' ')}</span>
            </div>
          )}
          {capture.error_description && (
            <div className="error-info-item error-info-item--full">
              <label>Error:</label>
              <span>{capture.error_description}</span>
            </div>
          )}
          {capture.parent_id && (
            <div className="error-info-item">
              <label>Parent Capture:</label>
              <button
                type="button"
                className="link-button"
                onClick={() => onSelectCapture(capture.parent_id!)}
              >
                #{capture.parent_id}
              </button>
            </div>
          )}
          {capture.correction_attempt != null && capture.correction_attempt > 0 && (
            <div className="error-info-item">
              <label>Correction Attempt:</label>
              <span>#{capture.correction_attempt}</span>
            </div>
          )}
        </div>
      )}

      {/* Decision Analysis — equity/EV always, plus pipeline panel for TieredBot */}
      {analysis && <DecisionAnalysisSection analysis={analysis} />}

      {/* TieredBot pipeline trace — additive to the equity view above */}
      {analysis && analysis.intervention_trace && analysis.intervention_trace.length > 0 && (
        <PipelineTracePanel
          trace={analysis.intervention_trace}
          snapshot={analysis.strategy_pipeline_snapshot ?? null}
          actionTaken={analysis.action_taken}
        />
      )}

      {/* Psychology */}
      {analysis && <PsychologySection analysis={analysis} />}

      {/* Prompt Config */}
      {renderPromptConfig(capture)}

      {!hasLlmCall && <div className="no-llm-call">No LLM call made for this decision.</div>}

      {hasLlmCall && (
        <>
          <div className="detail-tabs">
            <button
              className={mode === 'view' ? 'active' : ''}
              onClick={() => onModeChange('view')}
            >
              View
            </button>
            <button
              className={mode === 'replay' ? 'active' : ''}
              onClick={() => onModeChange('replay')}
            >
              Edit & Replay
            </button>
            <button
              className={mode === 'interrogate' ? 'active' : ''}
              onClick={() => {
                onModeChange('interrogate');
                // Initialize interrogation with original response as context
                if (interrogationMessages.length === 0) {
                  onInterrogationMessagesUpdate([
                    {
                      id: 'original-decision',
                      role: 'context',
                      content: capture.ai_response,
                      timestamp: capture.created_at,
                    },
                  ]);
                }
              }}
            >
              Interrogate
            </button>
          </div>

          {/* Token & Latency Info */}
          {(capture.input_tokens || capture.latency_ms) && (
            <div className="token-info">
              <div className="token-info-row">
                {(capture.provider || capture.model) && (
                  <span>
                    {capture.provider && <strong>{capture.provider}</strong>}
                    {capture.provider && capture.model && ' / '}
                    {capture.model}
                    {capture.reasoning_effort && ` (${capture.reasoning_effort})`}
                  </span>
                )}
                {capture.latency_ms && (
                  <span>Latency: {capture.latency_ms.toLocaleString()}ms</span>
                )}
                {capture.estimated_cost != null && (
                  <span className="cost">Cost: ${capture.estimated_cost.toFixed(4)}</span>
                )}
              </div>
              <div className="token-info-row">
                <span className="token-count cached">
                  Cached: {(capture.cached_tokens ?? 0).toLocaleString()}
                </span>
                <span className="token-count input">
                  Input:{' '}
                  {((capture.input_tokens ?? 0) - (capture.cached_tokens ?? 0)).toLocaleString()}
                </span>
                <span className="token-count total-in">
                  Total In: {(capture.input_tokens ?? 0).toLocaleString()}
                </span>
              </div>
              <div className="token-info-row">
                <span className="token-count reasoning">
                  Reasoning: {(capture.reasoning_tokens ?? 0).toLocaleString()}
                </span>
                <span className="token-count output">
                  Output: {(capture.output_tokens ?? 0).toLocaleString()}
                </span>
                <span className="token-count total-out">
                  Total Out:{' '}
                  {(
                    (capture.reasoning_tokens ?? 0) + (capture.output_tokens ?? 0)
                  ).toLocaleString()}
                </span>
              </div>
            </div>
          )}

          {mode === 'view' && <CaptureView capture={capture} />}

          {mode === 'replay' && (
            <ReplayEditor
              modifiedSystemPrompt={modifiedSystemPrompt}
              onSystemPromptChange={onSystemPromptChange}
              modifiedUserMessage={modifiedUserMessage}
              onUserMessageChange={onUserMessageChange}
              modifiedConversationHistory={modifiedConversationHistory}
              onUpdateHistoryMessage={onUpdateHistoryMessage}
              onRemoveHistoryMessage={onRemoveHistoryMessage}
              onAddHistoryMessage={onAddHistoryMessage}
              useHistory={useHistory}
              onUseHistoryChange={onUseHistoryChange}
              replayProvider={replayProvider}
              onReplayProviderChange={onReplayProviderChange}
              replayModel={replayModel}
              onReplayModelChange={onReplayModelChange}
              replayReasoningEffort={replayReasoningEffort}
              onReplayReasoningEffortChange={onReplayReasoningEffortChange}
              providers={providers}
              getModelsForProvider={getModelsForProvider}
              reasoningLevels={reasoningLevels}
              onReplay={onReplay}
              replaying={replaying}
              replayResult={replayResult}
            />
          )}

          {mode === 'interrogate' && (
            <InterrogationChat
              capture={capture}
              messages={interrogationMessages}
              onMessagesUpdate={onInterrogationMessagesUpdate}
              sessionId={interrogationSessionId}
              onSessionIdUpdate={onInterrogationSessionIdUpdate}
              provider={interrogateProvider}
              onProviderChange={onInterrogateProviderChange}
              model={interrogateModel}
              onModelChange={onInterrogateModelChange}
              reasoningEffort={interrogateReasoningEffort}
              onReasoningEffortChange={onInterrogateReasoningEffortChange}
              providers={providers}
              getModelsForProvider={getModelsForProvider}
              reasoningLevels={reasoningLevels}
            />
          )}
        </>
      )}
    </div>
  );
}
