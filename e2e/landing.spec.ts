/**
 * Landing Page E2E Tests
 * 
 * Tests for the public landing page at /
 */

import { test, expect } from '@playwright/test';

test.describe('Landing Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
  });

  test('should load landing page with correct title', async ({ page }) => {
    await expect(page).toHaveTitle(/Themis IA/);
  });

  test('should display hero section', async ({ page }) => {
    const hero = page.locator('.hero');
    await expect(hero).toBeVisible();
    
    // Check hero content — use specific selectors to avoid strict mode violations
    await expect(page.locator('.hero h1')).toContainText('THEMIS');
    await expect(page.locator('.hero p').first()).toContainText('Análisis forense');
  });

  test('should display features section', async ({ page }) => {
    const features = page.locator('#features');
    await expect(features).toBeVisible();
    
    // Check feature cards
    const featureCards = page.locator('.feature-card');
    await expect(featureCards).toHaveCount(6);
  });

  test('should display pricing section', async ({ page }) => {
    const pricing = page.locator('#pricing');
    await expect(pricing).toBeVisible();
    
    // Check pricing cards
    const pricingCards = page.locator('.pricing-card');
    await expect(pricingCards).toHaveCount(4);
  });

  test('should have working navigation links', async ({ page }) => {
    // Check login link
    const loginLink = page.locator('a[href*="login.html"]').first();
    await expect(loginLink).toBeVisible();
  });

  test('should navigate to login page', async ({ page }) => {
    // Click the hero CTA (not the hidden navbar link)
    await page.locator('.hero a[href*="login.html"]').click();
    await page.waitForLoadState('networkidle');
    
    expect(page.url()).toContain('login');
    await expect(page.locator('text=Themis IA')).toBeVisible();
  });

  test('should display how it works section', async ({ page }) => {
    const howItWorks = page.locator('#how');
    await expect(howItWorks).toBeVisible();
    
    // Check steps
    const steps = page.locator('.step');
    await expect(steps).toHaveCount(4);
  });

  test('should have responsive navbar on scroll', async ({ page }) => {
    // Navbar should be hidden initially
    const navbar = page.locator('#navbar');
    
    // Scroll down to trigger navbar
    await page.evaluate(() => window.scrollTo(0, 500));
    await page.waitForTimeout(500);
    
    // Navbar should become visible
    await expect(navbar).toHaveClass(/show/);
  });
});
