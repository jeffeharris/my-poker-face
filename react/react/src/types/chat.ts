export interface ChatMessage {
  id: string;
  sender: string;
  message: string;
  timestamp: string;
  type: 'player' | 'ai' | 'table' | 'system';
}

/**
 * Backend message format (before transformation to ChatMessage).
 * Uses the same message_type values as ChatMessage.type.
 */
export interface BackendChatMessage {
  id?: string;
  sender: string;
  content: string;
  timestamp: string;
  message_type: ChatMessage['type'];
}