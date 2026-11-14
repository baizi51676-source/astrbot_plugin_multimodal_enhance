// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { App } from './app';

const host = document.getElementById('root');
if (!host) throw new Error('#root element is missing');

createRoot(host).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
