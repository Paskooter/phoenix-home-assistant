'use strict';

// Synthetic SDK resources for the optional real Node 6 endpoint release gate.
// No native source, household identifiers, media captures or credentials here.
var EventEmitter = require('events');
module.exports = function (modulePath, runtime, server, telemetry) {
    var native = require(require('path').join(require('path').dirname(modulePath), 'phoenix-local-robot-controls'));
    function Signal() { this.listeners = new Set(); }
    Signal.prototype.on = function (fn) { this.listeners.add(fn); };
    Signal.prototype.off = function (fn) { this.listeners.delete(fn); };
    Signal.prototype.emit = function () { this.listeners.forEach(function (fn) { fn(); }); };
    var eye = { _type: 'EyeView' }, volume = 0.5, asleep = false;
    var calls = { views: 0, rings: 0, sounds: 0, photos: 0, skills: 0 };
    var views = {
        currentView: eye, viewsInProcess: false,
        createView: function (type, config) { calls.views++; return { _type: type, config: config, id: config.viewConfig.id }; },
        changeView: function (options, done) { views.currentView = options.addView; Promise.resolve().then(done); },
        removeView: function (done) { views.currentView = eye; Promise.resolve().then(done); }
    };
    var idle = { assetPack: '@be/idle', circadianManager: {
        goToSleepHandler: function () { asleep = true; }, heyJiboHandler: function () { asleep = false; },
        getCurrentCircadianState: function () { return asleep ? 'ASLEEP' : 'ALERT'; }
    } };
    function Be() {
        this.skills = { '@be/idle': idle, '@be/clock': { assetPack: '@be/clock' } };
        this._skillSwitchScheduler = { _destroyed: false, _pendingSkillLifecycle: null, _pendingSkillRedirectToken: null,
            currentSkillRedirectToken: { skillSwitchData: { skill: idle } } };
        Object.defineProperty(this, 'currentSkill', { get: function () { return this._skillSwitchScheduler.currentSkillRedirectToken.skillSwitchData.skill; } });
    }
    Be.SkillSwitchData = function (skill, options) { this.skill = skill; this.options = options; };
    Be.SkillLifecycleState = { SKILL_OPENED: 4, LIFECYCLE_ENDED: 5 };
    Be.prototype.redirect = function (data) {
        calls.skills++;
        var scheduler = this._skillSwitchScheduler, ended = [], opened = [];
        var token = { skillSwitchData: data,
            onState: function (_, fn) { opened.push(fn); },
            addOnSkillLifecycleEnd: function (fn) { ended.push(fn); }, ended: function () { ended.forEach(function (fn) { fn(); }); } };
        var prior = scheduler.currentSkillRedirectToken;
        scheduler._pendingSkillRedirectToken = token; scheduler._pendingSkillLifecycle = {};
        Promise.resolve().then(function () {
            scheduler.currentSkillRedirectToken = token; scheduler._pendingSkillRedirectToken = null; scheduler._pendingSkillLifecycle = null;
            if (prior.ended) prior.ended(); opened.forEach(function (fn) { fn(); });
        });
        return token;
    };
    Be.prototype.exit = function () { this.redirect(new Be.SkillSwitchData(idle, {})); };
    var signals = { touchOn: new Signal(), hatchOpen: new Signal() };
    var jibo = { face: { views: views }, loader: { addCache: function () {}, deleteCache: function () {} }, system: { events: signals,
        getMasterVolume: function (cb) { cb(null, volume); }, setMasterVolume: function (value, cb) { volume = value; cb(null); } },
        sound: { add: function (_, options) {
            calls.sounds++;
            var sound = { play: function () { return { paused: false, stop: function () {} }; } };
            Promise.resolve().then(function () { options.loaded(null, sound); }); return sound;
        }, remove: function () {} },
        expression: { dofs: { LED: {} }, createAnimation: function () {
            calls.rings++; var started = new Signal(), finish;
            return Promise.resolve({ events: { started: started, rejected: new Signal(), cancelled: new Signal() },
                play: function () { Promise.resolve().then(function () { started.emit(); }); return new Promise(function (resolve) { finish = resolve; }); },
                stop: function () { if (finish) finish(); return Promise.resolve(); }, destroy: function () {} });
        }, destroyCaches: function () { return Promise.resolve(); } },
        media: { takePhoto: function (_, cb) { calls.photos++; cb(null, { id: 'invented-preview' }); },
            getPreviewUrl: function () { return 'http://127.0.0.1:12345/media/photo?id=invented-preview'; } },
        records: [{ name: 'media', host: '127.0.0.1', port: 12345 }] };
    var adapter = native.create({ jibo: jibo, be: new Be(), speech: runtime,
        getTelemetry: function () { return telemetry(); },
        resolveMedia: function (id) { return server().controlBroker.resolveMedia(id); },
        onChange: function () { if (server()) server().controlBroker.broadcastState(true); },
        httpGet: function (_, done) {
            var request = new EventEmitter(); request.abort = function () {};
            var response = new EventEmitter(); response.statusCode = 200; response.headers = { 'content-type': 'image/jpeg' };
            Promise.resolve().then(function () {
                done(response); response.emit('data', Buffer.from([255,216,255,192,0,11,8,0,1,0,1,1,1,17,0,255,218,0,8,1,1,0,0,63,0,0,255,217])); response.emit('end');
            }); return request;
        } });
    adapter.start();
    return { adapter: adapter, calls: calls, touch: function () { signals.touchOn.emit(); } };
};
