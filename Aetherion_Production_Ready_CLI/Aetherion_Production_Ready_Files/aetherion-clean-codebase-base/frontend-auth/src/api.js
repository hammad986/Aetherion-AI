/**
 * Frontend Auth API Client
 * Connects directly to hardened backend endpoints (/api/auth/*)
 */

const BASE_URL = import.meta.env.VITE_API_URL || '';

async function request(endpoint, options = {}) {
  const url = `${BASE_URL}${endpoint}`;
  const config = {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'include',
    ...options,
  };

  if (config.body && typeof config.body === 'object') {
    config.body = JSON.stringify(config.body);
  }

  const res = await fetch(url, config);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    let errorMsg = '';
    if (typeof data.error === 'object' && data.error !== null) {
      errorMsg = data.error.message || data.error.detail || data.error.code || JSON.stringify(data.error);
    } else if (typeof data.error === 'string' && data.error.trim()) {
      errorMsg = data.error.trim();
    } else if (typeof data.message === 'string' && data.message.trim()) {
      errorMsg = data.message.trim();
    } else if (typeof data.detail === 'string' && data.detail.trim()) {
      errorMsg = data.detail.trim();
    } else {
      errorMsg = `HTTP ${res.status}: ${res.statusText}`;
    }
    throw new Error(errorMsg);
  }

  return data;
}

export const api = {
  // Login / Signup / Me
  login: (email, password) => request('/api/auth/login', { method: 'POST', body: { email, password } }),
  signup: (email, password, name) => request('/api/auth/signup', { method: 'POST', body: { email, password, name } }),
  me: (token) => request('/api/auth/me', { headers: token ? { Authorization: `Bearer ${token}` } : {} }),
  refresh: () => request('/api/auth/refresh', { method: 'POST' }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  // Device Auth Flow
  confirmDeviceAuth: (userCode, email, password) =>
    request('/api/auth/device/confirm', {
      method: 'POST',
      body: { user_code: userCode, email, password },
    }),
  denyDeviceAuth: (userCode) =>
    request('/api/auth/device/deny', {
      method: 'POST',
      body: { user_code: userCode },
    }),

  // Account Recovery
  forgotPassword: (email) =>
    request('/api/auth/forgot-password', {
      method: 'POST',
      body: { email },
    }),
  resetPassword: (token, password) =>
    request('/api/auth/reset-password', {
      method: 'POST',
      body: { token, password },
    }),
  validateResetToken: (token) =>
    request('/api/auth/validate-reset-token', {
      method: 'POST',
      body: { token },
    }),

  // Health probe
  getHealth: () => request('/api/health'),
};
