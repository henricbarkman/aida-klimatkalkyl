import React from 'react';

interface ProgressTrackerProps {
  steps: string[];
  currentStep: number;
}

const ProgressTracker: React.FC<ProgressTrackerProps> = ({ steps, currentStep }) => {
  // Guard against division by zero if there's only one step
  const progressPercentage = steps.length > 1 ? ((currentStep - 1) / (steps.length - 1)) * 100 : 0;

  return (
    <div className="w-full">
      <div className="relative flex justify-between items-start">
        {/* Lines Container - positioned to align with centers of first/last step */}
        {/* The step label width is w-28 (7rem), so we inset the line by half of that (3.5rem = 14) on each side. */}
        <div className="absolute top-4 left-14 right-14 h-0.5">
            {/* Background Line */}
            <div className="absolute top-0 left-0 w-full h-full bg-gray-200" />
            {/* Progress Line */}
            <div
              className="absolute top-0 left-0 h-full bg-gray-800 transition-all duration-500"
              style={{ width: `${progressPercentage}%` }}
            />
        </div>
        
        {steps.map((step, index) => {
          const stepNumber = index + 1;
          const isActive = stepNumber === currentStep;
          const isCompleted = stepNumber < currentStep;

          let labelClasses = 'text-gray-500'; // Default for future steps
          if (isActive) {
            labelClasses = 'text-brand-charcoal font-bold';
          } else if (isCompleted) {
            labelClasses = 'text-gray-800'; // Darker for completed steps
          }

          return (
            <div key={index} className="z-10 flex flex-col items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-base font-bold transition-all duration-300
                  ${
                    isActive
                      ? 'bg-brand-charcoal text-white shadow-lg shadow-gray-400/50 scale-110'
                      : isCompleted
                      ? 'bg-gray-800 text-white'
                      : 'bg-white text-gray-400 border-2 border-gray-200'
                  }`}
              >
                {isCompleted ? (
                   <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                ) : stepNumber}
              </div>
              <p className={`mt-2 text-center text-xs sm:text-sm font-medium w-28 ${labelClasses}`}>
                {step}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ProgressTracker;