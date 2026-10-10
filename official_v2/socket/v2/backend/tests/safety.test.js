// 离线回归：全部 WebSocket 是内存对象，Jest 使用假时钟，不启动服务器。
const assert = require('node:assert/strict');

jest.mock('../src/logger', () => ({ info() {}, debug() {}, warn() {}, error() {} }));

let timers;

function socket() {
  return {
    readyState: 1,
    messages: [],
    events: [],
    send(raw) {
      const message = JSON.parse(raw);
      this.messages.push(message);
      this.events.push(message.message);
    },
    close() {
      this.events.push('closed');
      this.readyState = 3;
    }
  };
}

function pulse(message) {
  return { type: 'msg', clientId: 'web', targetId: 'app', message };
}

function queue(target, source, name, count = 3) {
  timers.sendMessage('web', 'A', target, pulse(name), count, 1000, source);
}

function routerFor(target) {
  const router = require('../src/message');
  router.connectionManager = {
    isPaired: (client, app) => client === 'web' && app === 'app',
    getClient: id => id === 'app' ? { ws: target } : null
  };
  return router;
}

beforeEach(() => {
  jest.resetModules();
  jest.useFakeTimers();
  timers = require('../src/timer');
});

afterEach(() => {
  timers.cleanupAll();
  jest.clearAllTimers();
  jest.restoreAllMocks();
  jest.useRealTimers();
});

test('clear cancels a pending replacement, including an already queued callback', () => {
  const target = socket(), source = socket();
  const timeout = jest.spyOn(global, 'setTimeout');
  queue(target, source, 'old');
  queue(target, source, 'replacement');
  const lateCallback = timeout.mock.calls[0][0];
  assert.equal(timers.timers.size, 1);
  timers.clearTimer('web', 'A', source);
  const count = target.messages.length;
  lateCallback();
  jest.advanceTimersByTime(5000);
  assert.equal(target.messages.length, count);
  assert.equal(timers.timers.size, 0);
});

test('rapid replacement cannot resurrect the middle waveform', () => {
  const target = socket(), source = socket();
  const timeout = jest.spyOn(global, 'setTimeout');
  queue(target, source, 'first');
  queue(target, source, 'second');
  const secondCallback = timeout.mock.calls[0][0];
  queue(target, source, 'third');
  secondCallback();
  jest.advanceTimersByTime(150);
  assert.equal(target.messages.filter(m => m.message === 'second').length, 0);
  assert.equal(target.messages.filter(m => m.message === 'third').length, 1);
  assert.equal(timers.timers.get('web-A').message.message, 'third');
});

test('late interval cannot delete the new task or keep sending old data', () => {
  const target = socket(), source = socket();
  const intervals = jest.spyOn(global, 'setInterval');
  queue(target, source, 'old');
  const oldInterval = intervals.mock.calls[0][0];
  queue(target, source, 'new');
  const pending = timers.timers.get('web-A');
  oldInterval();
  assert.equal(timers.timers.get('web-A'), pending);
  jest.advanceTimersByTime(150);
  const current = timers.timers.get('web-A');
  oldInterval();
  assert.equal(timers.timers.get('web-A'), current);
  assert.equal(target.messages.filter(m => m.message === 'old').length, 1);
});

test.each(['web', 'app'])('disconnect of %s cancels active and pending tasks', id => {
  const target = socket(), source = socket();
  queue(target, source, 'old');
  queue(target, source, 'new');
  timers.clearClientTimers(id);
  const count = target.messages.length;
  assert.equal(target.messages.at(-1).message, 'strength-1+2+0');
  jest.advanceTimersByTime(5000);
  assert.equal(target.messages.length, count);
  assert.equal(timers.timers.size, 0);
});

test('shutdown cancels pending starts and zeroes their target channel', () => {
  const target = socket(), source = socket();
  queue(target, source, 'old');
  queue(target, source, 'new');
  timers.cleanupAll();
  const count = target.messages.length;
  assert.equal(target.messages.at(-1).message, 'strength-1+2+0');
  jest.advanceTimersByTime(5000);
  assert.equal(target.messages.length, count);
});

test('type 4 clear cancels the backend task rather than only clearing the APP', () => {
  const target = socket(), source = socket();
  queue(target, source, 'old');
  queue(target, source, 'new');
  const result = routerFor(target).handleCustomStrength({
    type: 4, clientId: 'web', targetId: 'app', message: 'clear-1'
  }, source, timers);
  assert.equal(result.success, true);
  const count = target.messages.length;
  jest.advanceTimersByTime(5000);
  assert.equal(target.messages.length, count);
  assert.equal(timers.timers.size, 0);
});

test('type 4 native pulse forwards the original protocol string exactly once', () => {
  const target = socket(), source = socket();
  const message = 'pulse-A:["0A0A0A0A00000000","0A0A0A0A14141414"]';
  const result = routerFor(target).handleCustomStrength({
    type: 4, clientId: 'web', targetId: 'app', message
  }, source, timers);
  assert.equal(result.success, true);
  assert.deepEqual(target.messages, [{ type: 'msg', clientId: 'web', targetId: 'app', message }]);
  assert.equal(timers.timers.size, 0);
  jest.advanceTimersByTime(5000);
  assert.equal(target.messages.length, 1);
});

test.each(['null', '[]', '1'])('non-object JSON %s is rejected without throwing', raw => {
  assert.equal(routerFor(socket()).validate(raw).valid, false);
});

test.each([0, -1, 'NaN', 'Infinity'])('invalid pulse duration %s cannot create a task', time => {
  const target = socket();
  const result = routerFor(target).handleClientMessage({
    type: 'clientMsg', clientId: 'web', targetId: 'app', channel: 'A', message: 'A:[]', time
  }, socket(), timers);
  assert.equal(result.success, false);
  assert.equal(timers.timers.size, 0);
  assert.equal(target.messages.length, 0);
});

test('disconnect zeros both APP channels before the break and close messages', () => {
  const connection = require('../src/connection');
  const web = socket(), app = socket();
  connection.register(web, 'web');
  connection.register(app, 'app');
  connection.pair('web', 'app');
  connection.disconnect('web');
  assert.deepEqual(app.events, [
    'clear-1', 'strength-1+2+0', 'clear-2', 'strength-2+2+0', '209', 'closed'
  ]);
});
