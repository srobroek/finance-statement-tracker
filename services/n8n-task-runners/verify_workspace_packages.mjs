import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const closure = fs.realpathSync(process.argv[2] ?? '.');
const manifest = JSON.parse(
  fs.readFileSync(path.join(closure, 'finance-workspace-packages.json'), 'utf8'),
);

if (manifest.schema_version !== 1 || typeof manifest.packages !== 'object') {
  throw new Error('invalid workspace package manifest');
}

for (const [name, version] of Object.entries(manifest.packages)) {
  const packageDirectory = fs.realpathSync(path.join(closure, 'node_modules', ...name.split('/')));
  const relative = path.relative(closure, packageDirectory);
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`package escapes closure: ${name}`);
  }
  if (!fs.statSync(packageDirectory).isDirectory()) {
    throw new Error(`package is not a directory: ${name}`);
  }
  const packageJson = JSON.parse(
    fs.readFileSync(path.join(packageDirectory, 'package.json'), 'utf8'),
  );
  if (packageJson.name !== name || packageJson.version !== version) {
    throw new Error(`package identity mismatch: ${name}`);
  }
}

const require = createRequire(path.join(closure, 'finance-entrypoint-check.cjs'));
for (const exported of ['@n8n/di', 'moment', './dist/start.js']) {
  require.resolve(exported);
}
