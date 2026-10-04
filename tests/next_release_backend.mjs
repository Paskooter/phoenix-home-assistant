// Disposable cross-repository backend. All identities and robot transport are
// invented; this proves server contracts, never physical robot speech.
import { mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import https from 'node:https';
import http from 'node:http';

if (!process.env.PHOENIX_HA_TEST_ROOT || !process.env.PHOENIX_SERVER_DIR) {
  throw new Error('Explicit disposable storage and Phoenix source are required');
}
const source = resolve(process.env.PHOENIX_SERVER_DIR);
const root = resolve(process.env.PHOENIX_HA_TEST_ROOT, 'next-release');
mkdirSync(root, { recursive: true });
const load = relative => import(pathToFileURL(join(source, relative)).href);
const [{ Store }, { createAccountService }, { createSession }, { createGateway },
  { loadConfig }, { createService, jwt, createDeploymentActivity }, { parseRequest },
  { readActivity, waitForQuiescence }, { createRobotAnnouncementAdapter }, { HomeAssistantBroker }] = await Promise.all([
  load('packages/account/src/store.js'), load('packages/account/src/index.js'),
  load('packages/account/src/sessions.js'), load('packages/gateway/src/index.js'),
  load('packages/gateway/src/config.js'), load('packages/common/src/index.js'),
  load('packages/nlu/src/requestParser.js'), load('scripts/deployment-quiescence.mjs'),
  load('packages/account/src/integrations/homeAssistant/robotAdapter.js'),
  load('packages/account/src/integrations/homeAssistant/broker.js'),
]);
const { WebSocket } = createRequire(join(source, 'package.json'))('ws');
process.env.ETCO_account_internalPeerToken = 'synthetic-next-peer';
process.env.PHOENIX_RUNTIME_DIR = join(root, 'run');
process.env.PHOENIX_VOICE_TURN_FILE = join(root, 'voice-turns.json');
mkdirSync(process.env.PHOENIX_RUNTIME_DIR, { recursive: true });
writeFileSync(join(process.env.PHOENIX_RUNTIME_DIR, 'services.json'), JSON.stringify({ services:
  Object.fromEntries(['hub', 'ota'].map(name => [name, { pid: process.pid, state: 'running' }])) }));
const ota = createDeploymentActivity('ota');
const store = new Store(join(root, 'account.json'));
const owner = { _id: 'synthetic-next-owner', isActive: true, email: 'owner@example.invalid' };
const stranger = { _id: 'synthetic-next-stranger', isActive: true, email: 'stranger@example.invalid' };
const robots = ['A', 'B'].map(label => ({ _id: `synthetic-next-robot-${label.toLowerCase()}`,
  friendlyId: `synthetic-next-jibo-${label.toLowerCase()}`, name: `Test Robot ${label}`,
  isActive: true, accessKeyId: `synthetic-next-key-${label.toLowerCase()}` }));
for (const account of [owner, stranger, ...robots]) store.accounts.set(account._id, account);
for (const robot of robots) store.loops.set(`synthetic-loop-${robot._id}`, {
  _id: `synthetic-loop-${robot._id}`, name: 'Invented Test Loop', owner: owner._id,
  robot: robot._id, members: [],
});
const session = createSession(store, { kind: 'user', accountId: owner._id });
const otherSession = createSession(store, { kind: 'user', accountId: stranger._id });
const controls = new Map(robots.map(robot => [robot._id, {
  online: true, busy: false, executing: false, active_request_id: null, mode: 'confirmed', cancel_mode: 'confirm',
}]));
const dispatches = [];
const cancellations = [];
const holds = new Map();
const serverFrames = [];
const clientFrames = [];
const receivers = new Map();
const recovered = [];
let nativeAdapter;
let guard;
let verifyGate;
let verifyWaiting = 0;
const robotAdapter = {
  status: identity => nativeAdapter ? nativeAdapter.status(identity) : { online: false, busy: false },
  announce: input => nativeAdapter.announce(input),
};
const account = createAccountService({ store, homeAssistantOptions: { robotAdapter } });
await account.listen(0, '127.0.0.1');
let authorizationRequests = 0;
account.server.on('request', request => {
  if (request.method === 'POST' && request.url === '/internal/home-assistant/robot-action/authorize') authorizationRequests++;
});
const accountUrl = `http://127.0.0.1:${account.server.address().port}`;
account.homeAssistant.wss.on('connection', socket => {
  socket.on('message', data => {
    try { clientFrames.push(JSON.parse(String(data))); } catch { /* rejected by the real broker */ }
    while (clientFrames.length > 256) clientFrames.shift();
  });
  const send = socket.send.bind(socket);
  socket.send = (data, ...args) => {
    try { serverFrames.push(JSON.parse(String(data))); } catch { /* pings are not JSON */ }
    while (serverFrames.length > 256) serverFrames.shift();
    return send(data, ...args);
  };
});
const parser = createService({ name: 'next-test-parser', routes: {
  'POST /v1/parse': ({ body }) => ({ data: parseRequest(body.data || body) }),
} });
await parser.listen(0, '127.0.0.1');
const config = await loadConfig({ ETCO_hub_accountUrl: accountUrl,
  ETCO_account_internalPeerToken: 'synthetic-next-peer', ETCO_server_hubTokenSecret: 'synthetic-next-hub',
  NET_skills: '127.0.0.1:1', NET_parser: `127.0.0.1:${parser.server.address().port}`,
  ETCO_hub_recordLaunchHistory: 'false' });
config.robotActions = { fetchImpl: async (url, options) => {
  if (verifyGate && String(url).includes('/api/verify?')) {
    verifyWaiting++;
    try { await verifyGate.promise; } finally { verifyWaiting--; }
  }
  return await fetch(url, options);
} };
const gateway = await createGateway(config);
await gateway.service.listen(0, '127.0.0.1');
const gatewayUrl = `http://127.0.0.1:${gateway.service.server.address().port}`;
let receiverOrigin = gatewayUrl;
let replacement;
nativeAdapter = createRobotAnnouncementAdapter({ url: gatewayUrl, token: 'synthetic-next-peer' });
const robotToken = robot => jwt.sign({ id: robot._id, accessKeyId: robot.accessKeyId,
  friendlyId: robot.friendlyId, exp: Math.floor(Date.now() / 1000) + 600 }, config.hubTokenSecret);
const nativeResult = result => result?.outcome === 'success'
  ? { outcome: 'completed', confirmed: result.confirmed === true }
  : result?.outcome === 'error' ? { outcome: 'rejected', code: result.code || 'busy' }
    : { outcome: 'uncertain', code: result?.code || 'speech_failed' };
async function connectReceiver(robot) {
  const state = controls.get(robot._id);
  const socket = new WebSocket(receiverOrigin.replace('http://', 'ws://') + '/v1/robot-actions', {
    headers: { Authorization: `Bearer ${robotToken(robot)}`, 'x-jibo-robotid': 'forged-header-id' },
  });
  socket.on('error', () => {});
  socket.on('message', async data => {
    const frame = JSON.parse(String(data));
    if (frame.type === 'cancel') {
      cancellations.push({ request_id: frame.request_id, reason: frame.reason, robot: robot.friendlyId });
      if (state.cancel_mode === 'hold') return;
      holds.delete(frame.request_id);
      state.executing = false;
      state.active_request_id = null;
      socket.send(JSON.stringify({ v: 1, type: 'action_result', request_id: frame.request_id,
        outcome: 'uncertain', code: 'interrupted' }));
      return;
    }
    if (frame.type !== 'announce') return;
    state.executing = true;
    state.active_request_id = frame.request_id;
    dispatches.push({ request_id: frame.request_id, robot: robot.friendlyId, text: frame.text,
      deadline: frame.deadline_ms, started_at_ms: Date.now(),
      native_keys: Object.keys(frame).sort(),
      persisted_before_dispatch: readFileSync(join(root, 'account.json'), 'utf8').includes(frame.request_id) });
    if (state.mode === 'hold') { holds.set(frame.request_id, socket); return; }
    if (state.delay_ms) await new Promise(resolveDelay => setTimeout(resolveDelay, state.delay_ms));
    if (state.mode === 'throw') { socket.terminate(); return; }
    const result = state.mode === 'confirmed' ? { outcome: 'success', confirmed: true }
      : state.mode === 'reject' ? { outcome: 'error', code: 'busy' } : { outcome: 'success', confirmed: false };
    state.executing = false;
    state.active_request_id = null;
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ v: 1, type: 'action_result',
      request_id: frame.request_id, ...nativeResult(result) }));
  });
  await new Promise((resolveOpen, rejectOpen) => { socket.once('open', resolveOpen); socket.once('error', rejectOpen); });
  receivers.set(robot._id, socket);
  socket.send(JSON.stringify({ v: 1, type: 'ready', capabilities: ['announce'], busy: state.busy || state.executing,
    active_request_id: state.active_request_id }));
  let ready = false;
  for (let tries = 0; tries < 100 && !ready; tries++) {
    const status = await nativeAdapter.status({ id: robot._id, accessKeyId: robot.accessKeyId, friendlyId: robot.friendlyId });
    ready = status.announcements_supported === true;
    if (!ready) await new Promise(resolveReady => setTimeout(resolveReady, 10));
  }
  if (!ready) throw new Error('Synthetic native receiver did not become ready');
}
for (const robot of robots) await connectReceiver(robot);
const receiverTimer = setInterval(() => {
  for (const robot of robots) {
    const socket = receivers.get(robot._id);
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ v: 1, type: 'status',
      busy: controls.get(robot._id).busy || controls.get(robot._id).executing,
      active_request_id: controls.get(robot._id).active_request_id }));
  }
}, 500);
receiverTimer.unref();

async function launchReplacement({ reconnect = true } = {}) {
  writeFileSync(replacement.config, JSON.stringify({ accountUrl,
    parser: `127.0.0.1:${parser.server.address().port}`, port: replacement.port || 0 }));
  const child = spawn(process.execPath, [fileURLToPath(new URL('./next_release_gateway.mjs', import.meta.url))], {
    cwd: source, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env,
      PHOENIX_HA_RESTART_CONFIG: replacement.config,
      PHOENIX_RUNTIME_DIR: replacement.runtime,
      PHOENIX_VOICE_TURN_FILE: join(replacement.runtime, 'voice-turns.json') },
  });
  replacement.child = child;
  replacement.stderr = '';
  child.stderr.on('data', data => { replacement.stderr += String(data); });
  writeFileSync(join(replacement.runtime, 'services.json'), JSON.stringify({ services: {
    hub: { pid: child.pid, state: 'running' }, ota: { pid: process.pid, state: 'running' },
  } }));
  const lines = createInterface({ input: child.stdout });
  const ready = await new Promise((resolveReady, rejectReady) => {
    const timer = setTimeout(() => rejectReady(new Error('Replacement Gateway did not start: ' + replacement.stderr)), 25000);
    child.once('error', error => { clearTimeout(timer); rejectReady(error); });
    child.once('exit', code => { clearTimeout(timer); rejectReady(new Error(`Replacement Gateway exited (${code}): ${replacement.stderr}`)); });
    lines.on('line', line => {
      try {
        const data = JSON.parse(line);
        if (data.test_gateway_started) { clearTimeout(timer); resolveReady(data); }
      } catch { /* Gateway logs are separate from the fixture readiness message. */ }
    });
  });
  replacement.port = Number(new URL(ready.gateway_url).port);
  receiverOrigin = ready.gateway_url;
  nativeAdapter = createRobotAnnouncementAdapter({ url: receiverOrigin, token: 'synthetic-next-peer' });
  if (reconnect) for (const robot of robots) await connectReceiver(robot);
  await account.homeAssistant.broadcastRoster();
  return { ...ready, runtime_dir: replacement.runtime };
}

async function killReplacement() {
  const child = replacement.child;
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise(resolveExit => {
    child.once('exit', resolveExit);
    child.kill('SIGKILL');
  });
}

const certificate = join(root, 'tls.pem');
const key = join(root, 'tls.key');
const generated = spawnSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
  '-keyout', key, '-out', certificate, '-subj', '/CN=localhost',
  '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'], { stdio: 'ignore' });
if (generated.status !== 0) throw new Error('Test certificate generation failed');
const json = (res, value, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value));
};
async function body(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString() || '{}');
}
const edge = https.createServer({ key: readFileSync(key), cert: readFileSync(certificate) }, async (req, res) => {
  try {
    if (req.url === '/test/state') {
      return json(res, { dispatches, cancellations, authorization_requests: authorizationRequests,
        server_frames: serverFrames, client_frames: clientFrames,
        controls: robots.map(robot => ({ robot: robot.friendlyId, ...controls.get(robot._id) })),
        pending: account.homeAssistant.pending.size, held: [...holds.keys()], verify_waiting: verifyWaiting });
    }
    if (req.url === '/test/verify-hold') {
      const value = await body(req);
      if (value.enabled) {
        if (verifyGate) return json(res, { error: 'verification_already_held' }, 409);
        let release;
        const promise = new Promise(resolveGate => { release = resolveGate; });
        verifyGate = { promise, release };
      } else if (verifyGate) { verifyGate.release(); verifyGate = null; }
      return json(res, {});
    }
    if (req.url === '/test/control') {
      const value = await body(req);
      const robot = robots.find(item => item.friendlyId === value.robot);
      if (!robot) return json(res, { error: 'unknown_fixture_robot' }, 400);
      const state = controls.get(robot._id);
      for (const field of ['online', 'busy', 'mode', 'delay_ms', 'cancel_mode', 'executing']) {
        if (field in value) state[field] = value[field];
      }
      if (value.executing === false) state.active_request_id = null;
      const receiver = receivers.get(robot._id);
      if (!state.online) receiver?.terminate();
      else if (!receiver || receiver.readyState !== WebSocket.OPEN) await connectReceiver(robot);
      else receiver.send(JSON.stringify({ v: 1, type: 'status', busy: state.busy || state.executing,
        active_request_id: state.active_request_id }));
      await new Promise(resolveStatus => setTimeout(resolveStatus, 20));
      await account.homeAssistant.broadcastRoster();
      return json(res, {});
    }
    if (req.url === '/test/release') {
      const value = await body(req);
      const receiver = holds.get(value.request_id);
      if (!receiver) return json(res, { error: 'unknown_fixture_request' }, 400);
      holds.delete(value.request_id);
      const dispatch = dispatches.find(item => item.request_id === value.request_id);
      const robot = robots.find(item => item.friendlyId === dispatch.robot);
      controls.get(robot._id).executing = false;
      controls.get(robot._id).active_request_id = null;
      if (receiver.readyState === WebSocket.OPEN) receiver.send(JSON.stringify({ v: 1, type: 'action_result',
        request_id: value.request_id, ...nativeResult(value.result || { outcome: 'success', confirmed: true }) }));
      return json(res, {});
    }
    if (req.url === '/test/disconnect') {
      for (const socket of account.homeAssistant.wss.clients) socket.terminate();
      return json(res, {});
    }
    if (req.url === '/test/revoke') {
      for (const row of store.homeAssistantInstallations.values()) account.homeAssistant.revoke(row);
      return json(res, {});
    }
    if (req.url === '/test/transfer') {
      const value = await body(req);
      const robot = robots.find(item => item.friendlyId === value.robot);
      if (!robot) return json(res, { error: 'unknown_fixture_robot' }, 400);
      store.loops.get(`synthetic-loop-${robot._id}`).owner = stranger._id;
      store.flush(); account.homeAssistant.sweep();
      return json(res, {});
    }
    if (req.url === '/test/inject-command') {
      const value = await body(req);
      const linked = [...account.homeAssistant.sessions.values()].find(item => item.ready);
      if (!linked) return json(res, { error: 'fixture_not_connected' }, 409);
      linked.socket.send(JSON.stringify({ v: 1, type: 'command', session_id: linked.id, ...value }));
      return json(res, {});
    }
    if (req.url === '/test/telemetry') return json(res, readActivity(process.env.PHOENIX_RUNTIME_DIR));
    if (req.url === '/test/restart-start') {
      if (replacement) return json(res, { error: 'replacement_already_started' }, 409);
      for (const socket of receivers.values()) socket.terminate();
      receivers.clear();
      const runtime = join(root, 'replacement-run');
      mkdirSync(runtime, { recursive: true });
      replacement = { runtime, config: join(root, 'replacement-config.json'),
        ota: createDeploymentActivity('ota', { runtimeDir: runtime }), guard: null };
      return json(res, await launchReplacement());
    }
    if (req.url === '/test/restart-reconnect') {
      for (const robot of robots) await connectReceiver(robot);
      await account.homeAssistant.broadcastRoster();
      return json(res, {});
    }
    if (req.url === '/test/restart') {
      const value = await body(req);
      await killReplacement();
      if (value.draining) writeFileSync(join(replacement.runtime, 'deployment', 'drain.json'),
        JSON.stringify({ version: 1, id: 'synthetic-recovered-drain', ownerPid: process.pid,
          expiresAt: Date.now() + 90000 }));
      return json(res, await launchReplacement({ reconnect: value.reconnect !== false }));
    }
    if (req.url === '/test/restart-state') {
      const hub = JSON.parse(readFileSync(join(replacement.runtime, 'deployment', 'hub.json'), 'utf8'));
      return json(res, { ...readActivity(replacement.runtime), hub,
        pid: replacement.child.pid, guard: replacement.guard, gateway_url: receiverOrigin });
    }
    if (req.url === '/test/restart-drain') {
      const value = await body(req);
      const file = join(replacement.runtime, 'deployment', 'drain.json');
      if (value.enabled) writeFileSync(file, JSON.stringify({ version: 1, id: 'synthetic-recovered-drain',
        ownerPid: process.pid, expiresAt: Date.now() + 90000 }));
      else { try { unlinkSync(file); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
      return json(res, {});
    }
    if (req.url === '/test/restart-guard') {
      if (replacement.guard) return json(res, { error: 'restart_guard_already_started' }, 409);
      const file = join(replacement.runtime, 'deployment', 'drain.json');
      const leaseId = 'synthetic-recovered-quiet-minute';
      replacement.guard = { started_at_ms: Date.now(), claimed_at_ms: null, settled_at_ms: null, error: null };
      const release = () => {
        try { unlinkSync(file); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      };
      void waitForQuiescence({ read: () => readActivity(replacement.runtime), report: () => {}, timeoutMs: 180000,
        claim: () => {
          replacement.guard.claimed_at_ms = Date.now();
          writeFileSync(file, JSON.stringify({ version: 1, id: leaseId, ownerPid: process.pid,
            expiresAt: Date.now() + 15000 }));
          return leaseId;
        }, release,
      }).then(() => { replacement.guard.settled_at_ms = Date.now(); release(); },
        error => { replacement.guard.error = error.message; });
      return json(res, replacement.guard);
    }
    if (req.url === '/test/recovery') {
      const file = join(root, `recovered-${recovered.length}.json`);
      writeFileSync(file, readFileSync(join(root, 'account.json')));
      const broker = new HomeAssistantBroker(new Store(file), { robotAdapter });
      const server = https.createServer({ key: readFileSync(key), cert: readFileSync(certificate) },
        (_request, response) => json(response, {}, 404));
      server.on('upgrade', (...args) => broker.upgrade(...args));
      await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
      recovered.push({ broker, server });
      return json(res, { url: `https://127.0.0.1:${server.address().port}` });
    }
    if (req.url === '/test/drain') {
      const value = await body(req);
      const file = join(process.env.PHOENIX_RUNTIME_DIR, 'deployment', 'drain.json');
      if (value.enabled) writeFileSync(file, JSON.stringify({ version: 1, id: 'synthetic-drain',
        ownerPid: process.pid, expiresAt: Date.now() + 15000 }));
      else { try { unlinkSync(file); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
      return json(res, {});
    }
    if (req.url === '/test/guard') {
      if (req.method === 'POST') {
        if (guard) return json(res, { error: 'guard_already_started' }, 409);
        const file = join(process.env.PHOENIX_RUNTIME_DIR, 'deployment', 'drain.json');
        const leaseId = 'synthetic-observed-quiet-minute';
        guard = { started_at_ms: Date.now(), claimed_at_ms: null, settled_at_ms: null, error: null };
        const release = () => {
          try { unlinkSync(file); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        };
        void waitForQuiescence({ read: () => readActivity(process.env.PHOENIX_RUNTIME_DIR),
          report: () => {}, timeoutMs: 90000,
          claim: () => {
            guard.claimed_at_ms = Date.now();
            writeFileSync(file, JSON.stringify({ version: 1, id: leaseId, ownerPid: process.pid,
              expiresAt: Date.now() + 15000 }));
            return leaseId;
          }, release,
        }).then(() => { guard.settled_at_ms = Date.now(); release(); }, error => { guard.error = error.message; });
      }
      return json(res, guard || {});
    }
    const upstream = http.request(accountUrl + req.url, { method: req.method, headers: req.headers }, response => {
      res.writeHead(response.statusCode, response.headers); response.pipe(res);
    });
    upstream.on('error', () => { res.writeHead(502); res.end(); }); req.pipe(upstream);
  } catch (error) { json(res, { error: error.message }, 500); }
});
edge.on('upgrade', (...args) => account.homeAssistant.upgrade(...args));
await new Promise(resolveListen => edge.listen(0, '127.0.0.1', resolveListen));
const code = account.homeAssistant.issueCode(owner, robots.map(robot => robot.friendlyId)).code;
console.log(JSON.stringify({ url: `https://127.0.0.1:${edge.address().port}`, certificate, code,
  owner_cookie: `phx_session=${session._id}`, stranger_cookie: `phx_session=${otherSession._id}`,
  gateway_url: gatewayUrl, runtime_dir: process.env.PHOENIX_RUNTIME_DIR,
  robots: robots.map(robot => ({ name: robot.name, friendly_id: robot.friendlyId,
    identity: { id: robot._id, accessKeyId: robot.accessKeyId, friendlyId: robot.friendlyId },
    robot_token: robotToken(robot) })), robot_token: robotToken(robots[0]) }));
process.on('SIGTERM', () => {
  account.homeAssistant.close(); ota.stop(); clearInterval(receiverTimer);
  replacement?.ota.stop(); replacement?.child.kill('SIGKILL');
  verifyGate?.release();
  gateway.robotActions.close();
  for (const item of recovered) { item.broker.close(); item.server.close(); }
  for (const receiver of receivers.values()) receiver.terminate();
  for (const socket of gateway.wss.clients) socket.terminate();
  edge.close(); parser.server.close(); account.server.close(); gateway.service.server.close();
  setTimeout(() => process.exit(0), 100).unref();
});
