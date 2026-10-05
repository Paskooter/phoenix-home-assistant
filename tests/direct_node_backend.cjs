'use strict';

// Fixture IPC only. Native source stays private and is never copied into CI.
var fs = require('fs');
var path = require('path');
var crypto = require('crypto');
var readline = require('readline');
var modulePath = process.env.PHOENIX_DIRECT_MODULE;
if (!modulePath || !path.isAbsolute(modulePath)) throw new Error('Private module path required');
var native = require(modulePath);
var directory = process.argv[3];

if (process.argv[2] === '--seed') {
    native.generateIdentity(crypto.webcrypto.subtle, Date.now()).then(function (identity) {
        native.secureStore(directory).save(identity, true);
        process.stdout.write(JSON.stringify({ seeded: true }) + '\n');
    }).catch(function (error) { process.stderr.write(String(error.code || error) + '\n'); process.exitCode = 1; });
} else {
    var pairingEvent = null;
    var gates = new Set();
    var speaks = [];
    var speechResult = 'SUCCEEDED';
    var telemetry = null;
    var busy = false;
    var runtime = {
        isBusy: function () { return busy; },
        consumeHomeGate: function (gate) { if (!gates.has(gate)) return false; gates.delete(gate); return true; },
        speak: function (text) { speaks.push(text); return Promise.resolve(speechResult); },
        stop: function () { return Promise.resolve('STOPPED'); },
        onInterrupt: function () { return function () {}; },
        getTelemetry: function () { return telemetry; }
    };
    var server = new native.LocalHomeServer({ directory: directory, runtime: runtime,
        host: '127.0.0.1', port: 0, name: 'Invented native Jibo', firmwareVersion: '13.2.0',
        getOwnerBinding: function () { return crypto.createHash('sha256').update('invented-owner').digest('hex'); },
        onPairing: function (event) { if (event.phase === 'revealed') pairingEvent = event; } });
    function status() {
        return { ready: !!(server.session && server.session.ready), generation: server.state.generation,
            paired: !!server.state.credential_hash, direct_enabled: server.state.direct_enabled,
            sas: pairingEvent && pairingEvent.sas, speaks: speaks, routing: server.routingPreference() };
    }
    function respond(id, result, error) {
        process.stdout.write(JSON.stringify({ id: id, result: result, error: error }) + '\n');
    }
    function operation(request) {
        switch (request.op) {
        case 'open': server.openPairing(); pairingEvent = null; return true;
        case 'approve': return server.approveCandidate(pairingEvent && pairingEvent.pair_id);
        case 'status': return status();
        case 'speak_result': speechResult = request.value; return true;
        case 'busy': busy = request.value === true; server.broadcastRoster(); return true;
        case 'command':
            var gate = {}; if (request.wake !== false) gates.add(gate);
            return server.dispatchHome({ text: request.text, originalText: request.text,
                explicit: request.explicit === true, route: request.route || { kind: 'command' }, gate: gate });
        case 'telemetry':
            telemetry = { values: request.values, observed_at_monotonic_ms: server.monotonic() - (request.age_ms || 0) };
            server.broadcastTelemetry(telemetry); return true;
        case 'disconnect':
            if (server.session) server.session.socket.terminate(); return true;
        case 'revoke': server.revoke(); return true;
        case 'close': return server.destroy().then(function () { return true; });
        default: throw new Error('Unknown fixture operation');
        }
    }
    server.init().then(function () { return server.start(); }).then(function () {
        process.stdout.write(JSON.stringify({ host: '127.0.0.1', port: server.server.address().port,
            robot_id: server.state.endpoint_id, fingerprint: server.state.certificate_sha256,
            node_version: process.version, module_sha256: crypto.createHash('sha256').update(fs.readFileSync(modulePath)).digest('hex') }) + '\n');
        var input = readline.createInterface({ input: process.stdin });
        input.on('line', function (line) {
            var request;
            try { request = JSON.parse(line); }
            catch (_) { process.exitCode = 1; return; }
            Promise.resolve().then(function () { return operation(request); }).then(function (result) {
                respond(request.id, result); if (request.op === 'close') { input.close(); process.stdin.destroy(); }
            }, function (error) { respond(request.id, null, String(error.code || error.message)); });
        });
        input.on('close', function () { server.destroy(); });
    }).catch(function (error) { process.stderr.write(String(error.code || error.stack) + '\n'); process.exitCode = 1; });
}
