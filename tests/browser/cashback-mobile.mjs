// No browser library: exercise Chrome through its local DevTools protocol.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, mkdir, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const [chrome, url, artifactDir] = process.argv.slice(2);
const profile = await mkdtemp(join(tmpdir(), 'cashback-mobile-'));
await mkdir(artifactDir, {recursive:true});
const browser = spawn(chrome, ['--headless=new', '--no-sandbox', '--disable-gpu',
  '--disable-dev-shm-usage', '--disable-background-networking', '--disable-component-update',
  '--no-first-run', '--no-default-browser-check', '--remote-debugging-address=127.0.0.1',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {stdio:'ignore'});
let socket;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, message) {
  for (let i = 0; i < 100; i++) {
    const value = await check();
    if (value) return value;
    await pause(100);
  }
  throw new Error(message);
}
try {
  const port = await until(async () => {
    try { return (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; }
    catch { return null; }
  }, 'Chrome did not start');
  const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
  let serial = 0;
  const pending = new Map();
  const errors = [];
  socket.onmessage = event => {
    const data = JSON.parse(event.data);
    if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.text);
    if (data.id && pending.has(data.id)) {
      const [resolve, reject] = pending.get(data.id); pending.delete(data.id);
      data.error ? reject(new Error(JSON.stringify(data.error))) : resolve(data.result);
    }
  };
  const cdp = (method, params={}) => new Promise((resolve, reject) => {
    const id = ++serial; pending.set(id, [resolve, reject]); socket.send(JSON.stringify({id, method, params}));
  });
  async function evaluate(expression) {
    const result = await cdp('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
    assert.ok(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  async function tap(selector) {
    const point = await evaluate(`(() => {
      const node = document.querySelector(${JSON.stringify(selector)});
      if (!node) throw new Error('Missing tap target: ' + ${JSON.stringify(selector)});
      node.scrollIntoView({block:'center'});
      const r = node.getBoundingClientRect();
      const x = r.x + r.width / 2, y = r.y + r.height / 2;
      const hit = document.elementFromPoint(x, y);
      if (!hit || !node.contains(hit)) throw new Error('Tap target obscured');
      return {x, y};
    })()`);
    await cdp('Input.dispatchTouchEvent', {type:'touchStart', touchPoints:[point]});
    await cdp('Input.dispatchTouchEvent', {type:'touchEnd', touchPoints:[]});
    await pause(80);
  }
  async function layout(label) {
    const problems = await evaluate(`(() => {
      const visible = node => node.checkVisibility({checkVisibilityCSS:true}) && node.getBoundingClientRect().width > 0;
      const issues = [];
      if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('page horizontal overflow');
      for (const node of document.querySelectorAll('main *')) {
        if (!visible(node)) continue;
        const r = node.getBoundingClientRect();
        if (r.left < -1 || r.right > innerWidth + 1) issues.push('outside viewport: ' + node.className);
        if (!node.children.length && /(?:AED|\\d[.,]\\d|\\d%)/.test(node.textContent)
            && parseFloat(getComputedStyle(node).fontSize) < 12) issues.push('financial label below 12px: ' + node.textContent);
      }
      return issues;
    })()`);
    assert.deepEqual(problems, [], label);
  }
  async function screenshot(name, target) {
    await evaluate(target ? `document.querySelector(${JSON.stringify(target)}).scrollIntoView({block:'start'})` : 'window.scrollTo(0,0)');
    const shot = await cdp('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
    await writeFile(join(artifactDir, name + '.png'), Buffer.from(shot.data, 'base64'));
  }
  await cdp('Page.enable'); await cdp('Runtime.enable');
  for (const width of [375, 430]) {
    await cdp('Emulation.setDeviceMetricsOverride', {width, height:932, deviceScaleFactor:1, mobile:true});
    await cdp('Emulation.setTouchEmulationEnabled', {enabled:true});
    await cdp('Page.navigate', {url});
    await until(() => evaluate('document.querySelectorAll("#recommendations .route-row").length === 6'), 'Dashboard did not render six fixture categories');
    assert.equal(await evaluate('document.body.dataset.screenActive'), 'routing');
    assert.equal(await evaluate('document.querySelector("#attention .alert-card").checkVisibility()'), true);
    assert.match(await evaluate('document.querySelector("#attention").innerText'), /1,249.75/);
    assert.match(await evaluate('document.querySelector("#as-of").innerText'), /Checked/);
    assert.match(await evaluate('document.querySelector("#card-summary").textContent'), /4,250.25/);
    assert.match(await evaluate('document.querySelector("#recommendations .route-row summary").textContent'), /Groceries/);
    assert.match(await evaluate('document.querySelector("#recommendations .route-row summary").textContent'), /RAK/);
    assert.match(await evaluate('document.querySelector("#recommendations .route-row summary").textContent'), /1,245.25/);
    const columns = await evaluate(`(() => { const rows = [...document.querySelectorAll('#recommendations .route-row')]; return rows.slice(0,2).map(node => {const r=node.getBoundingClientRect(); return {x:r.x,y:r.y};}); })()`);
    assert.ok(Math.abs(columns[0].y - columns[1].y) < 2 && columns[1].x > columns[0].x, 'mobile categories must form two columns');
    await layout('overview ' + width); await screenshot(`fictional-${width}-overview`);
    await tap('#recommendations .route-row summary');
    assert.equal(await evaluate('document.querySelector("#recommendations .route-row").open'), true);
    assert.match(await evaluate('document.querySelector("#recommendations .bucket-detail").innerText'), /254.75/);
    await layout('expanded bucket ' + width); await screenshot(`fictional-${width}-bucket`, '.bucket-detail');
    await tap('#recommendations .route-row [data-detail-view="routing"]');
    assert.equal(await evaluate('document.querySelector("#recommendations .routing-tree").checkVisibility()'), true);
    assert.ok(await evaluate('document.querySelectorAll("#recommendations .routing-tree .routing-step").length >= 2'), 'ordered alternatives must be rendered');
    const routeText = await evaluate('document.querySelector("#recommendations .routing-tree").innerText');
    assert.match(routeText, /RAK/); assert.match(routeText, /SimplyCash/); assert.match(routeText, /Apple Pay/);
    assert.ok(routeText.indexOf('RAK') < routeText.indexOf('SimplyCash'), 'preferred route precedes fallback');
    await layout('expanded route ' + width); await screenshot(`fictional-${width}-route`, '.routing-tree');
    await tap('#recommendations .route-row [data-detail-view="bucket"]');
    assert.equal(await evaluate('document.querySelector("#recommendations .bucket-facts").checkVisibility()'), true);
    await tap('#recommendations .route-row summary');
    assert.equal(await evaluate('document.querySelector("#recommendations .route-row").open'), false);
    await tap('[data-screen-view="cards"]');
    assert.equal(await evaluate('document.body.dataset.screenActive'), 'cards');
    assert.equal(await evaluate('document.querySelector("[data-screen-view=cards]").getAttribute("aria-selected")'), 'true');
    assert.match(await evaluate('document.querySelector("#cards").innerText'), /254.75/);
    await tap('#cards .card-header');
    assert.equal(await evaluate('document.querySelector("#cards .card-details").open'), true);
    assert.match(await evaluate('document.querySelector("#cards .card-facts").innerText'), /5,500/);
    assert.equal(await evaluate('document.querySelector("#history-section").hidden'), true);
    await layout('cards ' + width); await screenshot(`fictional-${width}-cards`, '#cards');
    await tap('[data-screen-view="routing"]');
    assert.equal(await evaluate('document.body.dataset.screenActive'), 'routing');
    await tap('#card-summary .card-total');
    assert.equal(await evaluate('document.body.dataset.screenActive'), 'cards');
    assert.equal(await evaluate('document.querySelector("#cards .card-details").open'), true);
  }
  assert.deepEqual(errors, [], 'browser runtime errors');
  await writeFile(join(artifactDir, 'README.txt'), 'Fictional data only. Chrome touch emulation at 375 and 430 CSS pixels. Overview, expanded bucket, routing and card positions.\n');
  console.log('Mobile browser interaction/layout checks passed at 375px and 430px.');
} finally {
  socket?.close(); browser.kill('SIGTERM');
  await Promise.race([new Promise(resolve => browser.once('exit', resolve)), pause(1500)]);
  if (browser.exitCode === null) browser.kill('SIGKILL');
  await rm(profile, {recursive:true, force:true, maxRetries:3, retryDelay:100});
}
