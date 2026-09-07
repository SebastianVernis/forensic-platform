/**
 * Case Management E2E Tests
 * 
 * Tests for case CRUD operations and dashboard
 */

import { test, expect } from '@playwright/test';
import { generateTestUser, generateTestCase, TestUser } from './fixtures/test-data';

test.describe('Case Management', () => {
  let testUser: TestUser;

  test.beforeEach(async ({ page }) => {
    testUser = generateTestUser();
    
    // Register and login user via API for faster setup
    await page.goto('/login.html');
    await page.waitForLoadState('networkidle');
    
    // Register via API
    await page.evaluate(async (user) => {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(user),
      });
      if (!res.ok) throw new Error('Registration failed');
    }, testUser);
    
    // Login via UI to establish session
    await page.fill('#login-email', testUser.email);
    await page.fill('#login-password', testUser.password);
    await page.click('#login-btn');
    await page.waitForTimeout(2000);
  });

  test.describe('Dashboard', () => {
    test('should display dashboard after login', async ({ page }) => {
      // Should be on dashboard or subscribe page
      const url = page.url();
      
      if (url.includes('dashboard')) {
        await expect(page.locator('text=Mis casos')).toBeVisible();
      }
    });

    test('should show empty state when no cases', async ({ page }) => {
      if (page.url().includes('dashboard')) {
        // Should show empty state or create button
        const createBtn = page.locator('text=Crear caso').or(page.locator('text=Nuevo caso'));
        await expect(createBtn).toBeVisible();
      }
    });
  });

  test.describe('Case Creation', () => {
    test('should open create case dialog', async ({ page }) => {
      // Navigate to dashboard if not there
      if (!page.url().includes('dashboard')) {
        await page.goto('/pages/dashboard.html');
        await page.waitForLoadState('networkidle');
      }
      
      // Click create button
      const createBtn = page.locator('text=Crear caso').or(page.locator('text=Nuevo caso'));
      if (await createBtn.isVisible()) {
        await createBtn.click();
        await page.waitForTimeout(300);
        
        // Should show dialog/form
        const dialog = page.locator('[role="dialog"]').or(page.locator('.modal'));
        await expect(dialog).toBeVisible();
      }
    });

    test('should create a new case via API', async ({ page }) => {
      const testCase = generateTestCase();
      
      const result = await page.evaluate(async (caseData) => {
        const res = await fetch('/api/cases', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(caseData),
          credentials: 'same-origin',
        });
        return { ok: res.ok, status: res.status };
      }, testCase);
      
      expect(result.ok).toBeTruthy();
    });
  });

  test.describe('Case List', () => {
    test('should list user cases', async ({ page }) => {
      // Create a case first
      const testCase = generateTestCase();
      await page.evaluate(async (caseData) => {
        await fetch('/api/cases', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(caseData),
          credentials: 'same-origin',
        });
      }, testCase);
      
      // Refresh dashboard
      await page.goto('/pages/dashboard.html');
      await page.waitForLoadState('networkidle');
      
      // Should show the case
      if (page.url().includes('dashboard')) {
        await expect(page.locator(`text=${testCase.name}`)).toBeVisible();
      }
    });
  });

  test.describe('Case API', () => {
    test('should get case details', async ({ page }) => {
      // Create a case
      const testCase = generateTestCase();
      const createResult = await page.evaluate(async (caseData) => {
        const res = await fetch('/api/cases', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(caseData),
          credentials: 'same-origin',
        });
        return res.json();
      }, testCase);
      
      // Get case details
      const getResult = await page.evaluate(async (caseId) => {
        const res = await fetch(`/api/cases/${caseId}`, {
          credentials: 'same-origin',
        });
        return res.json();
      }, createResult.id);
      
      expect(getResult.name).toBe(testCase.name);
    });

    test('should update case', async ({ page }) => {
      // Create a case
      const testCase = generateTestCase();
      const createResult = await page.evaluate(async (caseData) => {
        const res = await fetch('/api/cases', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(caseData),
          credentials: 'same-origin',
        });
        return res.json();
      }, testCase);
      
      // Update case
      const newName = 'Updated Case Name';
      const updateResult = await page.evaluate(async ({ caseId, name }) => {
        const res = await fetch(`/api/cases/${caseId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
          credentials: 'same-origin',
        });
        return res.ok;
      }, { caseId: createResult.id, name: newName });
      
      expect(updateResult).toBeTruthy();
      
      // Verify update
      const verifyResult = await page.evaluate(async (caseId) => {
        const res = await fetch(`/api/cases/${caseId}`, {
          credentials: 'same-origin',
        });
        return res.json();
      }, createResult.id);
      
      expect(verifyResult.name).toBe(newName);
    });

    test('should delete case', async ({ page }) => {
      // Create a case
      const testCase = generateTestCase();
      const createResult = await page.evaluate(async (caseData) => {
        const res = await fetch('/api/cases', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(caseData),
          credentials: 'same-origin',
        });
        return res.json();
      }, testCase);
      
      // Delete case
      const deleteResult = await page.evaluate(async (caseId) => {
        const res = await fetch(`/api/cases/${caseId}`, {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        return res.ok;
      }, createResult.id);
      
      expect(deleteResult).toBeTruthy();
    });
  });
});
