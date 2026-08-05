import { ConfigProvider, App as AntApp } from 'antd';
import ruRU from 'antd/locale/ru_RU';
import dayjs from 'dayjs';
import 'dayjs/locale/ru';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import App from './App';
import { AuthProvider } from './auth/AuthContext';
import { ThemeProvider, useTheme } from './theme/ThemeContext';
import { antdTheme } from './theme/antdTheme';
import './index.css';

dayjs.locale('ru');

/** Тема живет выше ConfigProvider, чтобы смена цвета перестраивала компоненты. */
function Themed() {
  const { resolved } = useTheme();
  return (
    <ConfigProvider locale={ruRU} theme={antdTheme(resolved)}>
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <Themed />
    </ThemeProvider>
  </StrictMode>,
);
