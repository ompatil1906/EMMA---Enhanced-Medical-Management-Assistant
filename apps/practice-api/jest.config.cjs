/** @type {import('jest').Config} */
const shared = {
  testEnvironment: 'node',
  moduleFileExtensions: ['ts', 'js', 'json'],
  // NodeNext sources import relative modules with explicit .js suffixes; strip
  // the suffix for jest so the sibling .ts files resolve. Only relative
  // specifiers are affected — package internals keep their real .js files.
  moduleNameMapper: {
    // Project sources import relative modules with .js suffixes; map to .ts
    // so Jest can resolve the sibling TypeScript files.
    '^(\\.\\./common/.*)\\.js$': '$1.ts',
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  transform: {
    '^.+\\.ts$': [
      'ts-jest',
      {
        tsconfig: '<rootDir>/tsconfig.json',
        diagnostics: false,
        isolatedModules: true,
      },
    ],
    // @nestjs/typeorm v12 is ESM-only; compile it (and typeorm) to CJS for Jest.
    '^.+\\.js$': '<rootDir>/scripts/jest-esbuild-transform.cjs',
  },
  // Allow transforming ESM packages that Jest would otherwise ignore.
  // Matches both hoisted (node_modules/@nestjs/typeorm/) and pnpm-store
  // (node_modules/.pnpm/@nestjs+typeorm@.../node_modules/@nestjs/typeorm/) layouts.
  transformIgnorePatterns: [
    '/node_modules/(?!.*(@nestjs/typeorm|typeorm)/)',
  ],
};

module.exports = {
  projects: [
    { ...shared, displayName: 'unit', testMatch: ['<rootDir>/test/unit/**/*.spec.ts'] },
    { ...shared, displayName: 'db', testMatch: ['<rootDir>/test/db/**/*.spec.ts'] },
  ],
};
