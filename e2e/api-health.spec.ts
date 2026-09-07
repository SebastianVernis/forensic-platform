/**
 * API Health Check E2E Tests
 * 
 * Basic tests to verify API is working
 */

import { test, expect } from '@playwright/test';

test.describe('API Health', () => {
  test('should return health status', async ({ request }) => {
    const response = await request.get('/api/health');
    expect(response.ok()).toBeTruthy();
    
    const data = await response.json();
    expect(data.ok).toBe(true);
  });

  test('should handle 404 for unknown routes', async ({ request }) => {
    const response = await request.get('/api/nonexistent');
    expect(response.status()).toBe(404);
  });

  test('should require auth for protected routes', async ({ request }) => {
    const response = await request.get('/api/auth/me');
    expect(response.status()).toBe(401);
  });

  test('should require auth for cases endpoint', async ({ request }) => {
    const response = await request.get('/api/cases');
    expect(response.status()).toBe(401);
  });
});
