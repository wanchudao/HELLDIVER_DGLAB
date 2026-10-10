// Private parent-child control channel; never accepts shutdown from WebSocket.
const { gracefulShutdown } = require('./index');
const readline = require('readline');
let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  gracefulShutdown('desktop launcher');
  // A broken peer must not leave the owned backend orphaned.
  setTimeout(() => process.exit(0), 3000).unref();
}
const input = readline.createInterface({ input: process.stdin });
input.on('line', (line) => { if (line === 'STOP') stop(); });
input.on('close', stop);
