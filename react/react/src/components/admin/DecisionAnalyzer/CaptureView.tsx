import type { PromptCapture } from './types';
import { buildRawRequest, downloadJson, safeJsonParse } from './utils';

interface CaptureViewProps {
  capture: PromptCapture;
}

// "View" mode: read-only prompts, response, download buttons, and raw response.
export function CaptureView({ capture }: CaptureViewProps) {
  return (
    <div className="detail-prompts">
      <div className="prompt-section">
        <h4>System Prompt</h4>
        <pre>{capture.system_prompt}</pre>
      </div>

      {/* Conversation History */}
      {capture.conversation_history && capture.conversation_history.length > 0 && (
        <div className="prompt-section conversation-history">
          <h4>Conversation History ({capture.conversation_history.length} messages)</h4>
          <div className="history-messages">
            {capture.conversation_history.map((msg, idx) => (
              <div key={idx} className={`history-message ${msg.role}`}>
                <span className="message-role">{msg.role}</span>
                <pre>{msg.content}</pre>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="prompt-section">
        <h4>User Message (Current Turn)</h4>
        <pre>{capture.user_message}</pre>
      </div>
      <div className="prompt-section">
        <h4>AI Response</h4>
        <pre>{capture.ai_response}</pre>
      </div>

      {/* Download buttons */}
      <div className="download-buttons">
        <button
          className="download-button"
          onClick={() => {
            const request = buildRawRequest(capture);
            const filename = `request_${capture.id}_${capture.player_name}_h${capture.hand_number || 0}.json`;
            downloadJson(request, filename);
          }}
        >
          Download Request
        </button>
        {capture.raw_api_response && (
          <button
            className="download-button"
            onClick={() => {
              const response = JSON.parse(capture.raw_api_response!);
              const filename = `response_${capture.id}_${capture.player_name}_h${capture.hand_number || 0}.json`;
              downloadJson(response, filename);
            }}
          >
            Download Response
          </button>
        )}
      </div>

      {/* Raw API Response - contains reasoning tokens, etc. */}
      {capture.raw_api_response && (
        <details className="prompt-section raw-response">
          <summary>
            <h4>Raw API Response (click to expand)</h4>
          </summary>
          <pre>
            {JSON.stringify(
              safeJsonParse<unknown>(capture.raw_api_response, capture.raw_api_response),
              null,
              2
            )}
          </pre>
        </details>
      )}
    </div>
  );
}
