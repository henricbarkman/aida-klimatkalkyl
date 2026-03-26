import React, { useState, useRef, useEffect } from 'react';
import type { ChatMessage } from '../types';
import SendIcon from './icons/SendIcon';
import PaperclipIcon from './icons/PaperclipIcon';
import MicrophoneIcon from './icons/MicrophoneIcon';
import UndoIcon from './icons/UndoIcon';
import HistoryIcon from './icons/HistoryIcon';

const ChatPanel: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: 1, sender: 'ai', text: 'Hej! Jag är din AI-assistent. Hur kan jag hjälpa dig med projektet Älmhults skola idag?' },
    { id: 2, sender: 'user', text: 'Kan du ge mig en översikt över de nuvarande klimatoptimerade materialvalen?' },
    { id: 3, sender: 'ai', text: 'Absolut! Här är en sammanfattning av de rekommenderade materialen baserat på den senaste analysen. Du kan se detaljerna i resultatpanelen till höger.' },
  ]);
  const [inputValue, setInputValue] = useState('');
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);
  
  const handleSendMessage = () => {
    if (inputValue.trim()) {
      const newMessage: ChatMessage = {
        id: messages.length + 1,
        sender: 'user',
        text: inputValue.trim(),
      };
      setMessages([...messages, newMessage]);
      setInputValue('');
    }
  };

  return (
    <div className="bg-brand-lilac-bg rounded-xl shadow-md flex flex-col h-full">
      <div className="px-4 py-2 border-b border-gray-200 flex-shrink-0 bg-brand-lilac-header rounded-t-xl">
        <div className="flex justify-between items-center">
            <h2 className="text-base font-semibold text-brand-charcoal">Aida</h2>
            <div className="flex items-center space-x-2">
                <button
                    className="text-gray-500 hover:text-brand-charcoal p-2 rounded-full focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors"
                    aria-label="Ångra senaste"
                >
                    <UndoIcon />
                </button>
                <button
                    className="text-gray-500 hover:text-brand-charcoal p-2 rounded-full focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors"
                    aria-label="Visa historik"
                >
                    <HistoryIcon />
                </button>
            </div>
        </div>
      </div>
      <div className="flex-grow p-4 overflow-y-auto bg-brand-lilac-bg">
        <div className="space-y-4">
          {messages.map((message) => (
            <div key={message.id} className={`flex ${message.sender === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] px-4 py-2 rounded-2xl ${
                message.sender === 'user' 
                  ? 'bg-user-bubble-bg text-black rounded-br-none' 
                  : 'bg-white text-black rounded-bl-none'
              }`}>
                <p className="text-sm">{message.text}</p>
              </div>
            </div>
          ))}
           <div ref={chatEndRef} />
        </div>
      </div>
      <div className="px-4 py-3 border-t border-gray-200 flex-shrink-0 bg-brand-lilac-header rounded-b-xl">
        <div className="flex items-center space-x-2">
          <button
            className="text-gray-500 hover:text-brand-charcoal p-2 rounded-full focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors"
            aria-label="Attach file"
          >
            <PaperclipIcon />
          </button>
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSendMessage()}
            placeholder="Skriv ditt meddelande..."
            className="flex-grow w-full px-4 py-2 bg-white border border-gray-200 rounded-full focus:outline-none focus:ring-2 focus:ring-brand-charcoal"
          />
          <button
            className="text-gray-500 hover:text-brand-charcoal p-2 rounded-full focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors"
            aria-label="Spela in röstmeddelande"
          >
            <MicrophoneIcon />
          </button>
          <button
            onClick={handleSendMessage}
            className="bg-brand-charcoal text-white rounded-full p-3 hover:bg-black focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors"
            aria-label="Send message"
          >
            <SendIcon />
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatPanel;