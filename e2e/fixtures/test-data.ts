/**
 * Test Data Fixtures
 * 
 * Pre-defined test data for E2E tests
 */

export interface TestUser {
  nombre: string;
  apellido: string;
  email: string;
  password: string;
  organizacion?: string;
}

export const TEST_USERS = {
  valid: {
    nombre: 'Juan',
    apellido: 'Pérez',
    email: `test-${Date.now()}@example.com`,
    password: 'Test1234!@#$ab',
    organizacion: 'Despacho Jurídico Test',
  },
  admin: {
    email: 'admin@themis-ia.com',
    password: 'Admin1234!@#$ab',
  },
  invalid: {
    email: 'nonexistent@example.com',
    password: 'WrongPassword123!',
  },
};

export const TEST_CASES = {
  minimal: {
    name: 'Caso Test Básico',
    description: 'Caso de prueba para E2E testing',
  },
  full: {
    name: 'Caso Homicidio Test',
    description: 'Caso de prueba con documentos múltiples para testing completo del pipeline forense',
  },
};

export const API_ENDPOINTS = {
  auth: {
    register: '/api/auth/register',
    login: '/api/auth/login',
    logout: '/api/auth/logout',
    me: '/api/auth/me',
    sessions: '/api/auth/sessions',
  },
  cases: {
    list: '/api/cases',
    create: '/api/cases',
    get: (id: string) => `/api/cases/${id}`,
    update: (id: string) => `/api/cases/${id}`,
    delete: (id: string) => `/api/cases/${id}`,
    share: (id: string) => `/api/cases/${id}/share`,
    documents: (id: string) => `/api/cases/${id}/documents`,
  },
  subscribe: {
    status: '/api/subscribe/status',
    confirm: '/api/subscribe/confirm',
    declined: '/api/subscribe/declined',
  },
  health: '/api/health',
};

/**
 * Generate unique test user data
 */
export function generateTestUser() {
  const timestamp = Date.now();
  const random = Math.floor(Math.random() * 1000);
  return {
    nombre: `Test${random}`,
    apellido: `User${random}`,
    email: `e2e-${timestamp}-${random}@test.example.com`,
    password: `SecurePass${random}!@#`,
    organizacion: `Org Test ${random}`,
  };
}

/**
 * Generate unique case data
 */
export function generateTestCase() {
  const timestamp = Date.now();
  const random = Math.floor(Math.random() * 1000);
  return {
    name: `Caso E2E ${timestamp}-${random}`,
    description: `Descripción del caso de prueba generado automáticamente ${timestamp}`,
  };
}
