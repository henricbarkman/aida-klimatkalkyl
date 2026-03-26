import React, { useState, useMemo } from 'react';
import DownloadIcon from './icons/DownloadIcon';

// --- DATA STRUCTURES & MOCK DATA ---

type OptionType = 'Baslinje' | 'Återbruk' | 'Nyproduktion';

interface MaterialOption {
  id: string;
  name: string;
  type: OptionType;
  source: string;
  co2ePerUnit: number; // in kg
  costPerUnit: number; // in SEK
}

interface ComponentData {
  id: string;
  name:string;
  quantity: number;
  unit: string;
  options: MaterialOption[];
}

const analysisData: ComponentData[] = [
  {
    id: 'inner-walls',
    name: 'Innerväggar',
    quantity: 1500,
    unit: 'm²',
    options: [
      { id: 'iw-1', name: 'Lättregelvägg med gips', type: 'Baslinje', source: 'Boverket Klimatdatabas', co2ePerUnit: 12.5, costPerUnit: 450 },
      { id: 'iw-2', name: 'Återbrukat tegel', type: 'Återbruk', source: 'Solareturen', co2ePerUnit: 3.2, costPerUnit: 600 },
      { id: 'iw-3', name: 'Massivträ (CLT)', type: 'Nyproduktion', source: 'EPD S-P-98765', co2ePerUnit: -5.0, costPerUnit: 750 },
    ],
  },
  {
    id: 'flooring',
    name: 'Golv',
    quantity: 800,
    unit: 'm²',
    options: [
      { id: 'fl-1', name: 'Parkett, Ek', type: 'Baslinje', source: 'EPD PAR-1234', co2ePerUnit: 8.0, costPerUnit: 550 },
      { id: 'fl-2', name: 'Återvunnet trägolv', type: 'Återbruk', source: 'Lokal leverantör', co2ePerUnit: 1.5, costPerUnit: 700 },
      { id: 'fl-3', name: 'Linoleum', type: 'Nyproduktion', source: 'EPD LINO-567', co2ePerUnit: 4.5, costPerUnit: 400 },
    ],
  },
  {
      id: 'dishwasher',
      name: 'Diskmaskin',
      quantity: 10,
      unit: 'st',
      options: [
        { id: 'dw-1', name: 'Standardmodell Klass D', type: 'Baslinje', source: 'Tillverkardata', co2ePerUnit: 350, costPerUnit: 6000 },
        { id: 'dw-2', name: 'Rekonditionerad Klass B', type: 'Återbruk', source: 'Inrego', co2ePerUnit: 120, costPerUnit: 4500 },
        { id: 'dw-3', name: 'Högpresterande Klass A', type: 'Nyproduktion', source: 'Tillverkardata', co2ePerUnit: 250, costPerUnit: 8500 },
      ]
  }
];

// --- HELPER COMPONENTS & FUNCTIONS ---

const formatCost = (value: number) => {
  return new Intl.NumberFormat('sv-SE', { style: 'currency', currency: 'SEK', minimumFractionDigits: 0 }).format(value);
};

const formatCO2 = (value: number) => {
  // Convert from kg to tons
  return `${(value / 1000).toFixed(2)} ton`;
};

const calculatePercentageChange = (current: number, baseline: number) => {
  if (baseline === 0) return 0;
  return ((current - baseline) / baseline) * 100;
};

const ComparisonStat: React.FC<{ value: number }> = ({ value }) => {
  const isPositive = value > 0;
  const isNegative = value < 0;
  const colorClass = isNegative ? 'text-brand-mint-700' : isPositive ? 'text-red-800' : 'text-gray-700';
  const arrow = isNegative ? '↓' : isPositive ? '↑' : '';
  
  return (
    <span className={`text-base font-semibold ${colorClass}`}>
      {arrow} {value.toFixed(1)}%
    </span>
  );
};

const SummaryCard: React.FC<{ title: string; totalCO2: number; totalCost: number; baselineCO2?: number; baselineCost?: number; }> = 
({ title, totalCO2, totalCost, baselineCO2, baselineCost }) => {
  const hasComparison = baselineCO2 !== undefined && baselineCost !== undefined;

  return (
    <div className="bg-gray-25 rounded-lg p-4 flex-1 border border-gray-200">
      <h3 className="text-sm font-semibold text-gray-600 uppercase tracking-wider">{title}</h3>
      <div className="mt-2">
        <div className="flex items-baseline space-x-2">
            <p className="text-xl font-bold text-brand-charcoal">{formatCO2(totalCO2)}</p>
            {hasComparison && (
                <span className="text-gray-500">(<ComparisonStat value={calculatePercentageChange(totalCO2, baselineCO2)} />)</span>
            )}
        </div>
        <p className="text-sm text-gray-500">Total klimatpåverkan</p>
      </div>
      <div className="mt-4">
        <div className="flex items-baseline space-x-2">
            <p className="text-xl font-bold text-brand-charcoal">{formatCost(totalCost)}</p>
             {hasComparison && (
                <span className="text-gray-500">(<ComparisonStat value={calculatePercentageChange(totalCost, baselineCost)} />)</span>
            )}
        </div>
        <p className="text-sm text-gray-500">Total kostnad</p>
      </div>
    </div>
  );
};


const getTypeClasses = (type: OptionType) => {
  switch (type) {
    case 'Baslinje':
      return 'bg-gray-100 text-gray-800';
    case 'Återbruk':
      return 'bg-brand-mint-100 text-brand-mint-700';
    case 'Nyproduktion':
      return 'bg-yellow-100 text-yellow-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
}

interface FormInputProps {
  label: string;
  name: string;
  defaultValue?: string;
  placeholder?: string;
  required?: boolean;
}

const FormInput: React.FC<FormInputProps> = ({ label, name, defaultValue, placeholder, required }) => (
  <div>
    <label htmlFor={name} className="block text-sm font-medium text-gray-700">
      {label} {required && <span className="text-red-800">*</span>}
    </label>
    <input
      type="text"
      name={name}
      id={name}
      className="mt-1 block w-full px-3 py-2 bg-white border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-brand-charcoal focus:border-brand-charcoal sm:text-sm"
      defaultValue={defaultValue}
      placeholder={placeholder}
    />
  </div>
);

// --- MAIN COMPONENT ---

const ResultsPanel: React.FC = () => {
  const [activeTab, setActiveTab] = useState('project-info');

  const tabs = [
    { id: 'project-info', label: 'Projektinfo' },
    { id: 'comparison', label: 'Analys' },
    { id: 'summary', label: 'Sammanställning' },
    { id: 'follow-up', label: 'Uppföljning' },
  ];
  
  // State for user selections, initialized with baseline options
  const [selectedOptions, setSelectedOptions] = useState<Record<string, string>>(() => {
    const initialSelections: Record<string, string> = {};
    analysisData.forEach(component => {
      const baselineOption = component.options.find(opt => opt.type === 'Baslinje');
      if (baselineOption) {
        initialSelections[component.id] = baselineOption.id;
      }
    });
    return initialSelections;
  });

  const handleSelectionChange = (componentId: string, optionId: string) => {
    setSelectedOptions(prev => ({
      ...prev,
      [componentId]: optionId,
    }));
  };
  
  const summaryCalculations = useMemo(() => {
    let baseline = { totalCO2: 0, totalCost: 0 };
    let climateBest = { totalCO2: 0, totalCost: 0 };
    let selected = { totalCO2: 0, totalCost: 0 };

    analysisData.forEach(component => {
      // Baseline calculation
      const baselineOption = component.options.find(o => o.type === 'Baslinje')!;
      baseline.totalCO2 += baselineOption.co2ePerUnit * component.quantity;
      baseline.totalCost += baselineOption.costPerUnit * component.quantity;

      // Climate-best calculation
      const bestOption = component.options.reduce((best, current) => current.co2ePerUnit < best.co2ePerUnit ? current : best);
      climateBest.totalCO2 += bestOption.co2ePerUnit * component.quantity;
      climateBest.totalCost += bestOption.costPerUnit * component.quantity;

      // Selected calculation
      const selectedOptionId = selectedOptions[component.id];
      const selectedOption = component.options.find(o => o.id === selectedOptionId)!;
      selected.totalCO2 += selectedOption.co2ePerUnit * component.quantity;
      selected.totalCost += selectedOption.costPerUnit * component.quantity;
    });

    return { baseline, climateBest, selected };
  }, [selectedOptions]);
  

  const renderContent = () => {
    switch (activeTab) {
      case 'project-info':
        return (
          <div className="animate-fade-in-down">
            <form className="space-y-8">
              {/* === Obligatoriska uppgifter === */}
              <div className="space-y-6">
                <h4 className="text-lg font-semibold text-brand-charcoal border-b border-gray-300 pb-3 mb-6">Obligatoriska uppgifter</h4>
                <div>
                  <fieldset>
                    <legend className="block text-sm font-medium text-gray-700 mb-2">
                      Övergripande kategori för renoveringen <span className="text-red-800">*</span>
                    </legend>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                       {['Ombyggnad stomrent', 'Installationer', 'Invändig renovering', 'Kök och badrum'].map(category => (
                        <div key={category} className="relative flex items-start">
                          <div className="flex h-5 items-center">
                            <input id={category} name="categories" type="checkbox" className="h-4 w-4 rounded border-gray-300 text-brand-charcoal focus:ring-brand-charcoal" />
                          </div>
                          <div className="ml-3 text-sm">
                            <label htmlFor={category} className="font-medium text-gray-700">{category}</label>
                          </div>
                        </div>
                      ))}
                    </div>
                  </fieldset>
                </div>
                 <div>
                  <label htmlFor="totalBTA" className="block text-sm font-medium text-gray-700">
                    Total BTA (m²) <span className="text-red-800">*</span>
                  </label>
                  <input
                    type="text"
                    name="totalBTA"
                    id="totalBTA"
                    className="mt-1 block w-full px-3 py-2 bg-white border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-brand-charcoal focus:border-brand-charcoal sm:text-sm"
                    placeholder="Ange total bruttoarea"
                  />
                </div>
                <div>
                  <label htmlFor="projectScope" className="block text-sm font-medium text-gray-700">
                    Beskrivning av behov (inkl skick) <span className="text-red-800">*</span>
                  </label>
                  <textarea
                    name="projectScope"
                    id="projectScope"
                    rows={4}
                    className="mt-1 block w-full px-3 py-2 bg-white border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-brand-charcoal focus:border-brand-charcoal sm:text-sm"
                    defaultValue="Renovering och modernisering av A-huset på Älmhults skola. Byggnaden är från 1978 och har ett stort behov av uppdaterade ytskikt, nya installationer (el och ventilation) samt modernisering av kök och badrum i personalutrymmen. Stommen bedöms vara i gott skick."
                  />
                </div>
              </div>

              {/* === Frivilliga uppgifter === */}
               <div className="space-y-6">
                <div className="border-b border-gray-300 pb-3 mb-6">
                  <h4 className="text-lg font-semibold text-brand-charcoal">Frivilliga uppgifter</h4>
                  <p className="text-sm text-gray-500 mt-1">AI-assistenten kan göra antaganden utifrån en övergripande beskrivning, men mer detaljerad information ger ett bättre resultat.</p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormInput name="buildingType" label="Byggnadstyp/verksamhet" defaultValue="Skola" />
                  <FormInput name="buildingYear" label="Byggnadsår (även ev tidigare renovering)" defaultValue="1978 (mindre renovering 2005)" />
                </div>
                 
                <div>
                    <label className="block text-sm font-medium text-gray-700">Geometri</label>
                    <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-6 p-4 bg-gray-25 rounded-md border border-gray-200">
                        <FormInput name="bta" label="BTA per plan" placeholder="t.ex. 800 m²" />
                        <FormInput name="floors" label="Antal våningar" placeholder="t.ex. 3" />
                        <FormInput name="windowPercentage" label="Fönsterandel" placeholder="t.ex. 20%" />
                    </div>
                </div>

                 <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <FormInput name="structure" label="Stomme och grund" placeholder="Trä/betong/stål, platta/källare" />
                  <FormInput name="exterior" label="Tak och fasad" placeholder="Typ av tak, fasadmaterial" />
                  <FormInput name="installations" label="Installationer" placeholder="Ventilation, värme, kyla, el" />
                  <FormInput name="interior" label="Invändiga delar" placeholder="Golv, väggar, innertak" />
                </div>
              </div>
              
              <div className="flex justify-end">
                <button
                  type="submit"
                  onClick={(e) => e.preventDefault()}
                  className="px-4 py-2 bg-brand-charcoal text-white font-medium text-sm rounded-lg hover:bg-black focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors"
                >
                  Spara ändringar
                </button>
              </div>
            </form>
          </div>
        );
      case 'comparison':
        return (
          <div className="animate-fade-in-down space-y-8">
            {/* Summary Section */}
            <div className="flex flex-col md:flex-row gap-4">
              <SummaryCard title="Baslinje" {...summaryCalculations.baseline} />
              <SummaryCard title="Klimatbästa alternativ" {...summaryCalculations.climateBest} baselineCO2={summaryCalculations.baseline.totalCO2} baselineCost={summaryCalculations.baseline.totalCost} />
              <SummaryCard title="Valda alternativ" {...summaryCalculations.selected} baselineCO2={summaryCalculations.baseline.totalCO2} baselineCost={summaryCalculations.baseline.totalCost} />
            </div>

            {/* Components Section */}
            <div className="space-y-6">
              {analysisData.map((component) => (
                <div key={component.id} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                  <div className="p-4 bg-gray-25 border-b border-gray-200">
                    <h3 className="font-semibold text-brand-charcoal">{component.name}</h3>
                    <p className="text-sm text-gray-500">{component.quantity} {component.unit}</p>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left table-fixed">
                      <thead className="text-left">
                        <tr className="border-b border-gray-200">
                          <th className="px-4 py-2 w-12"></th>
                          <th className="px-4 py-2 font-medium text-gray-500 w-32">Typ</th>
                          <th className="px-4 py-2 font-medium text-gray-500">Material</th>
                          <th className="px-4 py-2 font-medium text-gray-500 w-40">Källa</th>
                          <th className="px-4 py-2 font-medium text-gray-500 w-24 text-right">CO₂e</th>
                          <th className="px-4 py-2 font-medium text-gray-500 w-28 text-right">Kostnad</th>
                        </tr>
                      </thead>
                      <tbody>
                        {component.options.map((option) => (
                          <tr key={option.id} className="border-b border-gray-100 last:border-b-0 hover:bg-gray-50">
                            <td className="px-4 py-3">
                              <input
                                type="radio"
                                name={component.id}
                                value={option.id}
                                checked={selectedOptions[component.id] === option.id}
                                onChange={() => handleSelectionChange(component.id, option.id)}
                                className="h-4 w-4 accent-brand-charcoal border-gray-300 focus:ring-brand-charcoal"
                              />
                            </td>
                             <td className="px-4 py-3">
                              <span className={`px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${getTypeClasses(option.type)}`}>
                                {option.type}
                              </span>
                            </td>
                            <td className="px-4 py-3 font-medium text-gray-800">{option.name}</td>
                            <td className="px-4 py-3 text-gray-700">{option.source}</td>
                            <td className="px-4 py-3 text-gray-700 font-medium text-right">{formatCO2(option.co2ePerUnit * component.quantity)}</td>
                            <td className="px-4 py-3 text-gray-700 font-medium text-right">{formatCost(option.costPerUnit * component.quantity)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      case 'summary':
        return (
           <div className="animate-fade-in-down">
             <p className="text-sm text-gray-600 mb-6">Här genereras färdiga underlag anpassade för olika målgrupper baserat på den genomförda analysen. Välj en rapport nedan för att exportera.</p>
              {/* Report list content from previous step... */}
              <div className="space-y-4">
                <div className="p-4 border border-gray-200 rounded-lg flex justify-between items-center">
                    <div>
                        <h4 className="font-semibold text-brand-charcoal">Komplett analys och planering</h4>
                        <p className="text-sm text-gray-500">En fullständig rapport som täcker hela analysen och den framtagna planeringen. (Word)</p>
                    </div>
                    <button className="flex items-center px-4 py-2 bg-gray-100 text-brand-charcoal font-medium text-sm rounded-lg hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors">
                        <DownloadIcon />
                        Exportera
                    </button>
                </div>
                <div className="p-4 border border-gray-200 rounded-lg flex justify-between items-center">
                    <div>
                        <h4 className="font-semibold text-brand-charcoal">Beskrivning till byggteam</h4>
                        <p className="text-sm text-gray-500">En anpassad rapport för byggteamet med fokus på materialval och utförande. (Word)</p>
                    </div>
                    <button className="flex items-center px-4 py-2 bg-gray-100 text-brand-charcoal font-medium text-sm rounded-lg hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors">
                        <DownloadIcon />
                        Exportera
                    </button>
                </div>
                 <div className="p-4 border border-gray-200 rounded-lg flex justify-between items-center">
                    <div>
                        <h4 className="font-semibold text-brand-charcoal">Tabeller och metod</h4>
                        <p className="text-sm text-gray-500">Alla uträkningar, inklusive formler, exporteras för transparens och vidare analys. (Excel)</p>
                    </div>
                    <button className="flex items-center px-4 py-2 bg-gray-100 text-brand-charcoal font-medium text-sm rounded-lg hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-charcoal transition-colors">
                        <DownloadIcon />
                        Exportera
                    </button>
                </div>
             </div>
           </div>
        );
      case 'follow-up':
        return (
          <div className="animate-fade-in-down">
            <p className="text-sm text-gray-600">Här kommer data från uppföljningen av projektet att visas. Jämför beräknad klimatpåverkan med faktiskt utfall för att verifiera att målen har uppnåtts och dra lärdomar för framtida projekt.</p>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="flex-shrink-0">
        <div className="border-b border-gray-200">
          <nav className="-mb-px flex space-x-8" aria-label="Tabs">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`${
                  activeTab === tab.id
                    ? 'border-brand-charcoal text-brand-charcoal'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                } whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm transition-colors focus:outline-none`}
                aria-current={activeTab === tab.id ? 'page' : undefined}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
      </div>
      
      <div className="overflow-y-auto flex-grow p-6 bg-gray-25">
        {renderContent()}
      </div>
    </div>
  );
};

export default ResultsPanel;