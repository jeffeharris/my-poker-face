export interface ChatMessage {
  id: string;
  sender: string;
  message: string;
  timestamp: string;
  type: 'game' | 'player' | 'system';
}