import React, { useState } from 'react';
import ProgressTracker from './components/ProgressTracker';
import ChatPanel from './components/ChatPanel';
import ResultsPanel from './components/ResultsPanel';
import TopBar from './components/TopBar';
import Footer from './components/Footer';

const App: React.FC = () => {
  const [currentStep, setCurrentStep] = useState(3);
  const steps = [
    "Tidig planering",
    "Baslinje",
    "Återbruk",
    "Nyproduktion",
    "Ekonomi",
    "Sammanställning",
    "Uppföljning"
  ];

  return (
    <div className="h-screen bg-white font-sans text-brand-charcoal flex flex-col">
      <TopBar />
      <div className="px-4 sm:px-6 lg:px-12 pt-6 flex flex-col flex-grow min-h-0">
        <header className="mb-6 flex-shrink-0">
          <ProgressTracker steps={steps} currentStep={currentStep} />
        </header>
        <main className="flex flex-col lg:flex-row gap-12 flex-grow min-h-0">
          <div className="lg:w-[40%] lg:flex-shrink-0 flex flex-col min-h-0">
            <ChatPanel />
            <p className="text-xs text-gray-500 text-center mt-2 mb-4 px-4">
              Aida kan begå misstag. Kontrollera viktig information.
            </p>
          </div>
          <div className="lg:w-[60%] lg:flex-grow flex flex-col min-h-0">
            <ResultsPanel />
          </div>
        </main>
      </div>
      <Footer />
    </div>
  );
};

export default App;