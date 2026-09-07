/**
 * Auth Helper Utilities
 * 
 * Functions to automate authentication flows in E2E tests
 */

import { Page, expect } from '@playwright/test';
import { generateTestUser } from '../fixtures/test-data';

const BASE_URL = 'http://localhost:8788';

export interface TestUser {
  nombre: string;
  apellido: string;
  email: string;
  password: string;
  organizacion?: string;
}

/**
 * Register a new user via the UI
 */
export async function registerUser(page: Page, user?: Partial<TestUser>): Promise<TestUser> {
  const userData = { ...generateTestUser(), ...user };
  
  await page.goto('/login.html');
  await page.waitForLoadState('networkidle');
  
  // Switch to register tab
  await page.click('[data-tab="register"]');
  await page.waitForTimeout(300);
  
  // Fill registration form
  await page.fill('#reg-nombre', userData.nombre);
  await page.fill('#reg-apellido', userData.apellido);
  await page.fill('#reg-email', userData.email);
  await page.fill('#reg-password', userData.password);
  await page.fill('#reg-password-confirm', userData.password);
  
  if (userData.organizacion) {
    await page.fill('#reg-organizacion', userData.organizacion);
  }
  
  // Accept terms
  await page.check('#accept-terms');
  
  // Submit
  await page.click('#register-btn');
  
  // Wait for redirect or success
  await page.waitForTimeout(1500);
  
  return userData;
}

/**
 * Login via the UI
 */
export async function loginUser(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login.html');
  await page.waitForLoadState('networkidle');
  
  // Ensure we're on login tab
  await page.click('[data-tab="login"]');
  await page.waitForTimeout(200);
  
  // Fill login form
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  
  // Submit
  await page.click('#login-btn');
  
  // Wait for navigation
  await page.waitForTimeout(2000);
}

/**
 * Register and login a user (combined for efficiency)
 */
export async function registerAndLogin(page: Page, user?: Partial<TestUser>): Promise<TestUser> {
  const userData = await registerUser(page, user);
  
  // If registration succeeded and redirected, we might already be logged in
  // If not, login explicitly
  const currentUrl = page.url();
  if (currentUrl.includes('login.html')) {
    await loginUser(page, userData.email, userData.password);
  }
  
  return userData;
}

/**
 * Logout via the UI
 */
export async function logoutUser(page: Page): Promise<void> {
  // Try to find logout button/link
  const logoutBtn = page.locator('text=Cerrar sesión').or(page.locator('text=Logout'));
  
  if (await logoutBtn.isVisible()) {
    await logoutBtn.click();
    await page.waitForTimeout(1000);
  }
}

/**
 * Register user via API (faster for tests that don't need to test registration UI)
 */
export async function registerUserAPI(user?: Partial<TestUser>): Promise<TestUser> {
  const userData = { ...generateTestUser(), ...user };
  
  const response = await fetch(`${BASE_URL}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(userData),
  });
  
  if (!response.ok) {
    throw new Error(`Registration failed: ${response.status}`);
  }
  
  return userData;
}

/**
 * Login user via API and return session cookie
 */
export async function loginUserAPI(email: string, password: string): Promise<string> {
  const response = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  
  if (!response.ok) {
    throw new Error(`Login failed: ${response.status}`);
  }
  
  const setCookie = response.headers.get('set-cookie');
  const sessionMatch = setCookie?.match(/session=([^;]+)/);
  return sessionMatch ? sessionMatch[1] : '';
}

/**
 * Check if user is authenticated by calling /api/auth/me
 */
export async function isAuthenticated(page: Page): Promise<boolean> {
  try {
    const response = await page.evaluate(async () => {
      const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
      return res.ok;
    });
    return response;
  } catch {
    return false;
  }
}

/**
 * Wait for navigation to complete after auth action
 */
export async function waitForAuthRedirect(page: Page, timeout = 5000): Promise<string> {
  await page.waitForURL(url => !url.pathname.includes('login.html'), { timeout });
  return page.url();
}
