import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const parserDir = path.dirname(fileURLToPath(new URL('./telegram-parser/pyproject.toml', import.meta.url)));

export function startTelegramParser() {
  if (process.env.TELEGRAM_PARSER_ENABLED !== 'true') return;

  if (!process.env.TELEGRAM_BOT_TOKEN || !process.env.TELEGRAM_CHAT_ID || !path.isAbsolute(process.env.DATABASE_PATH || '')) {
    console.error('Telegram parser needs TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and an absolute DATABASE_PATH.');
    return;
  }

  const python = process.env.TELEGRAM_PARSER_PYTHON || 'python3';
  const child = spawn(python, ['-m', 'src.main'], {
    cwd: parserDir,
    env: process.env,
    stdio: 'inherit',
  });

  child.on('error', (error) => console.error('Telegram parser could not start:', error));
  child.on('exit', (code, signal) => console.error(`Telegram parser exited (code=${code}, signal=${signal}).`));

  const stop = () => {
    if (!child.killed) child.kill('SIGTERM');
    process.exit(0);
  };
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
}
