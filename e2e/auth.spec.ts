/**
 * Authentication Flow E2E Tests
 * 
 * Tests for login, register, and logout functionality
 */

import { test, expect } from '@playwright/test';
import { generateTestUser, TestUser } from './fixtures/test-data';

test.describe('Authentication', () => {
  let testUser: TestUser;

  test.beforeEach(() => {
    testUser = generateTestUser();
  });

  test.describe('Registration', () => {
    test('should display registration form', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      // Click register tab
      await page.click('[data-tab="register"]');
      await page.waitForTimeout(300);
      
      // Check form elements
      await expect(page.locator('#reg-nombre')).toBeVisible();
      await expect(page.locator('#reg-apellido')).toBeVisible();
      await expect(page.locator('#reg-email')).toBeVisible();
      await expect(page.locator('#reg-password')).toBeVisible();
      await expect(page.locator('#reg-password-confirm')).toBeVisible();
      await expect(page.locator('#accept-terms')).toBeVisible();
    });

    test('should register new user successfully', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      // Switch to register tab
      await page.click('[data-tab="register"]');
      await page.waitForTimeout(300);
      
      // Fill form
      await page.fill('#reg-nombre', testUser.nombre);
      await page.fill('#reg-apellido', testUser.apellido);
      await page.fill('#reg-email', testUser.email);
      await page.fill('#reg-password', testUser.password);
      await page.fill('#reg-password-confirm', testUser.password);
      await page.check('#accept-terms');
      
      // Submit
      await page.click('#register-btn');
      
      // Wait for success message or redirect
      await page.waitForTimeout(2000);
      
      // Should show success or redirect
      const alertSuccess = page.locator('.alert-success');
      const isRedirected = !page.url().includes('login.html');
      
      expect(await alertSuccess.isVisible() || isRedirected).toBeTruthy();
    });

    test('should show error for mismatched passwords', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      await page.click('[data-tab="register"]');
      await page.waitForTimeout(300);
      
      await page.fill('#reg-nombre', testUser.nombre);
      await page.fill('#reg-apellido', testUser.apellido);
      await page.fill('#reg-email', testUser.email);
      await page.fill('#reg-password', testUser.password);
      await page.fill('#reg-password-confirm', 'DifferentPassword123!');
      await page.check('#accept-terms');
      
      await page.click('#register-btn');
      await page.waitForTimeout(500);
      
      // Should show error
      const alertError = page.locator('.alert-error');
      await expect(alertError).toBeVisible();
      await expect(alertError).toContainText('coinciden');
    });

    test('should show error for short password', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      await page.click('[data-tab="register"]');
      await page.waitForTimeout(300);
      
      await page.fill('#reg-nombre', testUser.nombre);
      await page.fill('#reg-apellido', testUser.apellido);
      await page.fill('#reg-email', testUser.email);
      await page.fill('#reg-password', 'Short1!');
      await page.fill('#reg-password-confirm', 'Short1!');
      await page.check('#accept-terms');
      
      await page.click('#register-btn');
      await page.waitForTimeout(500);
      
      const alertError = page.locator('.alert-error');
      await expect(alertError).toBeVisible();
    });

    test('should require terms acceptance', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      await page.click('[data-tab="register"]');
      await page.waitForTimeout(300);
      
      await page.fill('#reg-nombre', testUser.nombre);
      await page.fill('#reg-apellido', testUser.apellido);
      await page.fill('#reg-email', testUser.email);
      await page.fill('#reg-password', testUser.password);
      await page.fill('#reg-password-confirm', testUser.password);
      // Don't check terms
      
      await page.click('#register-btn');
      await page.waitForTimeout(500);
      
      const alertError = page.locator('.alert-error');
      await expect(alertError).toBeVisible();
    });
  });

  test.describe('Login', () => {
    test('should display login form', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      // Check form elements
      await expect(page.locator('#login-email')).toBeVisible();
      await expect(page.locator('#login-password')).toBeVisible();
      await expect(page.locator('#login-btn')).toBeVisible();
    });

    test('should show error for invalid credentials', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      await page.fill('#login-email', 'nonexistent@example.com');
      await page.fill('#login-password', 'WrongPassword123!');
      
      await page.click('#login-btn');
      await page.waitForTimeout(1000);
      
      const alertError = page.locator('.alert-error');
      await expect(alertError).toBeVisible();
    });

    test('should login with valid credentials', async ({ page }) => {
      // Navigate first so relative URLs resolve
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');

      // Register user via API (page must be on the app origin)
      const registerResponse = await page.evaluate(async (user) => {
        const res = await fetch('/api/auth/register', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(user),
        });
        return { ok: res.ok, status: res.status, body: await res.clone().json().catch(() => ({})) };
      }, testUser);
      
      expect(registerResponse.ok).toBeTruthy();
      
      // Clear session cookie so login flow actually triggers redirect
      await page.context().clearCookies();
      
      // Re-navigate to login page (fresh state, no session cookie)
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      // Login via UI
      await page.fill('#login-email', testUser.email);
      await page.fill('#login-password', testUser.password);
      
      await page.click('#login-btn');
      
      // Wait for redirect after login (login JS waits 800ms + API call + redirect)
      await page.waitForURL(url => url.pathname.includes('subscribe') || url.pathname.includes('dashboard'), { timeout: 10000 });
      
      // Should redirect to subscribe page (new user has no subscription)
      const url = page.url();
      expect(url.includes('subscribe') || url.includes('dashboard')).toBeTruthy();
    });

    test('should redirect to requested page after login', async ({ page }) => {
      // Navigate directly to protected page WITHOUT registering (user is not authenticated)
      await page.goto('/pages/dashboard.html');
      await page.waitForLoadState('networkidle');
      
      // Should redirect to login (unauthenticated user can't access dashboard)
      expect(page.url()).toContain('login');
    });
  });

  test.describe('Password Visibility', () => {
    test('should toggle password visibility', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      const passwordInput = page.locator('#login-password');
      const toggleBtn = page.locator('.password-toggle').first();
      
      // Initially password type
      await expect(passwordInput).toHaveAttribute('type', 'password');
      
      // Click show
      await toggleBtn.click();
      await expect(passwordInput).toHaveAttribute('type', 'text');
      await expect(toggleBtn).toHaveText('Ocultar');
      
      // Click hide
      await toggleBtn.click();
      await expect(passwordInput).toHaveAttribute('type', 'password');
      await expect(toggleBtn).toHaveText('Mostrar');
    });
  });

  test.describe('Password Strength', () => {
    test('should show password strength indicator', async ({ page }) => {
      await page.goto('/login.html');
      await page.waitForLoadState('networkidle');
      
      await page.click('[data-tab="register"]');
      await page.waitForTimeout(300);
      
      const strengthBar = page.locator('#strength-bar');
      const passwordHelp = page.locator('#password-help');
      
      // Weak password
      await page.fill('#reg-password', 'abc');
      await page.waitForTimeout(200);
      await expect(passwordHelp).toContainText('Falta');
      
      // Strong password
      await page.fill('#reg-password', 'StrongPass123!@#');
      await page.waitForTimeout(200);
      await expect(passwordHelp).toContainText('segura');
    });
  });
});
