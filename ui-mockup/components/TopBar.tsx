import React, { useState, useRef, useEffect } from 'react';
import LogoIcon from './icons/LogoIcon';
import UserIcon from './icons/UserIcon';
import ChevronDownIcon from './icons/ChevronDownIcon';
import FolderIcon from './icons/FolderIcon';
import PlusIcon from './icons/PlusIcon';

const TopBar: React.FC = () => {
  const [isProjectMenuOpen, setIsProjectMenuOpen] = useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const projectMenuRef = useRef<HTMLDivElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const handleClickOutside = (event: MouseEvent) => {
    if (projectMenuRef.current && !projectMenuRef.current.contains(event.target as Node)) {
      setIsProjectMenuOpen(false);
    }
    if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
      setIsUserMenuOpen(false);
    }
  };

  useEffect(() => {
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);
  
  const recentProjects = [
    { id: 1, name: 'Älmhults skola' },
    { id: 2, name: 'Kvarteret Furan' },
    { id: 3, name: 'Stadsbiblioteket' },
  ];

  return (
    <header className="bg-brand-dark text-white shadow-md flex-shrink-0 relative z-30">
      <div className="flex items-center justify-between h-14 px-4 sm:px-6 lg:px-8">
        {/* Left: Logo */}
        <div className="flex-shrink-0">
          <a href="#" aria-label="Home">
            <LogoIcon />
          </a>
        </div>

        {/* Center: Project Dropdown */}
        <div className="absolute left-1/2 transform -translate-x-1/2" ref={projectMenuRef}>
          <button
            onClick={() => setIsProjectMenuOpen(!isProjectMenuOpen)}
            className="flex items-center space-x-2 text-gray-300 hover:bg-gray-700 hover:text-white px-3 py-2 rounded-md text-sm font-medium transition-colors"
            aria-haspopup="true"
            aria-expanded={isProjectMenuOpen}
          >
            <span>Älmhults skola</span>
            <ChevronDownIcon className="h-4 w-4 text-gray-400" />
          </button>
          {isProjectMenuOpen && (
            <div
              className="origin-top absolute left-1/2 -translate-x-1/2 mt-2 w-64 rounded-md shadow-lg bg-gray-800 ring-1 ring-black ring-opacity-20 focus:outline-none animate-fade-in-down"
              role="menu"
              aria-orientation="vertical"
            >
              <div className="py-1" role="none">
                <div className="px-4 py-2 text-xs font-semibold text-gray-400 uppercase">Senaste projekt</div>
                {recentProjects.map((project) => (
                  <a
                    key={project.id}
                    href="#"
                    onClick={(e) => { e.preventDefault(); setIsProjectMenuOpen(false); }}
                    className={`block px-4 py-2 text-sm ${project.name === 'Älmhults skola' ? 'bg-gray-700 text-white' : 'text-gray-300 hover:bg-gray-700 hover:text-white'}`}
                    role="menuitem"
                  >
                    {project.name}
                  </a>
                ))}
                <div className="border-t border-gray-700 my-1"></div>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsProjectMenuOpen(false); }}
                  className="flex items-center text-gray-300 hover:bg-gray-700 hover:text-white px-4 py-2 text-sm"
                  role="menuitem"
                >
                  <FolderIcon />
                  Hantera alla projekt...
                </a>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsProjectMenuOpen(false); }}
                  className="flex items-center text-gray-300 hover:bg-gray-700 hover:text-white px-4 py-2 text-sm"
                  role="menuitem"
                >
                  <PlusIcon />
                  Skapa nytt projekt
                </a>
              </div>
            </div>
          )}
        </div>

        {/* Right: User Dropdown */}
        <div className="relative" ref={userMenuRef}>
          <button
            onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
            className="p-1.5 rounded-full text-gray-300 hover:bg-gray-700 hover:text-white focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-800 focus:ring-white"
            aria-haspopup="true"
            aria-expanded={isUserMenuOpen}
          >
            <span className="sr-only">Öppna användarmeny</span>
            <UserIcon />
          </button>
          {isUserMenuOpen && (
             <div
              className="origin-top-right absolute right-0 mt-2 w-48 rounded-md shadow-lg bg-gray-800 ring-1 ring-black ring-opacity-20 focus:outline-none animate-fade-in-down"
              role="menu"
              aria-orientation="vertical"
            >
              <div className="py-1" role="none">
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsUserMenuOpen(false); }}
                  className="text-gray-300 hover:bg-gray-700 hover:text-white block px-4 py-2 text-sm"
                  role="menuitem"
                >
                  Inställningar
                </a>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsUserMenuOpen(false); }}
                  className="text-gray-300 hover:bg-gray-700 hover:text-white block px-4 py-2 text-sm"
                  role="menuitem"
                >
                  Hjälp
                </a>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsUserMenuOpen(false); }}
                  className="text-gray-300 hover:bg-gray-700 hover:text-white block px-4 py-2 text-sm"
                  role="menuitem"
                >
                  Om verktyget
                </a>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsUserMenuOpen(false); }}
                  className="text-gray-300 hover:bg-gray-700 hover:text-white block px-4 py-2 text-sm"
                  role="menuitem"
                >
                  Utmaningar
                </a>
                <div className="border-t border-gray-700 my-1"></div>
                <a
                  href="#"
                  onClick={(e) => { e.preventDefault(); setIsUserMenuOpen(false); }}
                  className="text-gray-300 hover:bg-gray-700 hover:text-white block px-4 py-2 text-sm"
                  role="menuitem"
                >
                  Logga ut
                </a>
              </div>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

export default TopBar;