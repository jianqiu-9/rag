/**
 * 在本机 Chrome 中用 Mermaid 官方解析器检查文档图，不依赖外部在线编辑器。
 * 同时检查本仓库 Markdown 相对链接，避免重命名文档后留下失效导航。
 */
import { readdir, readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const files = [
  path.join(root, 'README.md'),
  ...(await readdir(path.join(root, 'doc')))
    .filter((name) => name.endsWith('.md'))
    .map((name) => path.join(root, 'doc', name)),
];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
let count = 0;
try {
  const page = await browser.newPage();
  await page.goto('about:blank');
  await page.addScriptTag({
    path: path.join(path.dirname(require.resolve('mermaid')), 'mermaid.min.js'),
  });
  await page.evaluate(() =>
    window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' }),
  );
  for (const file of files) {
    const content = await readFile(file, 'utf8');
    const diagrams = [...content.matchAll(/```mermaid\s*\n([\s\S]*?)```/g)];
    for (const [index, match] of diagrams.entries()) {
      try {
        await page.evaluate((code) => window.mermaid.parse(code), match[1]);
        count += 1;
      } catch (error) {
        throw new Error(`${path.relative(root, file)} 第 ${index + 1} 个图无效：${error.message}`);
      }
    }
    for (const match of content.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
      const target = match[1];
      if (/^(https?:|mailto:|#)/.test(target)) continue;
      const resolved = path.resolve(path.dirname(file), target.split('#')[0]);
      await readFile(resolved).catch(() => {
        throw new Error(`${file} 包含无效链接：${target}`);
      });
    }
  }
  console.log(`文档校验通过：${files.length} 份 Markdown，${count} 个 Mermaid 图。`);
} finally {
  await browser.close();
}
