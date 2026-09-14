'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const args = process.argv.slice(2);
const [fastUriRoot, tomlRoot, ...rest] = args;
let ajvRoot = null;
let n8nNodeModulesRoot = null;
let securityRoots = rest;
if (rest.length === 9) {
  [ajvRoot, n8nNodeModulesRoot, ...securityRoots] = rest;
}
if (!fastUriRoot || !tomlRoot || ![0, 7].includes(securityRoots.length)) {
  throw new Error('usage: node javascript-security-overlay-smoke.cjs <fast-uri-root> <toml-root> [ajv-root n8n-node-modules-root] [tiptap-core-root tiptap-pm-root prosemirror-model-root prosemirror-view-root xmldom-root js-yaml-root multer-root]');
}

const fastUriPackage = require(`${fastUriRoot}/package.json`);
const tomlPackage = require(`${tomlRoot}/package.json`);
assert.equal(fastUriPackage.name, 'fast-uri');
assert.equal(fastUriPackage.version, '3.1.6');
assert.equal(tomlPackage.name, 'toml');
assert.equal(tomlPackage.version, '4.2.0');
if (securityRoots.length === 7) {
  const expectedSecurityPackages = [
    [securityRoots[0], '@tiptap/core', '3.30.5'],
    [securityRoots[1], '@tiptap/pm', '3.30.5'],
    [securityRoots[2], 'prosemirror-model', '1.25.11'],
    [securityRoots[3], 'prosemirror-view', '1.41.9'],
    [securityRoots[4], '@xmldom/xmldom', '0.8.15'],
    [securityRoots[5], 'js-yaml', '4.3.2'],
    [securityRoots[6], 'multer', '2.3.0'],
  ];
  for (const [root, name, version] of expectedSecurityPackages) {
    const packageJson = require(`${root}/package.json`);
    assert.equal(packageJson.name, name);
    assert.equal(packageJson.version, version);
  }
  const tiptapJsx = require(path.join(securityRoots[0], 'jsx-runtime'));
  assert.equal(typeof tiptapJsx.jsx, 'function');
  assert.deepEqual(tiptapJsx.jsx('p', { children: 'finance' }), ['p', {}, 'finance']);
  const xmldom = require(securityRoots[4]);
  const document = new xmldom.DOMParser().parseFromString('<finance/>', 'text/xml');
  assert.equal(document.documentElement.nodeName, 'finance');
  assert.equal(typeof new xmldom.XMLSerializer().serializeToString(document), 'string');
  const yaml = require(securityRoots[5]);
  const parsedYaml = yaml.load('amount: 12.5');
  assert.deepEqual(parsedYaml, { amount: 12.5 });
  assert.equal(typeof yaml.dump(parsedYaml), 'string');
  const multerError = require(path.join(securityRoots[6], 'lib/multer-error.js'));
  assert.equal(typeof multerError, 'function');
  if (n8nNodeModulesRoot) {
    const runtimePackages = [
      ['@tiptap/core', securityRoots[0], securityRoots[0]],
      ['@tiptap/pm/transform', securityRoots[1], path.join(securityRoots[1], 'dist/transform/index.cjs')],
      ['prosemirror-model', securityRoots[2], securityRoots[2]],
      ['prosemirror-view', securityRoots[3], securityRoots[3]],
      ['@xmldom/xmldom', securityRoots[4], securityRoots[4]],
      ['js-yaml', securityRoots[5], securityRoots[5]],
      ['multer', securityRoots[6], securityRoots[6]],
    ];
    const consumerNodeModulesRoot = path.join(n8nNodeModulesRoot, '.pnpm', 'node_modules');
    const resolvedRuntimePackages = runtimePackages.map(([name, root, expected]) => {
      // Resolve through n8n's actual pnpm consumer symlinks, then prove the
      // consumer lands on the reviewed immutable replacement root.
      const resolved = require.resolve(name, { paths: [consumerNodeModulesRoot] });
      assert.equal(fs.realpathSync(resolved), fs.realpathSync(require.resolve(expected)));
      return require(resolved);
    });
    const tiptap = resolvedRuntimePackages[0];
    assert.equal(typeof tiptap.Editor, 'function');
    const tiptapTransform = resolvedRuntimePackages[1];
    assert.equal(typeof tiptapTransform.Transform, 'function');
    const prosemirrorModel = resolvedRuntimePackages[2];
    assert.equal(typeof prosemirrorModel.Schema, 'function');
    const prosemirrorView = resolvedRuntimePackages[3];
    assert.equal(typeof prosemirrorView.EditorView, 'function');
    const xmldomRuntime = resolvedRuntimePackages[4];
    assert.equal(typeof xmldomRuntime.DOMParser, 'function');
    const yamlRuntime = resolvedRuntimePackages[5];
    assert.equal(typeof yamlRuntime.load, 'function');
    const multer = resolvedRuntimePackages[6];
    assert.equal(typeof multer, 'function');
    assert.equal(typeof multer.memoryStorage, 'function');
  }
}

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

process.stdout.write('JavaScript security overlays verified: fast-uri 3.1.6, toml 4.2.0, @tiptap/core 3.30.5, @tiptap/pm 3.30.5, prosemirror-model 1.25.11, prosemirror-view 1.41.9, @xmldom/xmldom 0.8.15, js-yaml 4.3.2, multer 2.3.0\n');
