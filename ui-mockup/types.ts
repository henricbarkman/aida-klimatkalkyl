
export interface ChatMessage {
  id: number;
  sender: 'user' | 'ai';
  text: string;
}
