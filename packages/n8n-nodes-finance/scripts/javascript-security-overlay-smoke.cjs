'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const [fastUriRoot, tomlRoot, ajvRoot, n8nNodeModulesRoot] = process.argv.slice(2);
if (!fastUriRoot || !tomlRoot) {
  throw new Error('usage: node javascript-security-overlay-smoke.cjs <fast-uri-root> <toml-root> [ajv-root]');
}

const fastUriPackage = require(`${fastUriRoot}/package.json`);
const tomlPackage = require(`${tomlRoot}/package.json`);
assert.equal(fastUriPackage.name, 'fast-uri');
assert.equal(fastUriPackage.version, '3.1.6');
assert.equal(tomlPackage.name, 'toml');
assert.equal(tomlPackage.version, '4.2.0');

const fastUri = require(fastUriRoot);
assert.equal(typeof fastUri.parse, 'function');
assert.equal(typeof fastUri.normalize, 'function');
assert.equal(fastUri.parse('https://example.com/finance?q=1').host, 'example.com');
assert.equal(fastUri.normalize('https://example.com/a/../finance?q=1'), 'https://example.com/finance?q=1');

const toml = require(tomlRoot);
const snowflakeProfile = toml.parse([
  '[default]',
  'account = "org-account"',
  'user = "finance"',
  'warehouse = "FINANCE_WH"',
  'database = "FINANCE"',
  'schema = "PUBLIC"',
  'role = "ANALYST"',
  'authenticator = "SNOWFLAKE"',
  'loginTimeout = 60',
  'clientSessionKeepAlive = true',
].join('\n'));
assert.deepEqual(
  { ...snowflakeProfile.default },
  {
    account: 'org-account',
    user: 'finance',
    warehouse: 'FINANCE_WH',
    database: 'FINANCE',
    schema: 'PUBLIC',
    role: 'ANALYST',
    authenticator: 'SNOWFLAKE',
    loginTimeout: 60,
    clientSessionKeepAlive: true,
  },
);
assert.equal(Object.getPrototypeOf(snowflakeProfile), null);
assert.equal(Object.getPrototypeOf(snowflakeProfile.default), null);
const pollutionAttempt = toml.parse('__proto__.polluted = "yes"');
assert.equal({}.polluted, undefined);
assert.equal(Object.getPrototypeOf(pollutionAttempt), null);
assert.throws(
  () => toml.parse(`value = ${'['.repeat(501)}0${']'.repeat(501)}`),
  /Maximum nesting depth of 500 exceeded/,
);

if (ajvRoot) {
  const ajvFastUriPackage = require.resolve('fast-uri/package.json', { paths: [ajvRoot] });
  assert.equal(fs.realpathSync(path.dirname(ajvFastUriPackage)), fs.realpathSync(fastUriRoot));
  const AjvModule = require(ajvRoot);
  const Ajv = AjvModule.default || AjvModule;
  const ajv = new Ajv();
  ajv.addSchema({
    $id: 'https://schemas.example.test/finance/amount.json',
    type: 'number',
    minimum: 0,
  });
  const validate = ajv.compile({
    $id: 'https://schemas.example.test/finance/statement.json',
    type: 'object',
    properties: {
      amount: { $ref: './amount.json' },
    },
    required: ['amount'],
    additionalProperties: false,
  });
  assert.equal(validate({ amount: 12.5 }), true);
  assert.equal(validate({ amount: -1 }), false);
}

if (n8nNodeModulesRoot) {
  const pnpmRoot = path.join(n8nNodeModulesRoot, '.pnpm');
  const snowflakeStores = fs.readdirSync(pnpmRoot).filter(entry => entry.startsWith('snowflake-sdk@2.1.0_'));
  assert.equal(snowflakeStores.length, 1);
  const snowflakeRoot = path.join(pnpmRoot, snowflakeStores[0], 'node_modules', 'snowflake-sdk');
  const snowflakeTomlPackage = require.resolve('toml/package.json', { paths: [snowflakeRoot] });
  assert.equal(fs.realpathSync(path.dirname(snowflakeTomlPackage)), fs.realpathSync(tomlRoot));
}

process.stdout.write('JavaScript security overlays verified: fast-uri 3.1.6, toml 4.2.0\n');
