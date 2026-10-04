// An actual disposable Gateway process for the native-execution restart test.
// Credentials, peers and runtime directory belong only to invented fixtures.
import fs, { readFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

if (!process.env.PHOENIX_SERVER_DIR || !process.env.PHOENIX_HA_RESTART_CONFIG || !process.env.PHOENIX_RUNTIME_DIR) {
  throw new Error('Explicit isolated source, configuration and runtime are required');
}
const load = relative => import(pathToFileURL(join(process.env.PHOENIX_SERVER_DIR, relative)).href);
const startupActivityCounts = [];
const hubFilePrefix = join(process.env.PHOENIX_RUNTIME_DIR, 'deployment', 'hub.json') + '.';
const originalWrite = fs.writeFileSync;
fs.writeFileSync = (file, data, ...options) => {
  // Observe successfully written real reports; forward bytes/options unchanged.
  const result = originalWrite(file, data, ...options);
  if (typeof file === 'string' && file.startsWith(hubFilePrefix)) {
    const report = JSON.parse(String(data));
    startupActivityCounts.push(Object.values(report.active).reduce((sum, count) => sum + count, 0));
  }
  return result;
};
syncBuiltinESMExports();
const [{ createGateway }, { loadConfig }] = await Promise.all([
  load('packages/gateway/src/index.js'), load('packages/gateway/src/config.js'),
]);
const fixture = JSON.parse(readFileSync(process.env.PHOENIX_HA_RESTART_CONFIG, 'utf8'));
const config = await loadConfig({
  ETCO_hub_accountUrl: fixture.accountUrl,
  ETCO_account_internalPeerToken: 'synthetic-next-peer',
  ETCO_server_hubTokenSecret: 'synthetic-next-hub',
  NET_parser: fixture.parser,
  NET_skills: '127.0.0.1:1',
  ETCO_hub_recordLaunchHistory: 'false',
});
const gateway = await createGateway(config);
fs.writeFileSync = originalWrite;
syncBuiltinESMExports();
await gateway.service.listen(fixture.port || 0, '127.0.0.1');
console.log(JSON.stringify({
  test_gateway_started: true,
  gateway_url: `http://127.0.0.1:${gateway.service.server.address().port}`,
  pid: process.pid,
  startup_activity_counts: startupActivityCounts,
}));
process.on('SIGTERM', () => {
  gateway.robotActions.close();
  for (const socket of gateway.wss.clients) socket.terminate();
  gateway.service.server.close();
  setTimeout(() => process.exit(0), 100).unref();
});
