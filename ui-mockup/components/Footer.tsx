import React from 'react';
import MailIcon from './icons/MailIcon';

const Footer: React.FC = () => {
  return (
    <footer className="bg-brand-dark text-white flex-shrink-0">
      <div className="flex items-center justify-between h-10 px-4 sm:px-6 lg:px-8">
        <a href="mailto:kontakt@klimatai.se" className="flex items-center text-xs text-gray-400 hover:text-white transition-colors">
          <MailIcon className="h-4 w-4 mr-2" />
          <span>Kontakt</span>
        </a>
        <p className="text-xs text-gray-400">
          &copy; {new Date().getFullYear()} Klimat AI. All rights reserved.
        </p>
      </div>
    </footer>
  );
};

export default Footer;